# -*- coding: utf-8 -*-
"""无需启动外部服务的后端 API 冒烟测试。"""

import unittest

from backend.app import create_app


class ApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app()
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_health(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["data"]["status"], "running")

    def test_anime_list_shape(self):
        resp = self.client.get("/api/anime/list")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["code"], 200)
        self.assertIsInstance(payload["data"], list)
        if payload["data"]:
            first = payload["data"][0]
            self.assertIn("id", first)
            self.assertIn("name", first)
            self.assertIn("comment_count", first)

    def test_404_shape(self):
        resp = self.client.get("/api/not-found")
        self.assertEqual(resp.status_code, 404)
        payload = resp.get_json()
        self.assertEqual(payload["code"], 404)
        self.assertIsNone(payload["data"])


if __name__ == "__main__":
    unittest.main()
