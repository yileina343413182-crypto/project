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


class _DecisionModel:
    def __init__(self, payload):
        self.payload = payload
        self.invocations = 0
        self.messages = None

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.invocations += 1
        self.messages = messages
        return self.payload


def _model_route(query, action, **kwargs):
    model = _DecisionModel({
        "action": action,
        "reason": "测试模型路由",
        "matched_signals": ["test"],
        "target_anime_name": kwargs.pop("target_anime_name", ""),
        "resume_preference": kwargs.pop("resume_preference", False),
        "confidence": 0.95,
    })
    with patch("backend.agents.recommend_turn_router.get_chat_model", return_value=model):
        decision = route_recommendation_turn(query, **kwargs)
    return decision, model


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
        decision, model = _model_route(
            "warm healing anime with solid public opinion",
            "recommendation",
            initial_turn=True,
        )
        self.assertEqual(decision["action"], "recommendation")
        self.assertEqual(model.invocations, 1)

    def test_explicit_recommendation_overrides_greeting(self):
        decision, model = _model_route(
            "你好，请推荐三部治愈番",
            "recommendation",
        )
        self.assertEqual(decision["action"], "recommendation")
        self.assertEqual(model.invocations, 1)

    def test_new_batch_overrides_existing_followup_context(self):
        context = extract_last_recommendation_context([_recommendation_message()])
        decision, model = _model_route(
            "再换三部，不要和刚才重复",
            "recommendation",
            history=[_recommendation_message()],
            has_recommendation_context=True,
            recommendation_context=context,
        )
        self.assertEqual(decision["action"], "recommendation")
        self.assertEqual(model.invocations, 1)

    def test_reference_to_recommendation_result_is_followup(self):
        context = extract_last_recommendation_context([_recommendation_message()])
        decision, model = _model_route(
            "详细介绍《作品1》的剧情",
            "followup",
            history=[_recommendation_message()],
            has_recommendation_context=True,
            recommendation_context=context,
        )
        self.assertEqual(decision["action"], "followup")
        self.assertEqual(model.invocations, 1)

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
        decision, model = _model_route(
            "科幻、悬疑",
            "recommendation",
            history=history,
        )
        self.assertEqual(decision["action"], "recommendation")
        self.assertTrue(decision["resume_preference"])
        self.assertEqual(model.invocations, 1)

    def test_watch_guide_request_wins_even_when_recommendation_is_mentioned(self):
        context = extract_last_recommendation_context([_recommendation_message()])
        decision, model = _model_route(
            "你推荐的相似作品里有玉子市场，请把它加入待看番剧指南",
            "add_watch_guide",
            target_anime_name="玉子市场",
            history=[_recommendation_message()],
            has_recommendation_context=True,
            recommendation_context=context,
        )

        self.assertEqual(decision["action"], "add_watch_guide")
        self.assertEqual(decision["target_anime_name"], "玉子市场")
        self.assertEqual(model.invocations, 1)

    def test_router_context_excludes_comment_and_retrieval_details(self):
        context = {
            "recommendations": [{
                "anime_id": 1,
                "name": "作品1",
                "reason": "相近作品包括玉子市场",
                "match_tags": ["日常"],
                "representative_comments": [{"content": "不应发送的评论正文"}],
                "retrieval_evidence": [{"content": "不应发送的检索正文"}],
            }]
        }
        _decision, model = _model_route(
            "把玉子市场加入待看番剧指南",
            "add_watch_guide",
            target_anime_name="玉子市场",
            has_recommendation_context=True,
            recommendation_context=context,
        )
        prompt = model.messages[1][1]
        self.assertIn("相近作品包括玉子市场", prompt)
        self.assertNotIn("不应发送的评论正文", prompt)
        self.assertNotIn("不应发送的检索正文", prompt)

    def test_router_model_failure_fallback_prioritizes_watch_guide(self):
        with patch("backend.agents.recommend_turn_router.get_chat_model", return_value=None):
            decision = route_recommendation_turn(
                "把你推荐里提到的玉子市场加入待看番剧指南",
                has_recommendation_context=True,
            )

        self.assertEqual(decision["action"], "add_watch_guide")
        self.assertTrue(decision["prompt_trace"]["fallback"])

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
            patch(
                "backend.api.agent.route_recommendation_turn",
                return_value={
                    "action": "recommendation",
                    "reason": "测试模型路由",
                    "matched_signals": [],
                    "target_anime_name": "",
                    "resume_preference": False,
                    "confidence": 1.0,
                },
            ),
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

    def test_worker_executes_direct_watch_guide_action_without_new_recommendation(self):
        message = _recommendation_message((1, 2, 3))
        context = extract_last_recommendation_context([message])
        anime = {"anime_id": 88, "name": "玉子市场", "key": "target-key", "source": "local"}
        stored = {
            "session_id": 3,
            "response_mode": "conversation",
            "answer": "已加入玉子市场",
        }
        with (
            patch(
                "backend.api.agent.route_recommendation_turn",
                return_value={
                    "action": "add_watch_guide",
                    "reason": "用户要求加入待看指南",
                    "matched_signals": [],
                    "target_anime_name": "玉子市场",
                    "resume_preference": False,
                    "confidence": 0.99,
                },
            ),
            patch("backend.api.agent.resolve_anime_subject", return_value=anime),
            patch("backend.api.agent.watch_guide_exists", return_value=False),
            patch("backend.api.agent.generate_watch_guide", return_value={"content": "指南"}),
            patch("backend.api.agent.save_watch_guide_with_message", return_value=stored) as save_guide,
            patch("backend.api.agent.run_recommendation_agent") as recommendation,
        ):
            payload = _run_recommendation_task(
                11,
                7,
                3,
                "把你推荐里提到的玉子市场加入待看番剧指南",
                [message],
                context,
            )

        recommendation.assert_not_called()
        save_guide.assert_called_once()
        self.assertEqual(payload, stored)


if __name__ == "__main__":
    unittest.main()
