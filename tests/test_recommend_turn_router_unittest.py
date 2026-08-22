# -*- coding: utf-8 -*-
"""推荐会话有限路由、无检索闲聊与跨轮去重测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.agents.recommend_agent import _validate_recommendation
from backend.agents.recommend_followup import (
    extract_last_recommendation_context,
    extract_recommended_anime_ids,
)
from backend.agents.recommend_turn_router import (
    route_recommendation_turn,
    run_recommendation_chat,
)
from backend.api.agent import _run_recommendation_task


def _recommendation_message(ids=(1, 2, 3)) -> dict:
    return {
        "role": "agent",
        "content": "推荐结果已生成",
        "metadata": {
            "session_id": 7,
            "result": {
                "result": {
                    "need_clarification": False,
                    "recommendations": [
                        {"anime_id": anime_id, "name": f"作品{anime_id}"}
                        for anime_id in ids
                    ],
                }
            },
        },
    }


class RecommendationTurnRouterTest(unittest.TestCase):
    def test_greeting_is_chat_and_never_opens_retrieval(self):
        decision = route_recommendation_turn("你好", initial_turn=True)
        self.assertEqual(decision["action"], "chat")

        with patch("backend.agents.recommend_turn_router.get_chat_model") as model:
            payload = run_recommendation_chat("你好")
        model.assert_not_called()
        self.assertEqual(payload["response_mode"], "conversation")
        self.assertIn("你好", payload["answer"])

    def test_initial_filter_description_keeps_legacy_recommendation_entry(self):
        decision = route_recommendation_turn(
            "warm healing anime with solid public opinion",
            initial_turn=True,
        )
        self.assertEqual(decision["action"], "recommendation")

    def test_explicit_recommendation_overrides_greeting(self):
        decision = route_recommendation_turn("你好，请推荐三部治愈番")
        self.assertEqual(decision["action"], "recommendation")

    def test_new_batch_overrides_existing_followup_context(self):
        decision = route_recommendation_turn(
            "再换三部，不要和刚才重复",
            [_recommendation_message()],
            has_recommendation_context=True,
        )
        self.assertEqual(decision["action"], "recommendation")

    def test_reference_to_recommendation_result_is_followup(self):
        decision = route_recommendation_turn(
            "详细介绍《作品1》的剧情",
            [_recommendation_message()],
            has_recommendation_context=True,
        )
        self.assertEqual(decision["action"], "followup")

    def test_pending_preference_answer_resumes_graph(self):
        history = [{
            "role": "agent",
            "content": "你更喜欢什么题材？",
            "metadata": {
                "result": {
                    "need_clarification": True,
                    "preference_stage": "preferred_genres",
                }
            },
        }]
        decision = route_recommendation_turn("科幻、悬疑", history)
        self.assertEqual(decision["action"], "preference_answer")

    def test_all_historical_recommendation_ids_are_unique_and_ordered(self):
        messages = [
            _recommendation_message((1, 2, 3)),
            _recommendation_message((3, 4, 5)),
        ]
        self.assertEqual(extract_recommended_anime_ids(messages), [1, 2, 3, 4, 5])

    def test_forced_result_requires_exactly_three_unique_items(self):
        candidates = [{"id": anime_id, "name": f"作品{anime_id}"} for anime_id in (1, 2, 3)]
        data = {
            "recommendations": [
                {"anime_id": 1, "reason": "理由", "evidence_refs": []},
                {"anime_id": 2, "reason": "理由", "evidence_refs": []},
            ]
        }
        _, errors = _validate_recommendation(data, candidates, {}, required_count=3)
        self.assertIn("recommendations must contain exactly 3 items", errors)

    def test_worker_greeting_does_not_call_recommendation_or_followup(self):
        with (
            patch("backend.api.agent.run_recommendation_agent") as recommendation,
            patch("backend.api.agent.run_recommendation_followup") as followup,
            patch("backend.api.agent.save_agent_message_sync") as save,
        ):
            payload = _run_recommendation_task(9, 7, 3, "你好", [])

        recommendation.assert_not_called()
        followup.assert_not_called()
        save.assert_called_once()
        self.assertEqual(payload["response_mode"], "conversation")
        self.assertEqual(payload["turn_route"]["action"], "chat")

    def test_worker_reenters_graph_and_passes_session_exclusions(self):
        message = _recommendation_message((1, 2, 3))
        context = extract_last_recommendation_context([message])
        graph_result = {
            "result": {
                "need_clarification": False,
                "clarifying_question": "",
                "recommendations": [
                    {"anime_id": anime_id, "name": f"新作品{anime_id}"}
                    for anime_id in (4, 5, 6)
                ],
            },
            "agent_steps": [],
            "fallback": False,
        }
        with (
            patch("backend.api.agent.run_recommendation_agent", return_value=graph_result) as recommendation,
            patch("backend.api.agent.run_recommendation_followup") as followup,
            patch("backend.api.agent.save_agent_message_sync"),
        ):
            payload = _run_recommendation_task(
                10,
                7,
                3,
                "再换三部",
                [message],
                context,
                excluded_anime_ids=[1, 2, 3],
            )

        followup.assert_not_called()
        recommendation.assert_called_once()
        kwargs = recommendation.call_args.kwargs
        self.assertTrue(kwargs["force_recommendation"])
        self.assertEqual(kwargs["excluded_anime_ids"], [1, 2, 3])
        self.assertEqual(payload["turn_route"]["action"], "recommendation")


if __name__ == "__main__":
    unittest.main()
