# -*- coding: utf-8 -*-
"""Smoke tests for the independent async Agent Center API."""

import os
import time
import unittest

os.environ["LLM_API_KEY"] = ""

from tests.api_test_support import open_test_client


class AgentApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context, cls.client = open_test_client()
        username = f"agt{time.time_ns() % 100000000}"
        resp = cls.client.post("/api/auth/register", json={"username": username, "password": "password123"})
        payload = resp.json()
        cls.token = payload["data"]["token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

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

    def test_opinion_agent_requires_target(self):
        resp = self.client.post("/api/agent/opinion/analyze", headers=self.headers, json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 400)


if __name__ == "__main__":
    unittest.main()
