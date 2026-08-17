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
