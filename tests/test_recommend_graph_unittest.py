# -*- coding: utf-8 -*-
"""Focused tests for Recommendation Agent 2.0 LangGraph routing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from backend.agents.fallback import build_recommendation_fallback
from backend.agents import recommend_graph as recommend_graph_module
from backend.agents.recommend_graph import (
    QUESTIONNAIRE_SLOTS,
    create_recommendation_graph,
    repair,
    retrieve_evidence,
    route_evidence,
    route_repair,
    route_validation,
    run_recommendation_graph,
    validate,
)
from backend.agents.recommend_agent import _validate_recommendation
from backend.agents.schemas import (
    RECOMMEND_REASON_MAX_CHARS,
    RECOMMEND_REASON_MIN_CHARS,
    RetrievalEvidence,
)
from backend.agents.tools import RECOMMEND_TOOLS


class RecommendationCheckpointerTest(unittest.TestCase):
    def tearDown(self):
        recommend_graph_module.build_recommendation_graph.cache_clear()
        recommend_graph_module._get_recommendation_checkpointer.cache_clear()

    def test_redis_checkpointer_is_initialized_with_setup(self):
        class DummyRedisSaver:
            def __init__(self):
                self.setup_called = False

            def setup(self):
                self.setup_called = True

        saver = DummyRedisSaver()
        with (
            patch.object(recommend_graph_module, "RECOMMEND_CHECKPOINT_BACKEND", "redis"),
            patch.object(recommend_graph_module, "RedisSaver", return_value=saver) as factory,
        ):
            checkpointer = recommend_graph_module._get_recommendation_checkpointer()

        self.assertIs(checkpointer, saver)
        self.assertTrue(saver.setup_called)
        factory.assert_called_once()
        kwargs = factory.call_args.kwargs
        self.assertEqual(
            kwargs["ttl"],
            {
                "default_ttl": recommend_graph_module.RECOMMEND_CHECKPOINT_TTL_MINUTES,
                "refresh_on_read": True,
            },
        )
        self.assertEqual(
            kwargs["connection_args"]["max_connections"],
            recommend_graph_module.RECOMMEND_REDIS_MAX_CONNECTIONS,
        )
        self.assertTrue(kwargs["checkpoint_prefix"].endswith("checkpoint"))

    def test_redis_failure_only_falls_back_when_enabled(self):
        sqlite_saver = object()
        with (
            patch.object(recommend_graph_module, "RECOMMEND_CHECKPOINT_BACKEND", "redis"),
            patch.object(recommend_graph_module, "RECOMMEND_CHECKPOINT_SQLITE_FALLBACK", True),
            patch.object(recommend_graph_module, "RedisSaver", side_effect=OSError("offline")),
            patch.object(
                recommend_graph_module,
                "_sqlite_recommendation_checkpointer",
                return_value=sqlite_saver,
            ),
        ):
            self.assertIs(
                recommend_graph_module._get_recommendation_checkpointer(),
                sqlite_saver,
            )

        recommend_graph_module._get_recommendation_checkpointer.cache_clear()
        with (
            patch.object(recommend_graph_module, "RECOMMEND_CHECKPOINT_BACKEND", "redis"),
            patch.object(recommend_graph_module, "RECOMMEND_CHECKPOINT_SQLITE_FALLBACK", False),
            patch.object(recommend_graph_module, "RedisSaver", side_effect=OSError("offline")),
        ):
            with self.assertRaises(RuntimeError):
                recommend_graph_module._get_recommendation_checkpointer()


class FakePlanningModel:
    def __init__(self, response):
        self.response = response

    def bind_tools(self, tools):
        del tools
        return self

    def invoke(self, messages):
        del messages
        return self.response


class RepeatingToolPlanningModel(FakePlanningModel):
    def invoke(self, messages):
        del messages
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "inspect_recommendation_candidate",
                    "args": {"anime_id": 1},
                    "id": "repeat-inspect",
                    "type": "tool_call",
                }
            ],
        )


class FakeStructuredModel:
    def __init__(self, response):
        self.response = response

    def with_structured_output(self, schema):
        del schema
        return self

    def bind_tools(self, tools):
        del tools
        return FakePlanningModel(AIMessage(content="", tool_calls=[]))

    def invoke(self, messages):
        del messages
        return self.response


class RecommendationGraphTest(unittest.TestCase):
    def setUp(self):
        self.graph = create_recommendation_graph(InMemorySaver())
        self.preferences = {
            "likes": [],
            "dislikes": [],
            "preferred_moods": [],
            "preferred_genres": [],
            "feedback": [],
        }
        self.history = []

    def _update_preferences(self, user_id, updates):
        del user_id
        for key, values in updates.items():
            values = values if isinstance(values, list) else [values]
            for value in values:
                if value and value not in self.preferences[key]:
                    self.preferences[key].append(value)
        return {key: list(value) for key, value in self.preferences.items()}

    def _append_turn(self, query, payload):
        self.history.append({"role": "user", "content": query})
        self.history.append(
            {
                "role": "agent",
                "content": payload["result"].get("clarifying_question", ""),
                "metadata": {"result": payload["result"]},
            }
        )

    def _run(self, query):
        with (
            patch(
                "backend.agents.recommend_graph.get_user_preferences",
                side_effect=lambda user_id: {
                    key: list(value) for key, value in self.preferences.items()
                },
            ),
            patch(
                "backend.agents.recommend_graph.update_user_preferences",
                side_effect=self._update_preferences,
            ),
            patch(
                "backend.agents.recommend_graph.build_candidate_pool",
                return_value=[
                    {
                        "id": 1,
                        "name": "测试动画",
                        "platform": "local",
                        "comment_count": 20,
                        "score": 1.0,
                        "final_score": 1.0,
                        "sentiment": {
                            "positive": 10,
                            "neutral": 5,
                            "negative": 1,
                            "total": 16,
                        },
                        "topics": ["科幻/成长"],
                        "comments": [],
                        "match_tags": ["本地匹配"],
                    }
                ],
            ),
            patch(
                "backend.agents.recommend_graph.retrieve_candidate_evidence",
                return_value=(
                    {1: []},
                    {
                        "modes": ["live_database"],
                        "candidate_count": 1,
                        "covered_candidates": 0,
                        "raw_evidence_count": 0,
                        "evidence_insufficient": True,
                    },
                ),
            ),
            patch(
                "backend.agents.recommend_graph.get_chat_model",
                return_value=None,
            ),
        ):
            return run_recommendation_graph(
                7,
                query,
                self.history,
                graph=self.graph,
            )

    def test_graph_contains_explicit_state_nodes(self):
        graph = create_recommendation_graph(InMemorySaver())
        expected = {
            "load_preferences",
            "inspect_user_input",
            "collect_preferences",
            "assess_preferences",
            "ask_preference",
            "candidates",
            "evidence",
            "inspect_evidence",
            "pack_context",
            "agent_decide",
            "tools",
            "record_tool_round",
            "tool_limit",
            "generate",
            "validate",
            "repair",
            "success",
            "fallback",
        }
        self.assertTrue(expected.issubset(graph.nodes))

    def test_recommend_tool_registry_contains_only_graph_safe_tools(self):
        self.assertEqual(
            [tool.name for tool in RECOMMEND_TOOLS],
            [
                "inspect_recommendation_candidate",
                "search_candidate_comments",
                "compare_candidate_sentiment",
            ],
        )

    def test_recommendation_reason_uses_trimmed_unicode_length_contract(self):
        candidates = [{"id": 1, "name": "测试动画"}]
        for length, is_valid in (
            (RECOMMEND_REASON_MIN_CHARS - 1, False),
            (RECOMMEND_REASON_MIN_CHARS, True),
            (RECOMMEND_REASON_MAX_CHARS, True),
            (RECOMMEND_REASON_MAX_CHARS + 1, False),
        ):
            with self.subTest(length=length):
                data = {
                    "recommendations": [
                        {
                            "anime_id": 1,
                            "reason": f"  {'理' * length}  ",
                            "evidence_refs": [],
                        }
                    ]
                }
                cleaned, errors = _validate_recommendation(
                    data,
                    candidates,
                    {1: []},
                )

                self.assertEqual(
                    errors == [],
                    is_valid,
                )
                self.assertEqual(
                    len(cleaned["recommendations"][0]["reason"]),
                    length,
                )

    def test_retrieval_evidence_preserves_hybrid_ranking_diagnostics(self):
        evidence = RetrievalEvidence(
            doc_id="comment:1",
            rrf_score=0.125,
            rerank_score=0.91,
            vector_rank=2,
            keyword_rank=1,
        ).model_dump()

        self.assertEqual(evidence["rrf_score"], 0.125)
        self.assertEqual(evidence["rerank_score"], 0.91)
        self.assertEqual(evidence["vector_rank"], 2)
        self.assertEqual(evidence["keyword_rank"], 1)

    def test_local_fallback_reasons_stay_within_length_contract(self):
        result = build_recommendation_fallback(
            "想看科幻治愈动画",
            [
                {"id": 1, "name": "测试动画", "sentiment": {}},
                {
                    "id": 2,
                    "name": "作品" * 80,
                    "platform": "平台" * 80,
                    "final_score": 1.2345,
                    "topics": ["主题" * 40] * 4,
                    "sentiment": {
                        "total": 99999,
                        "positive": 80000,
                        "neutral": 10000,
                        "negative": 9999,
                    },
                },
            ],
            {
                "preferred_genres": ["科幻" * 30],
                "preferred_moods": ["治愈" * 30],
            },
        )

        self.assertEqual(len(result.recommendations), 2)
        for recommendation in result.recommendations:
            reason_chars = len(recommendation.reason.strip())
            self.assertGreaterEqual(reason_chars, RECOMMEND_REASON_MIN_CHARS)
            self.assertLessEqual(reason_chars, RECOMMEND_REASON_MAX_CHARS)

    def test_multilevel_questions_persist_answers_before_recommendation(self):
        turns = [
            ("推荐动漫", "preferred_genres"),
            ("科幻", "preferred_moods"),
            ("轻松治愈", "likes"),
            ("剧情扎实、角色成长", "dislikes"),
        ]
        for query, expected_stage in turns:
            payload = self._run(query)
            result = payload["result"]
            self.assertTrue(result["need_clarification"])
            self.assertEqual(result["preference_stage"], expected_stage)
            self._append_turn(query, payload)

        final_payload = self._run("不要后宫")
        final_result = final_payload["result"]
        self.assertFalse(final_result["need_clarification"])
        self.assertTrue(final_payload["fallback"])
        self.assertEqual(len(final_result["recommendations"]), 1)
        self.assertTrue(
            all(
                RECOMMEND_REASON_MIN_CHARS
                <= len(item["reason"].strip())
                <= RECOMMEND_REASON_MAX_CHARS
                for item in final_result["recommendations"]
            )
        )
        self.assertEqual(self.preferences["preferred_genres"], ["科幻"])
        self.assertEqual(self.preferences["preferred_moods"], ["轻松", "治愈"])
        self.assertEqual(self.preferences["likes"], ["剧情扎实", "角色成长"])
        self.assertEqual(self.preferences["dislikes"], ["后宫"])

    def test_user_can_skip_a_question_explicitly(self):
        self.preferences["preferred_genres"] = ["悬疑"]
        self.preferences["preferred_moods"] = ["烧脑"]

        likes_payload = self._run("继续")
        self.assertEqual(likes_payload["result"]["preference_stage"], "likes")
        self._append_turn("继续", likes_payload)

        dislikes_payload = self._run("没有特别要求")
        self.assertEqual(dislikes_payload["result"]["preference_stage"], "dislikes")
        self._append_turn("没有特别要求", dislikes_payload)

        final_payload = self._run("没有")
        self.assertFalse(final_payload["result"]["need_clarification"])
        self.assertEqual(
            final_payload["result"]["preference_progress"]["completed"],
            list(QUESTIONNAIRE_SLOTS),
        )

    def test_valid_llm_result_passes_through_validate_and_success_nodes(self):
        for slot in QUESTIONNAIRE_SLOTS:
            self.preferences[slot] = [f"value-{slot}"]
        response = {
            "need_clarification": False,
            "clarifying_question": "",
            "recommendations": [
                {
                    "anime_id": 1,
                    "reason": "理" * RECOMMEND_REASON_MIN_CHARS,
                    "match_tags": ["匹配"],
                    "evidence_refs": [],
                }
            ],
            "preference_updates": {},
        }
        with (
            patch(
                "backend.agents.recommend_graph.get_user_preferences",
                return_value=self.preferences,
            ),
            patch(
                "backend.agents.recommend_graph.build_candidate_pool",
                return_value=[
                    {
                        "id": 1,
                        "name": "测试动画",
                        "platform": "local",
                        "comment_count": 20,
                        "score": 1.0,
                        "final_score": 1.0,
                        "sentiment": {
                            "total": 4,
                            "positive": 3,
                            "neutral": 1,
                            "negative": 0,
                        },
                        "topics": ["成长"],
                        "comments": [
                            {
                                "content": "角色成长自然",
                                "sentiment_label": "positive",
                            }
                        ],
                    }
                ],
            ),
            patch(
                "backend.agents.recommend_graph.retrieve_candidate_evidence",
                return_value=(
                    {1: []},
                    {
                        "modes": ["live_database"],
                        "candidate_count": 1,
                        "covered_candidates": 0,
                        "raw_evidence_count": 0,
                        "evidence_insufficient": True,
                    },
                ),
            ),
            patch(
                "backend.agents.recommend_graph.get_chat_model",
                return_value=FakeStructuredModel(response),
            ),
        ):
            payload = run_recommendation_graph(
                7,
                "继续推荐",
                [],
                graph=self.graph,
            )

        self.assertFalse(payload["fallback"])
        recommendation = payload["result"]["recommendations"][0]
        self.assertEqual(recommendation["anime_id"], 1)
        self.assertEqual(recommendation["platform"], "local")
        self.assertEqual(recommendation["comment_count"], 20)
        self.assertEqual(recommendation["evidence"]["sentiment"]["positive"], 3)
        self.assertEqual(recommendation["evidence"]["topics"], ["成长"])
        self.assertEqual(
            recommendation["evidence"]["comments"][0]["content"],
            "角色成长自然",
        )
        preference_updates = payload["result"]["preference_updates"]
        self.assertEqual(preference_updates["applied"], {})
        self.assertEqual(preference_updates["last_query"], "继续推荐")
        self.assertEqual(
            preference_updates["preferences"],
            self.preferences,
        )
        self.assertEqual(
            payload["result"]["prompt_trace"]["template_version"],
            "rag-v6-bounded-reasons",
        )
        self.assertEqual(
            len(payload["result"]["prompt_trace"]["template_hash"]),
            64,
        )
        step_names = [step["name"] for step in payload["agent_steps"]]
        self.assertIn("validate_recommendation", step_names)
        self.assertIn("finalize_recommendation", step_names)

    def test_short_reason_is_repaired_then_revalidated(self):
        state = {
            "candidates": [{"id": 1, "name": "测试动画"}],
            "evidence_map": {1: []},
            "packed_candidates": [{"anime_id": 1, "name": "测试动画"}],
            "prompt_trace": {"template_version": "rag-v6-bounded-reasons"},
            "repair_attempts": 0,
            "llm_data": {
                "need_clarification": False,
                "recommendations": [
                    {
                        "anime_id": 1,
                        "reason": "过短",
                        "evidence_refs": [],
                    }
                ],
                "preference_updates": {},
            },
        }
        first_validation = validate(state)
        self.assertEqual(route_validation(first_validation), "repair")
        self.assertIn("got 2", first_validation["validation_errors"][0])

        repaired_response = {
            "need_clarification": False,
            "recommendations": [
                {
                    "anime_id": 1,
                    "reason": "修" * RECOMMEND_REASON_MIN_CHARS,
                    "evidence_refs": [],
                }
            ],
            "preference_updates": {},
        }
        with patch(
            "backend.agents.recommend_graph.get_chat_model",
            return_value=FakeStructuredModel(repaired_response),
        ):
            repaired = repair({**state, **first_validation})

        self.assertEqual(route_repair(repaired), "validate")
        second_validation = validate({**state, **repaired})
        self.assertEqual(second_validation["validation_errors"], [])
        self.assertEqual(route_validation({**repaired, **second_validation}), "success")

    def test_evidence_exception_is_converted_to_local_fallback_state(self):
        state = {
            "query": "继续推荐",
            "search_query": "继续推荐 科幻",
            "candidates": [{"id": 1, "name": "测试动画"}],
            "preferences": self.preferences,
        }
        with patch(
            "backend.agents.recommend_graph.retrieve_candidate_evidence",
            side_effect=RuntimeError("retrieval unavailable"),
        ):
            result = retrieve_evidence(state)

        self.assertEqual(result["evidence_map"], {1: []})
        self.assertIn("retrieval unavailable", result["fallback_reason"])
        self.assertEqual(result["agent_steps"][0]["status"], "fallback")
        self.assertEqual(route_evidence(result), "fallback")

    def test_model_can_call_tool_then_continue_to_structured_result(self):
        for slot in QUESTIONNAIRE_SLOTS:
            self.preferences[slot] = [f"value-{slot}"]
        candidates = [
            {
                "id": 1,
                "name": "测试动画",
                "platform": "local",
                "comment_count": 20,
                "score": 1.0,
                "final_score": 1.0,
                "sentiment": {"total": 1, "positive": 1},
                "topics": [],
                "comments": [],
            }
        ]
        response = {
            "need_clarification": False,
            "clarifying_question": "",
            "recommendations": [
                {
                    "anime_id": 1,
                    "reason": "理" * RECOMMEND_REASON_MIN_CHARS,
                    "match_tags": ["匹配"],
                    "evidence_refs": [],
                }
            ],
            "preference_updates": {},
        }
        first_plan = FakePlanningModel(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "inspect_recommendation_candidate",
                        "args": {"anime_id": 1},
                        "id": "inspect-1",
                        "type": "tool_call",
                    }
                ],
            )
        )
        final_plan = FakePlanningModel(AIMessage(content="", tool_calls=[]))

        with (
            patch(
                "backend.agents.recommend_graph.get_user_preferences",
                return_value=self.preferences,
            ),
            patch(
                "backend.agents.recommend_graph.build_candidate_pool",
                return_value=candidates,
            ),
            patch(
                "backend.agents.recommend_graph.retrieve_candidate_evidence",
                return_value=(
                    {1: []},
                    {
                        "modes": ["live_database"],
                        "candidate_count": 1,
                        "covered_candidates": 0,
                        "raw_evidence_count": 0,
                        "evidence_insufficient": True,
                    },
                ),
            ),
            patch(
                "backend.agents.recommend_graph.get_chat_model",
                side_effect=[
                    first_plan,
                    final_plan,
                    FakeStructuredModel(response),
                ],
            ),
        ):
            payload = run_recommendation_graph(
                7,
                "继续推荐",
                [],
                graph=self.graph,
            )

        self.assertFalse(payload["fallback"])
        self.assertEqual(payload["result"]["tool_rounds"], 1)
        step_names = [step["name"] for step in payload["agent_steps"]]
        self.assertIn("execute_recommendation_tools", step_names)

    def test_tool_loop_limit_routes_to_fallback(self):
        for slot in QUESTIONNAIRE_SLOTS:
            self.preferences[slot] = [f"value-{slot}"]
        repeated_plan = RepeatingToolPlanningModel(
            AIMessage(content="", tool_calls=[])
        )
        candidates = [
            {
                "id": 1,
                "name": "测试动画",
                "platform": "local",
                "comment_count": 20,
                "score": 1.0,
                "final_score": 1.0,
                "sentiment": {"total": 1, "positive": 1},
                "topics": [],
                "comments": [],
            }
        ]
        with (
            patch(
                "backend.agents.recommend_graph.get_user_preferences",
                return_value=self.preferences,
            ),
            patch(
                "backend.agents.recommend_graph.build_candidate_pool",
                return_value=candidates,
            ),
            patch(
                "backend.agents.recommend_graph.retrieve_candidate_evidence",
                return_value=(
                    {1: []},
                    {
                        "modes": ["live_database"],
                        "candidate_count": 1,
                        "covered_candidates": 0,
                        "raw_evidence_count": 0,
                        "evidence_insufficient": True,
                    },
                ),
            ),
            patch(
                "backend.agents.recommend_graph.get_chat_model",
                return_value=repeated_plan,
            ),
        ):
            payload = run_recommendation_graph(
                7,
                "继续推荐",
                [],
                graph=self.graph,
            )

        self.assertTrue(payload["fallback"])
        self.assertEqual(payload["result"]["tool_rounds"], 3)
        self.assertIn("tool loop limit", payload["result"]["fallback_reason"])
        preference_updates = payload["result"]["preference_updates"]
        self.assertEqual(preference_updates["applied"], {})
        self.assertEqual(preference_updates["last_query"], "继续推荐")
        self.assertEqual(
            preference_updates["preferences"],
            self.preferences,
        )

    def test_failed_node_resumes_from_latest_checkpoint(self):
        for slot in QUESTIONNAIRE_SLOTS:
            self.preferences[slot] = [f"value-{slot}"]
        candidates = [
            {
                "id": 1,
                "name": "测试动画",
                "platform": "local",
                "comment_count": 20,
                "score": 1.0,
                "final_score": 1.0,
                "sentiment": {"total": 1, "positive": 1},
                "topics": [],
                "comments": [],
            }
        ]
        diagnostics = {
            "modes": ["live_database"],
            "candidate_count": 1,
            "covered_candidates": 0,
            "raw_evidence_count": 0,
            "evidence_insufficient": True,
        }
        with (
            patch(
                "backend.agents.recommend_graph.get_user_preferences",
                return_value=self.preferences,
            ),
            patch(
                "backend.agents.recommend_graph.build_candidate_pool",
                return_value=candidates,
            ) as candidate_mock,
            patch(
                "backend.agents.recommend_graph.retrieve_candidate_evidence",
                return_value=({1: []}, diagnostics),
            ) as evidence_mock,
            patch(
                "backend.agents.recommend_graph.pack_recommendation_context",
                side_effect=[
                    RuntimeError("temporary context packing failure"),
                    (
                        [
                            {
                                "anime_id": 1,
                                "name": "测试动画",
                                "evidence": [],
                            }
                        ],
                        {
                            "max_chars": 1000,
                            "before_chars": 0,
                            "after_chars": 0,
                            "estimated_tokens": 0,
                            "raw_evidence_count": 0,
                            "final_evidence_count": 0,
                            "truncated_or_dropped": 0,
                        },
                    ),
                ],
            ) as pack_mock,
            patch(
                "backend.agents.recommend_graph.get_chat_model",
                return_value=None,
            ),
        ):
            payload = run_recommendation_graph(
                7,
                "继续推荐",
                [],
                task_id=901,
                graph=self.graph,
            )

        self.assertTrue(payload["fallback"])
        self.assertEqual(candidate_mock.call_count, 1)
        self.assertEqual(evidence_mock.call_count, 1)
        self.assertEqual(pack_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
