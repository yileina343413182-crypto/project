# -*- coding: utf-8 -*-
"""Smoke tests for RAG APIs and Agent evidence trace fallback."""

import os
import time
import unittest

os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""

from tests.api_test_support import open_test_celery_worker, open_test_client
from backend.rag.embeddings import embedding_status


class RagApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context, cls.client = open_test_client()
        cls.worker_context = open_test_celery_worker()
        cls.worker_context.__enter__()
        username = f"rag{time.time_ns() % 100000000}"
        resp = cls.client.post("/api/auth/register", json={"username": username, "password": "password123"})
        payload = resp.json()
        cls.token = payload["data"]["token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    @classmethod
    def tearDownClass(cls):
        cls.worker_context.__exit__(None, None, None)
        cls.client_context.__exit__(None, None, None)

    def wait_for_task(self, task_id, timeout=15):
        deadline = time.time() + timeout
        last_payload = None
        while time.time() < deadline:
            resp = self.client.get(f"/api/agent/tasks/{task_id}", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            last_payload = resp.json()["data"]
            if last_payload["status"] in ("succeeded", "failed"):
                return last_payload
            time.sleep(0.2)
        self.fail(f"task {task_id} did not finish in time; last={last_payload}")

    def test_embedding_falls_back_without_key(self):
        status = embedding_status()
        self.assertFalse(status["configured"])

    def test_rag_status_and_search_api(self):
        status_resp = self.client.get("/api/rag/index/status", headers=self.headers)
        self.assertEqual(status_resp.status_code, 200)
        status = status_resp.json()["data"]
        self.assertIn("active_collection", status)
        self.assertIn("embedding", status)
        self.assertIn("vector_index", status)
        self.assertIn("vector_index_ready", status)

        search_resp = self.client.post(
            "/api/rag/search",
            headers=self.headers,
            json={"query": "评论 情感 口碑", "top_k": 3},
        )
        self.assertEqual(search_resp.status_code, 200)
        data = search_resp.json()["data"]
        self.assertIn("mode", data)
        self.assertIn("evidence", data)

    def test_rag_eval_api(self):
        run_resp = self.client.post("/api/rag/eval/run", headers=self.headers, json={"top_k": 3})
        self.assertEqual(run_resp.status_code, 200)
        run_id = run_resp.json()["data"]["run_id"]

        detail_resp = self.client.get(f"/api/rag/eval/runs/{run_id}", headers=self.headers)
        self.assertEqual(detail_resp.status_code, 200)
        detail = detail_resp.json()["data"]
        self.assertIn("metrics", detail)
        self.assertIn("items", detail)

    def test_recommendation_agent_contains_rag_trace(self):
        resp = self.client.post(
            "/api/agent/recommend/start",
            headers=self.headers,
            json={"query": "想看口碑稳定的动漫"},
        )
        self.assertEqual(resp.status_code, 200)
        task = self.wait_for_task(resp.json()["data"]["task_id"])
        self.assertEqual(task["status"], "succeeded")
        result = task["result"]["result"]
        self.assertIn("prompt_trace", result)
        self.assertIn("retrieval_evidence", result)


if __name__ == "__main__":
    unittest.main()
