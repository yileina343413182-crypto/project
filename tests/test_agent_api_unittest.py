# -*- coding: utf-8 -*-
"""Smoke tests for the independent async Agent Center API."""

import os
import threading
import time
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

os.environ["LLM_API_KEY"] = ""

from tests.api_test_support import open_test_celery_worker, open_test_client
from backend.database import orm_session
from backend.db.models import AgentMessage, AgentSession, AgentTask, WatchGuide


def _offline_recommendation(*_args, **_kwargs):
    return {
        "result": {
            "need_clarification": False,
            "clarifying_question": "",
            "recommendations": [],
        },
        "agent_steps": [],
        "fallback": True,
    }


def _offline_followup(query, *_args, **_kwargs):
    return {
        "response_mode": "conversation",
        "answer": f"这是关于“{query}”的离线详细测试回答。",
        "fallback": False,
        "prompt_trace": {},
    }


def _offline_guide(*_args, **_kwargs):
    return {
        "content": "**📺 观看前，你需要知道的**\n测试档案。\n\n**📅 分阶段观看计划**\n测试计划。",
        "fallback": False,
        "prompt_trace": {},
    }


class AgentApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context, cls.client = open_test_client()
        cls.agent_patchers = (
            patch("backend.api.agent.run_recommendation_agent", side_effect=_offline_recommendation),
            patch("backend.api.agent.run_recommendation_followup", side_effect=_offline_followup),
            patch("backend.api.agent.generate_watch_guide", side_effect=_offline_guide),
        )
        for patcher in cls.agent_patchers:
            patcher.start()
        cls.worker_context = open_test_celery_worker()
        cls.worker_context.__enter__()
        username = f"agt{time.time_ns() % 100000000}"
        resp = cls.client.post("/api/auth/register", json={"username": username, "password": "password123"})
        payload = resp.json()
        cls.token = payload["data"]["token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}
        cls.user_id = payload["data"]["user_id"]

    @classmethod
    def tearDownClass(cls):
        cls.worker_context.__exit__(None, None, None)
        cls.client_context.__exit__(None, None, None)
        for patcher in reversed(cls.agent_patchers):
            patcher.stop()

    def wait_for_task(self, task_id, headers=None, timeout=15):
        deadline = time.time() + timeout
        last_payload = None
        while time.time() < deadline:
            resp = self.client.get(f"/api/agent/tasks/{task_id}", headers=headers or self.headers)
            self.assertEqual(resp.status_code, 200)
            last_payload = resp.json()["data"]
            if last_payload["status"] in ("succeeded", "failed"):
                return last_payload
            time.sleep(0.2)
        self.fail(f"task {task_id} did not finish in time; last={last_payload}")

    def create_completed_recommendation_session(self, anime_name):
        with orm_session() as session:
            record = AgentSession(
                user_id=self.user_id,
                agent_type="recommendation",
                title=f"测试 {anime_name}",
            )
            session.add(record)
            session.flush()
            session.add(
                AgentMessage(
                    session_id=record.id,
                    role="agent",
                    content="推荐结果已生成",
                    message_metadata={
                        "result": {
                            "need_clarification": False,
                            "recommendations": [
                                {
                                    "anime_id": None,
                                    "name": anime_name,
                                    "platform": "test",
                                    "reason": "用于验证多轮追问与观看指南保存。",
                                    "match_tags": ["测试"],
                                    "evidence": {},
                                }
                            ],
                        }
                    },
                )
            )
            return record.id

    def test_agent_sessions_empty_shape(self):
        resp = self.client.get("/api/agent/sessions", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["code"], 200)
        self.assertIsInstance(payload["data"], list)

    def test_recommendation_agent_creates_async_task_and_result(self):
        resp = self.client.post(
            "/api/agent/recommend/start",
            headers=self.headers,
            json={"query": "warm healing anime with solid public opinion"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("session_id", data)
        self.assertIn("task_id", data)
        self.assertIn(data["status"], ("queued", "running"))

        task = self.wait_for_task(data["task_id"])
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["progress"], 100)
        self.assertIn("result", task)
        self.assertIn("result", task["result"])

    def test_request_id_is_idempotent_and_turn_sequence_is_monotonic(self):
        request_id = f"idem-{time.time_ns()}"
        body = {
            "query": "幂等推荐测试",
            "client_request_id": request_id,
        }
        first = self.client.post(
            "/api/agent/recommend/start",
            headers=self.headers,
            json=body,
        )
        second = self.client.post(
            "/api/agent/recommend/start",
            headers=self.headers,
            json=body,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_data = first.json()["data"]
        second_data = second.json()["data"]
        self.assertEqual(second_data["task_id"], first_data["task_id"])
        self.assertEqual(second_data["session_id"], first_data["session_id"])
        self.assertFalse(first_data["reused"])
        self.assertTrue(second_data["reused"])
        self.assertEqual(self.wait_for_task(first_data["task_id"])["turn_seq"], 1)

        followup = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={
                "session_id": first_data["session_id"],
                "message": "继续推荐",
                "client_request_id": f"turn-{time.time_ns()}",
            },
        )
        self.assertEqual(followup.status_code, 200)
        followup_task = self.wait_for_task(followup.json()["data"]["task_id"])
        self.assertEqual(followup_task["turn_seq"], 2)

        with orm_session() as session:
            task_count = session.scalar(
                select(func.count())
                .select_from(AgentTask)
                .where(AgentTask.client_request_id == request_id)
            )
            user_message_count = session.scalar(
                select(func.count())
                .select_from(AgentMessage)
                .where(
                    AgentMessage.session_id == first_data["session_id"],
                    AgentMessage.role == "user",
                    AgentMessage.content == "幂等推荐测试",
                )
            )
        self.assertEqual(task_count, 1)
        self.assertEqual(user_message_count, 1)

    def test_different_recommendation_sessions_run_in_parallel_and_report_active_tasks(self):
        release = threading.Event()
        both_started = threading.Event()
        counter_lock = threading.Lock()
        started = 0

        def slow_recommendation(*_args, **_kwargs):
            nonlocal started
            with counter_lock:
                started += 1
                if started == 2:
                    both_started.set()
            release.wait(10)
            return _offline_recommendation()

        first_data = None
        second_data = None
        try:
            with patch(
                "backend.api.agent.run_recommendation_agent",
                side_effect=slow_recommendation,
            ):
                first = self.client.post(
                    "/api/agent/recommend/start",
                    headers=self.headers,
                    json={
                        "query": "并行会话一",
                        "client_request_id": f"parallel-a-{time.time_ns()}",
                    },
                )
                second = self.client.post(
                    "/api/agent/recommend/start",
                    headers=self.headers,
                    json={
                        "query": "并行会话二",
                        "client_request_id": f"parallel-b-{time.time_ns()}",
                    },
                )
                self.assertEqual(first.status_code, 200)
                self.assertEqual(second.status_code, 200)
                first_data = first.json()["data"]
                second_data = second.json()["data"]
                self.assertTrue(both_started.wait(5), "两个推荐会话未同时进入执行阶段")

                listing = self.client.get(
                    "/api/agent/sessions",
                    headers=self.headers,
                ).json()["data"]
                active = {
                    item["id"]: item.get("active_task")
                    for item in listing
                    if item["id"] in {first_data["session_id"], second_data["session_id"]}
                }
                self.assertEqual(set(active), {first_data["session_id"], second_data["session_id"]})
                self.assertTrue(all(task and task["status"] == "running" for task in active.values()))

                detail = self.client.get(
                    f"/api/agent/sessions/{first_data['session_id']}",
                    headers=self.headers,
                )
                self.assertEqual(
                    detail.json()["data"]["active_task"]["task_id"],
                    first_data["task_id"],
                )
                blocked_delete = self.client.delete(
                    f"/api/agent/sessions/{first_data['session_id']}",
                    headers=self.headers,
                )
                self.assertEqual(blocked_delete.status_code, 409)
        finally:
            release.set()

        self.assertEqual(self.wait_for_task(first_data["task_id"])["status"], "succeeded")
        self.assertEqual(self.wait_for_task(second_data["task_id"])["status"], "succeeded")
        completed_detail = self.client.get(
            f"/api/agent/sessions/{first_data['session_id']}",
            headers=self.headers,
        ).json()["data"]
        self.assertIsNone(completed_detail["active_task"])

    def test_same_recommendation_session_remains_strictly_serial(self):
        anime_name = f"串行会话测试 {time.time_ns()}"
        session_id = self.create_completed_recommendation_session(anime_name)
        release = threading.Event()
        started = threading.Event()

        def slow_followup(query, *_args, **_kwargs):
            started.set()
            release.wait(10)
            return _offline_followup(query)

        first_task_id = None
        try:
            with patch(
                "backend.api.agent.run_recommendation_followup",
                side_effect=slow_followup,
            ):
                first = self.client.post(
                    "/api/agent/recommend/message",
                    headers=self.headers,
                    json={
                        "session_id": session_id,
                        "message": f"详细介绍《{anime_name}》",
                        "client_request_id": f"serial-a-{time.time_ns()}",
                    },
                )
                self.assertEqual(first.status_code, 200)
                first_task_id = first.json()["data"]["task_id"]
                self.assertTrue(started.wait(5))
                second = self.client.post(
                    "/api/agent/recommend/message",
                    headers=self.headers,
                    json={
                        "session_id": session_id,
                        "message": "这条消息不应并行执行",
                        "client_request_id": f"serial-b-{time.time_ns()}",
                    },
                )
                self.assertEqual(second.status_code, 409)
        finally:
            release.set()
        self.assertEqual(self.wait_for_task(first_task_id)["status"], "succeeded")

    def test_opinion_and_recommendation_agents_can_run_together(self):
        release = threading.Event()
        both_started = threading.Event()
        counter_lock = threading.Lock()
        started_types = set()

        def mark_started(agent_type):
            with counter_lock:
                started_types.add(agent_type)
                if started_types == {"recommendation", "opinion"}:
                    both_started.set()
            release.wait(10)

        def slow_recommendation(*_args, **_kwargs):
            mark_started("recommendation")
            return _offline_recommendation()

        def slow_opinion(*_args, **_kwargs):
            mark_started("opinion")
            return {
                "anime": {"id": None, "name": "并行舆情测试"},
                "report": {"summary": "并行舆情诊断完成"},
                "agent_steps": [],
            }

        recommendation_data = None
        opinion_data = None
        try:
            with (
                patch(
                    "backend.api.agent.run_recommendation_agent",
                    side_effect=slow_recommendation,
                ),
                patch(
                    "backend.api.agent.analyze_public_opinion",
                    side_effect=slow_opinion,
                ),
            ):
                recommendation = self.client.post(
                    "/api/agent/recommend/start",
                    headers=self.headers,
                    json={
                        "query": "跨 Agent 并行推荐",
                        "client_request_id": f"cross-recommend-{time.time_ns()}",
                    },
                )
                opinion = self.client.post(
                    "/api/agent/opinion/analyze",
                    headers=self.headers,
                    json={
                        "name": "并行舆情测试",
                        "query": "分析并行能力",
                        "client_request_id": f"cross-opinion-{time.time_ns()}",
                    },
                )
                self.assertEqual(recommendation.status_code, 200)
                self.assertEqual(opinion.status_code, 200)
                recommendation_data = recommendation.json()["data"]
                opinion_data = opinion.json()["data"]
                self.assertTrue(both_started.wait(5), "两个 Agent 未同时进入执行阶段")
        finally:
            release.set()

        self.assertEqual(
            self.wait_for_task(recommendation_data["task_id"])["status"],
            "succeeded",
        )
        self.assertEqual(
            self.wait_for_task(opinion_data["task_id"])["status"],
            "succeeded",
        )

    def test_task_requires_owner(self):
        resp = self.client.post(
            "/api/agent/recommend/start",
            headers=self.headers,
            json={"query": "quiet sci fi anime"},
        )
        self.assertEqual(resp.status_code, 200)
        task_id = resp.json()["data"]["task_id"]

        username = f"agt_other{time.time_ns() % 100000000}"
        other = self.client.post("/api/auth/register", json={"username": username, "password": "password123"})
        other_token = other.json()["data"]["token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}

        forbidden = self.client.get(f"/api/agent/tasks/{task_id}", headers=other_headers)
        self.assertEqual(forbidden.status_code, 404)
        self.assertEqual(self.wait_for_task(task_id)["status"], "succeeded")

    def test_opinion_agent_requires_target(self):
        resp = self.client.post("/api/agent/opinion/analyze", headers=self.headers, json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 400)

    def test_watch_guides_are_private_and_deletable(self):
        anime_name = f"API 测试番剧 {time.time_ns()}"
        with orm_session() as session:
            record = WatchGuide(
                user_id=self.user_id,
                source_session_id=None,
                anime_name=anime_name,
                anime_key=f"{time.time_ns():064x}"[-64:],
                guide_content="**📺 观看前，你需要知道的**\n这是一份测试指南。",
            )
            session.add(record)
            session.flush()
            guide_id = record.id

        listed = self.client.get("/api/agent/watch-guides", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(
            any(item["id"] == guide_id for item in listed.json()["data"]["items"])
        )

        detail = self.client.get(f"/api/agent/watch-guides/{guide_id}", headers=self.headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["anime_name"], anime_name)
        self.assertIn("测试指南", detail.json()["data"]["guide_content"])

        other_name = f"ago{time.time_ns() % 100000000}"
        other = self.client.post(
            "/api/auth/register",
            json={"username": other_name, "password": "password123"},
        )
        other_headers = {"Authorization": f"Bearer {other.json()['data']['token']}"}
        hidden = self.client.get(
            f"/api/agent/watch-guides/{guide_id}",
            headers=other_headers,
        )
        self.assertEqual(hidden.status_code, 404)
        forbidden_delete = self.client.delete(
            f"/api/agent/watch-guides/{guide_id}",
            headers=other_headers,
        )
        self.assertEqual(forbidden_delete.status_code, 404)

        deleted = self.client.delete(
            f"/api/agent/watch-guides/{guide_id}",
            headers=self.headers,
        )
        self.assertEqual(deleted.status_code, 200)
        missing = self.client.get(f"/api/agent/watch-guides/{guide_id}", headers=self.headers)
        self.assertEqual(missing.status_code, 404)

    def test_specific_followup_offer_and_confirmation_create_watch_guide(self):
        anime_name = f"观看指南集成测试 {time.time_ns()}"
        session_id = self.create_completed_recommendation_session(anime_name)

        followup = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": f"详细介绍《{anime_name}》"},
        )
        self.assertEqual(followup.status_code, 200)
        first_task = self.wait_for_task(followup.json()["data"]["task_id"])
        self.assertEqual(first_task["status"], "succeeded")
        self.assertEqual(first_task["result"]["response_mode"], "conversation")
        self.assertIn("加入“待看番剧指南”", first_task["result"]["answer"])

        accepted = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": "需要"},
        )
        self.assertEqual(accepted.status_code, 200)
        second_task = self.wait_for_task(accepted.json()["data"]["task_id"])
        self.assertEqual(second_task["status"], "succeeded")
        self.assertIn("已将", second_task["result"]["answer"])
        guide_id = second_task["result"]["watch_guide"]["id"]

        detail = self.client.get(
            f"/api/agent/watch-guides/{guide_id}",
            headers=self.headers,
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["anime_name"], anime_name)
        self.assertIn("分阶段观看计划", detail.json()["data"]["guide_content"])

        self.client.delete(f"/api/agent/watch-guides/{guide_id}", headers=self.headers)

    def test_detailed_question_is_not_mistaken_for_guide_confirmation(self):
        anime_name = f"确认分类集成测试 {time.time_ns()}"
        session_id = self.create_completed_recommendation_session(anime_name)
        offered = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": f"《{anime_name}》怎么样？"},
        )
        first_task = self.wait_for_task(offered.json()["data"]["task_id"])
        self.assertIn("加入“待看番剧指南”", first_task["result"]["answer"])

        question = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": "我需要知道有几集"},
        )
        second_task = self.wait_for_task(question.json()["data"]["task_id"])
        self.assertNotIn("watch_guide", second_task["result"])
        self.assertNotIn("加入“待看番剧指南”", second_task["result"]["answer"])

        stale_yes = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": "需要"},
        )
        third_task = self.wait_for_task(stale_yes.json()["data"]["task_id"])
        self.assertNotIn("watch_guide", third_task["result"])
        listing = self.client.get("/api/agent/watch-guides", headers=self.headers).json()["data"]
        self.assertFalse(any(item["anime_name"] == anime_name for item in listing["items"]))

    def test_declining_watch_guide_consumes_offer_without_saving(self):
        anime_name = f"拒绝指南集成测试 {time.time_ns()}"
        session_id = self.create_completed_recommendation_session(anime_name)
        offered = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": f"《{anime_name}》有几集？"},
        )
        first_task = self.wait_for_task(offered.json()["data"]["task_id"])
        self.assertIn("加入“待看番剧指南”", first_task["result"]["answer"])

        declined = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": "不需要"},
        )
        second_task = self.wait_for_task(declined.json()["data"]["task_id"])
        self.assertIn("不把", second_task["result"]["answer"])
        self.assertNotIn("watch_guide", second_task["result"])

        repeated = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": f"再讲讲《{anime_name}》的角色"},
        )
        third_task = self.wait_for_task(repeated.json()["data"]["task_id"])
        self.assertNotIn("加入“待看番剧指南”", third_task["result"]["answer"])

    def test_ignoring_old_offer_can_offer_and_save_a_different_anime(self):
        first_name = f"切换前动画 {time.time_ns()}"
        second_name = f"切换后动画 {time.time_ns()}"
        session_id = self.create_completed_recommendation_session(first_name)
        first = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": f"详细介绍《{first_name}》"},
        )
        first_task = self.wait_for_task(first.json()["data"]["task_id"])
        self.assertIn(first_name, first_task["result"]["answer"])

        switched = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={
                "session_id": session_id,
                "message": f"不用，改为详细介绍《{second_name}》",
            },
        )
        second_task = self.wait_for_task(switched.json()["data"]["task_id"])
        events = second_task["result"]["watch_guide_events"]
        self.assertEqual([event["type"] for event in events], ["ignored", "offered"])
        self.assertEqual(events[-1]["anime"]["name"], second_name)

        accepted = self.client.post(
            "/api/agent/recommend/message",
            headers=self.headers,
            json={"session_id": session_id, "message": "需要"},
        )
        third_task = self.wait_for_task(accepted.json()["data"]["task_id"])
        guide_id = third_task["result"]["watch_guide"]["id"]
        detail = self.client.get(
            f"/api/agent/watch-guides/{guide_id}",
            headers=self.headers,
        ).json()["data"]
        self.assertEqual(detail["anime_name"], second_name)
        self.client.delete(f"/api/agent/watch-guides/{guide_id}", headers=self.headers)


if __name__ == "__main__":
    unittest.main()
