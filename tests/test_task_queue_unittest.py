# -*- coding: utf-8 -*-
"""Celery 重投递所依赖的 SQL 租约、消息幂等与终态原子性。"""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from backend.agents.memory import claim_agent_task, save_agent_message
from backend.agents.task_queue import (
    _dispatch_agent_task,
    execute_agent_task,
    recover_stale_agent_tasks_periodically,
)
from backend.celery_app import celery_app
from backend.config import AGENT_REDIS_KEY_PREFIX
from backend.db.models import AgentMessage, AgentSession, AgentTask, Base, User
from backend.db.session import get_sync_engine, session_scope


class AgentTaskDeliveryTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(prefix="agent_delivery_", suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.engine = get_sync_engine(db_path=str(self.path))
        Base.metadata.create_all(self.engine)
        with session_scope(db_path=str(self.path)) as session:
            user = User(username="delivery_user", password_hash="test")
            session.add(user)
            session.flush()
            conversation = AgentSession(
                user_id=user.id,
                agent_type="recommendation",
                title="delivery test",
            )
            session.add(conversation)
            session.flush()
            task = AgentTask(
                user_id=user.id,
                session_id=conversation.id,
                agent_type="recommendation",
                input_data={"query": "test", "history": []},
                status="queued",
                progress=0,
                current_step="queued",
            )
            session.add(task)
            session.flush()
            self.user_id = user.id
            self.session_id = conversation.id
            self.task_id = task.id

    def tearDown(self):
        self.engine.dispose()
        self.path.unlink(missing_ok=True)

    @contextmanager
    def _session(self):
        with session_scope(db_path=str(self.path)) as session:
            yield session

    def test_concurrent_claim_allows_only_one_worker(self):
        def claim(worker):
            return claim_agent_task(self.task_id, worker, 120)["claim_state"]

        with patch("backend.agents.memory.orm_session", side_effect=self._session):
            with ThreadPoolExecutor(max_workers=2) as executor:
                states = list(executor.map(claim, ("worker-a", "worker-b")))
        self.assertEqual(states.count("claimed"), 1)
        self.assertEqual(states.count("busy"), 1)

        with session_scope(db_path=str(self.path)) as session:
            task = session.get(AgentTask, self.task_id)
            task.lease_until = datetime.now() - timedelta(seconds=1)
        with patch("backend.agents.memory.orm_session", side_effect=self._session):
            reclaimed = claim_agent_task(self.task_id, "worker-c", 120)
        self.assertEqual(reclaimed["claim_state"], "claimed")
        self.assertEqual(reclaimed["task"]["attempt_count"], 2)

    def test_task_message_and_success_are_atomic_and_idempotent(self):
        first_payload = {"answer": "first", "session_id": self.session_id}
        with patch("backend.agents.memory.orm_session", side_effect=self._session):
            first_id = save_agent_message(
                self.session_id,
                "agent",
                "first",
                first_payload,
                source_task_id=self.task_id,
                task_outcome="succeeded",
            )
            second_id = save_agent_message(
                self.session_id,
                "agent",
                "must not duplicate",
                {"answer": "second"},
                source_task_id=self.task_id,
                task_outcome="succeeded",
            )

        self.assertEqual(second_id, first_id)
        with session_scope(db_path=str(self.path)) as session:
            task = session.get(AgentTask, self.task_id)
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(task.result["answer"], "first")
            self.assertEqual(
                session.scalar(
                    select(func.count(AgentMessage.id)).where(
                        AgentMessage.source_task_id == self.task_id
                    )
                ),
                1,
            )

    def test_redelivery_of_terminal_task_does_not_dispatch_again(self):
        payload = {"answer": "completed", "session_id": self.session_id}

        def complete(_task):
            save_agent_message(
                self.session_id,
                "agent",
                payload["answer"],
                payload,
                source_task_id=self.task_id,
                task_outcome="succeeded",
            )
            return payload

        with (
            patch("backend.agents.memory.orm_session", side_effect=self._session),
            patch("backend.agents.task_queue._dispatch_agent_task", side_effect=complete) as dispatch,
            patch("backend.agents.task_queue.AgentStreamEmitter.emit", return_value=False),
        ):
            first = execute_agent_task.run(self.task_id)
            second = execute_agent_task.run(self.task_id)

        self.assertEqual(first["answer"], "completed")
        self.assertEqual(second["answer"], "completed")
        dispatch.assert_called_once()

    def test_periodic_recovery_task_uses_sql_recovery_function(self):
        with patch(
            "backend.agents.task_queue.recover_stale_agent_tasks",
            return_value=2,
        ) as recover:
            self.assertEqual(recover_stale_agent_tasks_periodically.run(), 2)
        recover.assert_called_once_with()

    def test_recommendation_dispatch_maintains_memory_after_saved_answer(self):
        task = {
            "id": 91,
            "user_id": 7,
            "session_id": 8,
            "agent_type": "recommendation",
            "attempt_count": 1,
            "input": {
                "message": "我一直喜欢日常番",
                "user_message_id": 77,
                "history": [],
            },
        }
        payload = {"session_id": 8, "answer": "已记录。"}
        with (
            patch(
                "backend.api.agent._run_recommendation_task",
                return_value=payload,
            ) as run_task,
            patch(
                "backend.agents.context_memory.maintain_context_memory",
                return_value={"status": "updated"},
            ) as maintain,
        ):
            result = _dispatch_agent_task(task)

        self.assertEqual(result, payload)
        run_task.assert_called_once()
        maintain.assert_called_once_with(7, 8, 77, payload)

    def test_recommendation_task_stays_running_until_memory_finishes(self):
        payload = {"session_id": self.session_id, "answer": "已记录。"}

        def run_task(task_id, *_args, **_kwargs):
            save_agent_message(
                self.session_id,
                "agent",
                payload["answer"],
                payload,
                source_task_id=task_id,
            )
            return payload

        def maintain(*_args):
            with session_scope(db_path=str(self.path)) as session:
                task = session.get(AgentTask, self.task_id)
                message = session.scalar(
                    select(AgentMessage).where(
                        AgentMessage.source_task_id == self.task_id
                    )
                )
                self.assertEqual(task.status, "running")
                self.assertIsNotNone(message)
            return {"status": "updated"}

        with session_scope(db_path=str(self.path)) as session:
            task = session.get(AgentTask, self.task_id)
            task.input_data = {
                "message": "我一直喜欢日常番",
                "user_message_id": 77,
                "history": [],
            }
        with (
            patch("backend.agents.memory.orm_session", side_effect=self._session),
            patch("backend.api.agent._run_recommendation_task", side_effect=run_task),
            patch(
                "backend.agents.context_memory.maintain_context_memory",
                side_effect=maintain,
            ),
            patch("backend.agents.task_queue.AgentStreamEmitter.emit", return_value=True) as emit,
        ):
            result = execute_agent_task.run(self.task_id)

        self.assertEqual(result, payload)
        with session_scope(db_path=str(self.path)) as session:
            task = session.get(AgentTask, self.task_id)
            self.assertEqual(task.status, "succeeded")
        event_names = [call.args[0] for call in emit.call_args_list]
        self.assertLess(event_names.index("result_ready"), event_names.index("task_completed"))

    def test_redelivery_with_saved_answer_repairs_memory_before_success(self):
        payload = {"session_id": self.session_id, "answer": "已保存回答。"}
        with session_scope(db_path=str(self.path)) as session:
            task = session.get(AgentTask, self.task_id)
            task.input_data = {
                "message": "我一直喜欢日常番",
                "user_message_id": 88,
                "history": [],
            }
            session.add(
                AgentMessage(
                    session_id=self.session_id,
                    role="agent",
                    content=payload["answer"],
                    message_metadata=payload,
                    source_task_id=self.task_id,
                )
            )

        with (
            patch("backend.agents.memory.orm_session", side_effect=self._session),
            patch(
                "backend.agents.context_memory.maintain_context_memory",
                return_value={"status": "updated"},
            ) as maintain,
            patch("backend.agents.task_queue.AgentStreamEmitter.emit", return_value=True),
        ):
            result = execute_agent_task.run(self.task_id)

        self.assertEqual(result, payload)
        maintain.assert_called_once_with(
            self.user_id,
            self.session_id,
            88,
            payload,
        )
        with session_scope(db_path=str(self.path)) as session:
            task = session.get(AgentTask, self.task_id)
            count = session.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.source_task_id == self.task_id
                )
            )
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(count, 1)

    def test_cloud_safe_celery_defaults_use_prefix_and_periodic_recovery(self):
        self.assertTrue(celery_app.conf.task_ignore_result)
        self.assertEqual(
            celery_app.conf.task_routes["backend.agents.recover_stale_agent_tasks"]["queue"],
            "agent.control",
        )
        schedule = celery_app.conf.beat_schedule["recover-stale-agent-tasks"]
        self.assertEqual(schedule["task"], "backend.agents.recover_stale_agent_tasks")
        self.assertEqual(schedule["options"]["queue"], "agent.control")
        broker_options = celery_app.conf.broker_transport_options
        if AGENT_REDIS_KEY_PREFIX:
            self.assertTrue(broker_options["global_keyprefix"].endswith(":broker:"))
        else:
            self.assertNotIn("global_keyprefix", broker_options)


if __name__ == "__main__":
    unittest.main()
