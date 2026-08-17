# -*- coding: utf-8 -*-
"""无需启动外部服务的后端 API 冒烟测试。"""

import unittest

import jwt

from tests.api_test_support import open_test_client


class ApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context, cls.client = open_test_client()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_health(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "running")

    def test_anime_list_shape(self):
        resp = self.client.get("/api/anime/list")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["code"], 200)
        self.assertIsInstance(payload["data"], list)
        if payload["data"]:
            first = payload["data"][0]
            self.assertIn("id", first)
            self.assertIn("name", first)
            self.assertIn("comment_count", first)

    def test_public_query_endpoints_keep_response_contract(self):
        anime = self.client.get("/api/anime/list").json()["data"]
        anime_id = anime[0]["id"] if anime else 0
        paths = (
            f"/api/comments/{anime_id}",
            f"/api/sentiment/stats/{anime_id}",
            f"/api/sentiment/trend/{anime_id}",
            f"/api/sentiment/scatter/{anime_id}",
            f"/api/topics/{anime_id}",
            f"/api/wordcloud/{anime_id}",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["code"], 200)
                self.assertIn("data", response.json())

    def test_post_validation_keeps_legacy_messages(self):
        cases = (
            ("/api/recommend", {}, "query 不能为空"),
            ("/api/sentiment/predict", {}, "缺少 text 参数"),
            ("/api/auth/login", {}, "用户名和密码不能为空"),
        )
        for path, body, message in cases:
            with self.subTest(path=path):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["msg"], message)

    def test_404_shape(self):
        resp = self.client.get("/api/not-found")
        self.assertEqual(resp.status_code, 404)
        payload = resp.json()
        self.assertEqual(payload["code"], 404)
        self.assertIsNone(payload["data"])

    def test_405_shape(self):
        resp = self.client.post("/api/health")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp.json(), {"code": 405, "msg": "请求方法不允许", "data": None})

    def test_validation_error_shape(self):
        resp = self.client.get("/api/comments/not-an-integer")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 400)
        self.assertIsNone(resp.json()["data"])

    def test_missing_token_uses_unified_contract(self):
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["code"], 401)
        self.assertIsNone(resp.json()["data"])

    def test_access_token_keeps_legacy_claims(self):
        username = "jwt_contract_user"
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "password123"},
        )
        if response.status_code == 400:
            response = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": "password123"},
            )
        self.assertEqual(response.status_code, 200)
        token = response.json()["data"]["token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        for name in ("fresh", "iat", "jti", "type", "sub", "nbf", "csrf", "exp"):
            self.assertIn(name, claims)

        me = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["data"]["username"], username)

        save = self.client.post(
            "/api/history/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_content": "测试问题", "ai_content": "测试回答"},
        )
        self.assertEqual(save.status_code, 200)
        msg_id = save.json()["data"]["msg_id"]

        history = self.client.get(
            "/api/history/chat?page=bad&page_size=bad",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["data"]["page"], 1)
        self.assertEqual(history.json()["data"]["page_size"], 20)

        deleted = self.client.delete(
            f"/api/history/chat/{msg_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["msg"], "删除成功")

    def test_openapi_contains_all_legacy_operations(self):
        operations = [
            method
            for path, item in self.client.app.openapi()["paths"].items()
            if path.startswith("/api")
            for method in item
            if method in {"get", "post", "delete", "put", "patch"}
        ]
        self.assertEqual(len(operations), 34)


if __name__ == "__main__":
    unittest.main()
