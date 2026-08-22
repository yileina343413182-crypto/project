# -*- coding: utf-8 -*-
"""推荐与舆情 Agent 的证据数量边界测试。"""

import unittest
from unittest.mock import patch

from backend.agents.evidence_excerpt import select_evidence_excerpt
from backend.agents.opinion_agent import (
    _render_opinion_prompt,
    build_compact_opinion_context,
)
from backend.agents.recommend_agent import _render_bounded_prompt
from backend.agents.recommend_context import (
    _select_evidence_by_source,
    evidence_field_coverage,
    pack_recommendation_context,
    retrieve_candidate_evidence,
)


def _evidence(anime_id: int, index: int) -> dict:
    return {
        "doc_id": f"anime:{anime_id}:comment:{index}",
        "content": f"候选 {anime_id} 的证据 {index}：{chr(0x4E00 + index) * 24}",
        "metadata": {
            "doc_id": f"anime:{anime_id}:comment:{index}",
            "anime_id": anime_id,
            "source_type": "comment",
        },
    }


def _typed_evidence(anime_id: int, index: int, source_type: str) -> dict:
    item = _evidence(anime_id, index)
    item["doc_id"] = f"anime:{anime_id}:{source_type}:{index}"
    item["source_type"] = source_type
    item["metadata"].update({
        "doc_id": item["doc_id"],
        "source_type": source_type,
    })
    if source_type == "platform_availability":
        item["metadata"].update({
            "verification_status": "verified",
            "viewing_platform": "测试平台",
        })
    return item


