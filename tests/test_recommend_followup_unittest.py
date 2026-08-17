# -*- coding: utf-8 -*-
"""推荐完成后普通文本追问的路由与输出契约。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.agents.recommend_followup import (
    extract_last_recommendation_context,
    run_recommendation_followup,
)
from backend.api.agent import _run_recommendation_task


def _recommendation_message(*, clarification: bool = False) -> dict:
    result = {
        "need_clarification": clarification,
        "clarifying_question": "还喜欢什么题材？" if clarification else "",
        "recommendations": [] if clarification else [
            {
                "anime_id": 876,
                "name": "CLANNAD ～AFTER STORY～",
                "platform": "local",
                "reason": "这是一段已保存的详细推荐理由。",
                "match_tags": ["情感", "成长"],
                "evidence": {
                    "sentiment": {"positive": 10, "total": 12},
                    "topics": ["家庭", "成长"],
                    "comments": [],
                },
            }
        ],
    }
    return {
        "role": "agent",
        "content": "还喜欢什么题材？" if clarification else "推荐结果已生成",
        "metadata": {"session_id": 1, "result": result},
    }


class _PlainModel:
    def __init__(self):
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return type("Response", (), {"content": "这是一段详细的普通文本回答。"})()


class RecommendationFollowupTest(unittest.TestCase):
    def test_clarification_does_not_switch_to_conversation_mode(self):
        context = extract_last_recommendation_context(
            [_recommendation_message(clarification=True)]
        )

        self.assertIsNone(context)

    def test_successful_recommendation_enables_followup_context(self):
        context = extract_last_recommendation_context([_recommendation_message()])

        self.assertIsNotNone(context)
        self.assertEqual(
            context["recommendations"][0]["name"],
            "CLANNAD ～AFTER STORY～",
        )
        self.assertNotIn("prompt_trace", context)

    def test_followup_returns_plain_answer_without_recommendation_schema(self):
        context = extract_last_recommendation_context([_recommendation_message()])
        model = _PlainModel()

        with patch(
            "backend.agents.recommend_followup.get_chat_model",
            return_value=model,
        ):
            payload = run_recommendation_followup(
                "详细介绍这部作品",
                [_recommendation_message()],
                context,
            )

        self.assertEqual(payload["response_mode"], "conversation")
        self.assertEqual(payload["answer"], "这是一段详细的普通文本回答。")
        self.assertNotIn("result", payload)
        self.assertNotIn("recommendations", payload)
        self.assertIn("CLANNAD", model.messages[1][1])

    def test_missing_model_uses_saved_recommendation_as_fallback(self):
        context = extract_last_recommendation_context([_recommendation_message()])

        with patch(
            "backend.agents.recommend_followup.get_chat_model",
            return_value=None,
        ):
            payload = run_recommendation_followup(
                "详细介绍 CLANNAD ～AFTER STORY～",
                [],
                context,
            )

        self.assertTrue(payload["fallback"])
        self.assertIn("CLANNAD ～AFTER STORY～", payload["answer"])
        self.assertIn("已保存的详细推荐理由", payload["answer"])

    def test_worker_routes_completed_session_to_plain_followup(self):
        context = extract_last_recommendation_context([_recommendation_message()])
        followup = {
            "response_mode": "conversation",
            "answer": "持久化的详细回答",
            "fallback": False,
        }

        with (
            patch(
                "backend.api.agent.run_recommendation_followup",
                return_value=followup,
            ) as followup_mock,
            patch("backend.api.agent.run_recommendation_agent") as recommendation_mock,
            patch("backend.api.agent.save_agent_message_sync") as save_mock,
        ):
            payload = _run_recommendation_task(
                9,
                7,
                3,
                "继续详细介绍",
                [_recommendation_message()],
                context,
            )

        recommendation_mock.assert_not_called()
        followup_mock.assert_called_once()
        save_mock.assert_called_once_with(
            3,
            "agent",
            "持久化的详细回答",
            payload,
            source_task_id=9,
            task_outcome="succeeded",
        )
        self.assertEqual(payload["response_mode"], "conversation")

    def test_external_anime_fallback_does_not_append_watch_guide_offer(self):
        context = extract_last_recommendation_context([_recommendation_message()])
        external = {
            "anime_id": None,
            "name": "不存在于推荐结果的新番",
            "key": "external-key",
            "source": "explicit_title",
        }
        followup = {
            "response_mode": "conversation",
            "answer": "当前无法可靠补充这部新番的信息。",
            "fallback": True,
        }

        with (
            patch("backend.api.agent.resolve_anime_subject", return_value=external),
            patch("backend.api.agent.run_recommendation_followup", return_value=followup),
            patch("backend.api.agent.should_offer_watch_guide") as offer_mock,
            patch("backend.api.agent.save_agent_message_sync") as save_mock,
        ):
            payload = _run_recommendation_task(
                10,
                7,
                3,
                "详细介绍《不存在于推荐结果的新番》",
                [_recommendation_message()],
                context,
                {"pending_offer": None, "active_target": None, "offered_keys": []},
                "normal",
            )

        offer_mock.assert_not_called()
        self.assertNotIn("待看番剧指南", payload["answer"])
        save_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