class AgentEvidenceBudgetTest(unittest.TestCase):
    def test_excerpt_selects_relevant_complete_sentence(self):
        relevant = "星光乐队通过舞台冲突呈现少女成长。"
        excerpt = select_evidence_excerpt(
            "无关背景描述很多，但没有涉及用户问题。" + relevant + "结尾讨论发行信息。",
            query="少女成长",
            anime_name="星光乐队",
            topics=["音乐", "群像"],
            target_chars=len(relevant),
            remaining_chars=len(relevant),
        )

        self.assertEqual(excerpt, relevant)
        self.assertTrue(excerpt.endswith("。"))

    def test_excerpt_keeps_complete_paragraph_when_it_fits(self):
        paragraph = "这是围绕音乐与成长展开的完整段落。"
        excerpt = select_evidence_excerpt(
            paragraph + "\n\n另一个无关段落内容更长，而且不应优先选择。",
            query="音乐成长",
            anime_name="",
            topics=[],
            target_chars=len(paragraph),
            remaining_chars=len(paragraph),
        )

        self.assertEqual(excerpt, paragraph)

    @patch("backend.agents.recommend_context.RECOMMEND_CONTEXT_MAX_CHARS", 5000)
    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_PER_ANIME", 4)
    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_CANDIDATES", 5)
    @patch("backend.agents.recommend_context.search_evidence")
    def test_recommendation_model_context_contains_up_to_twenty_evidence(
        self,
        search_evidence,
    ):
        candidates = [
            {"id": anime_id, "name": f"候选 {anime_id}", "topics": []}
            for anime_id in range(1, 7)
        ]
        search_evidence.side_effect = lambda _query, anime_id, top_k: {
            "mode": "hybrid",
            "evidence": [
                _typed_evidence(anime_id, 0, "anime_knowledge"),
                _typed_evidence(anime_id, 1, "comment"),
                _typed_evidence(anime_id, 2, "topic"),
                _typed_evidence(anime_id, 3, "platform_availability"),
                *[_typed_evidence(anime_id, index, "comment") for index in range(4, 20)],
            ],
        }

        evidence_map, diagnostics = retrieve_candidate_evidence(
            "query",
            candidates,
            {},
        )
        packed, budget = pack_recommendation_context(candidates, evidence_map)

        self.assertEqual(search_evidence.call_count, 5)
        self.assertTrue(
            all(call.kwargs["top_k"] == 20 for call in search_evidence.call_args_list)
        )
        self.assertEqual(diagnostics["candidate_count"], 5)
        self.assertEqual(sum(map(len, evidence_map.values())), 20)
        self.assertTrue(
            all(
                item["full_content"] == item["content"]
                for values in evidence_map.values()
                for item in values
            )
        )
        self.assertEqual(len(packed), 5)
        self.assertEqual(budget["final_evidence_count"], 20)
        self.assertLessEqual(budget["after_chars"], 5000)
        self.assertTrue(
            all(
                "evidence_excerpt" in item
                and "content" not in item
                and "full_content" not in item
                for candidate in packed
                for item in candidate["evidence"]
            )
        )
        with patch(
            "backend.agents.recommend_agent.RECOMMEND_PROMPT_MAX_CHARS",
            10000,
        ):
            prompt, _prompt_budget = _render_bounded_prompt(
                1,
                "query",
                {},
                packed,
                [],
            )
        self.assertEqual(
            sum(
                item["doc_id"] in prompt
                for values in evidence_map.values()
                for item in values
            ),
            20,
        )

    def test_source_quota_prioritizes_relation_and_platform_queries(self):
        items = [
            _typed_evidence(1, 0, "topic"),
            _typed_evidence(1, 1, "topic"),
            _typed_evidence(1, 2, "comment"),
            _typed_evidence(1, 3, "anime_profile"),
            _typed_evidence(1, 4, "anime_knowledge"),
            _typed_evidence(1, 5, "anime_relation"),
            _typed_evidence(1, 6, "platform_availability"),
        ]

        selected = _select_evidence_by_source(
            items,
            "有没有相似作品，在哪个播放平台看",
            4,
        )
        source_types = [item["source_type"] for item in selected]

        self.assertEqual(source_types[:2], ["anime_relation", "platform_availability"])
        self.assertIn("anime_knowledge", source_types)
        self.assertEqual(source_types.count("topic"), 0)

    def test_source_quota_fills_limit_when_distinct_groups_are_insufficient(self):
        items = [
            _typed_evidence(1, 0, "anime_knowledge"),
            _typed_evidence(1, 1, "comment"),
            _typed_evidence(1, 2, "topic"),
            _typed_evidence(1, 3, "comment"),
            _typed_evidence(1, 4, "comment"),
        ]

        selected = _select_evidence_by_source(items, "角色成长", 4)

        self.assertEqual(len(selected), 4)
        self.assertEqual([item["source_type"] for item in selected[:3]], [
            "anime_knowledge",
            "comment",
            "topic",
        ])
        self.assertEqual(selected[3]["doc_id"], items[3]["doc_id"])

    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_PER_ANIME", 4)
    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_CANDIDATES", 5)
    @patch("backend.agents.recommend_context.get_anime_documents")
    @patch("backend.agents.recommend_context.search_evidence")
    def test_each_candidate_gets_exact_verified_platform_evidence(
        self,
        search_evidence,
        get_anime_documents,
    ):
        candidates = [
            {"id": anime_id, "name": f"候选 {anime_id}", "topics": []}
            for anime_id in range(1, 6)
        ]
        search_evidence.side_effect = lambda _query, anime_id, top_k: {
            "mode": "hybrid",
            "evidence": [
                _typed_evidence(anime_id, 0, "anime_knowledge"),
                _typed_evidence(anime_id, 1, "comment"),
                _typed_evidence(anime_id, 2, "topic"),
                _typed_evidence(anime_id, 3, "anime_relation"),
            ],
        }
        get_anime_documents.side_effect = lambda anime_id, _types, limit: [
            _typed_evidence(anime_id, 4, "platform_availability")
        ]

        evidence_map, diagnostics = retrieve_candidate_evidence("少女乐队", candidates, {})

        self.assertEqual(get_anime_documents.call_count, 5)
        self.assertTrue(diagnostics["field_coverage"]["platform"])
        self.assertEqual(diagnostics["field_coverage_counts"]["platform"], 5)
        for evidence in evidence_map.values():
            self.assertEqual(len(evidence), 4)
            self.assertEqual(
                sum(item["source_type"] == "platform_availability" for item in evidence),
                1,
            )

    def test_field_coverage_does_not_treat_unverified_platform_as_available(self):
        platform = _typed_evidence(1, 1, "platform_availability")
        platform["metadata"]["verification_status"] = "unverified"
        platform["metadata"]["viewing_platform"] = ""

        coverage = evidence_field_coverage([
            _typed_evidence(1, 0, "anime_knowledge"),
            _typed_evidence(1, 2, "comment"),
            platform,
        ])

        self.assertTrue(coverage["profile"])
        self.assertTrue(coverage["comments"])
        self.assertFalse(coverage["relations"])
        self.assertFalse(coverage["platform"])

    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_CANDIDATES", 5)
    @patch("backend.agents.recommend_context.get_anime_documents", return_value=[])
    @patch("backend.agents.recommend_context.search_evidence")
    def test_backup_candidate_is_retrieved_when_initial_eligible_are_below_three(
        self,
        search_evidence,
        _get_anime_documents,
    ):
        candidates = [{"id": aid, "name": f"候选 {aid}", "topics": []} for aid in range(1, 9)]

        def result(_query, anime_id, top_k):
            evidence = [_typed_evidence(anime_id, 0, "comment")] if anime_id in {1, 2, 6} else []
            return {"mode": "hybrid", "evidence": evidence}

        search_evidence.side_effect = result
        evidence_map, diagnostics = retrieve_candidate_evidence("query", candidates, {})

        self.assertEqual(search_evidence.call_count, 6)
        self.assertEqual(diagnostics["eligible_candidate_ids"], [1, 2, 6])
        self.assertEqual(diagnostics["initial_candidate_count"], 5)
        self.assertEqual(diagnostics["attempted_candidate_count"], 6)
        self.assertEqual(diagnostics["retrieval_coverage"], {
            "covered_candidates": 3,
            "candidate_count": 6,
        })
        self.assertTrue(evidence_map[6])

    def test_opinion_primary_context_keeps_ten_evidence(self):
        evidence = [_evidence(1, index) for index in range(12)]
        evidence[7]["content"] = "完整长句" * 250
        evidence[7]["full_content"] = evidence[7]["content"]

        _context, compact_evidence = build_compact_opinion_context({}, evidence)

        self.assertEqual(len(compact_evidence), 10)
        self.assertTrue(
            all(
                "evidence_excerpt" in item
                and "content" not in item
                and "full_content" not in item
                for item in compact_evidence
            )
        )
        with patch(
            "backend.agents.opinion_agent.OPINION_PROMPT_MAX_CHARS",
            10000,
        ):
            prompt = _render_opinion_prompt(
                "query",
                {"id": 1, "name": "候选 1"},
                {},
                compact_evidence,
            )
        self.assertTrue(
            all(item["doc_id"] in prompt for item in compact_evidence)
        )

    @patch("backend.agents.recommend_context.RECOMMEND_CONTEXT_MAX_CHARS", 100)
    @patch("backend.agents.recommend_context.RECOMMEND_COMMENT_MAX_CHARS", 1)
    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_PER_ANIME", 2)
    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_CANDIDATES", 5)
    def test_recommendation_budget_is_allocated_round_robin(self):
        candidates = [
            {"id": anime_id, "name": f"候选 {anime_id}", "topics": []}
            for anime_id in range(1, 6)
        ]
        evidence_map = {
            anime_id: [
                {
                    **_evidence(anime_id, index),
                    "content": f"候选{anime_id}第{index + 1}条完整证据。",
                    "full_content": f"候选{anime_id}第{index + 1}条完整证据。",
                }
                for index in range(2)
            ]
            for anime_id in range(1, 6)
        }

        packed, _budget = pack_recommendation_context(
            candidates,
            evidence_map,
            "query",
        )

        self.assertTrue(all(candidate["evidence"] for candidate in packed))
        counts = [len(candidate["evidence"]) for candidate in packed]
        self.assertLessEqual(max(counts) - min(counts), 1)

    @patch("backend.agents.recommend_context.RECOMMEND_CONTEXT_MAX_CHARS", 2000)
    @patch("backend.agents.recommend_context.RECOMMEND_COMMENT_MAX_CHARS", 40)
    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_PER_ANIME", 4)
    @patch("backend.agents.recommend_context.RECOMMEND_EVIDENCE_CANDIDATES", 5)
    def test_round_robin_uses_global_remaining_budget_for_long_complete_sentence(self):
        candidates = [
            {"id": anime_id, "name": f"候选 {anime_id}", "topics": []}
            for anime_id in range(1, 6)
        ]
        evidence_map = {
            anime_id: [
                {
                    **_evidence(anime_id, index),
                    "content": (
                        "完整长句" * 100
                        if anime_id == 2 and index == 2
                        else f"候选{anime_id}第{index + 1}条完整证据。"
                    ),
                    "full_content": (
                        "完整长句" * 100
                        if anime_id == 2 and index == 2
                        else f"候选{anime_id}第{index + 1}条完整证据。"
                    ),
                }
                for index in range(4)
            ]
            for anime_id in range(1, 6)
        }

        packed, budget = pack_recommendation_context(candidates, evidence_map, "query")

        self.assertEqual(sum(len(item["evidence"]) for item in packed), 20)
        self.assertEqual(budget["dropped_count"], 0)


if __name__ == "__main__":
    unittest.main()
