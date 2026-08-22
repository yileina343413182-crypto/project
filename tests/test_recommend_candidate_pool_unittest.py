# -*- coding: utf-8 -*-
"""推荐候选召回与确定性评分的定向回归测试。"""

import unittest
from unittest.mock import patch

from backend.agents.tools import build_candidate_pool


class RecommendationCandidatePoolTest(unittest.TestCase):
    def setUp(self):
        self.anime = [
            {"id": 1, "name": "治愈日记", "comment_count": 2000, "platform": "local"},
            {"id": 2, "name": "燃烧群像", "comment_count": 120, "platform": "local"},
            {"id": 3, "name": "推理之城", "comment_count": 150, "platform": "local"},
            {"id": 4, "name": "慢热战记", "comment_count": 500, "platform": "local"},
        ]
        self.stats = {
            1: {"positive": 98, "neutral": 1, "negative": 1, "total": 100},
            2: {"positive": 70, "neutral": 20, "negative": 10, "total": 100},
            3: {"positive": 80, "neutral": 15, "negative": 5, "total": 100},
            4: {"positive": 90, "neutral": 8, "negative": 2, "total": 100},
        }
        self.topics = {
            1: ["日常", "轻松", "治愈"],
            2: ["热血", "战斗", "群像", "成长", "催泪"],
            3: ["悬疑", "推理", "烧脑", "反转"],
            4: ["热血", "战斗", "节奏", "拖沓"],
        }

    def _pool(
        self,
        query,
        preferences=None,
        limit=4,
        excluded=None,
        knowledge=None,
        session_excluded=None,
    ):
        preferences = preferences or {}
        with (
            patch("backend.agents.tools.get_all_anime", return_value=self.anime),
            patch("backend.agents.tools._get_sentiment_stats_map", return_value=self.stats),
            patch("backend.agents.tools._get_topic_terms_map", return_value=self.topics),
            patch(
                "backend.agents.tools._get_structured_knowledge_map",
                return_value=knowledge or {},
            ),
            patch("backend.agents.tools.load_preferences", return_value=preferences),
            patch(
                "backend.agents.tools.get_non_recommendable_anime_ids",
                return_value=set(excluded or []),
            ),
            patch("backend.agents.tools.get_topics", return_value=[]),
            patch(
                "backend.agents.tools.fetch_representative_comments",
                return_value={"positive": []},
            ),
        ):
            return build_candidate_pool(
                query,
                user_id=7 if preferences or excluded is not None else None,
                limit=limit,
                excluded_anime_ids=session_excluded,
            )

    def test_intent_match_beats_unrelated_popularity_and_sentiment(self):
        pool = self._pool("想看热血战斗又催泪的番剧")

        self.assertEqual(pool[0]["id"], 2)
        self.assertGreater(pool[0]["intent_match_score"], 0)
        self.assertIn("匹配：热血", pool[0]["match_tags"])

    def test_different_intents_produce_different_top_candidates(self):
        healing = self._pool("轻松治愈日常")
        mystery = self._pool("悬疑烧脑反转")

        self.assertEqual(healing[0]["id"], 1)
        self.assertEqual(mystery[0]["id"], 3)
        self.assertNotEqual(
            [item["id"] for item in healing[:3]],
            [item["id"] for item in mystery[:3]],
        )

    def test_explicit_title_query_ranks_the_title_first(self):
        pool = self._pool("请推荐《推理之城》")

        self.assertEqual(pool[0]["id"], 3)
        self.assertGreaterEqual(pool[0]["title_match_score"], 1.3)

    def test_dislike_terms_penalize_candidate_topics_not_only_titles(self):
        pool = self._pool(
            "想看热血战斗作品 节奏拖沓",
            preferences={
                "preferred_genres": ["热血", "战斗"],
                "preferred_moods": [],
                "likes": [],
                "dislikes": ["节奏拖沓"],
            },
        )

        ranks = {item["id"]: index for index, item in enumerate(pool)}
        slow = next(item for item in pool if item["id"] == 4)
        self.assertLess(ranks[2], ranks[4])
        self.assertGreater(slow["preference_penalty"], 0)

    def test_watched_and_watching_anime_are_excluded_before_ranking(self):
        pool = self._pool("推荐动画", excluded={1, 2})

        self.assertEqual({item["id"] for item in pool}, {3, 4})

    def test_session_recommendations_are_hard_excluded_with_watch_statuses(self):
        pool = self._pool(
            "推荐动画",
            excluded={1},
            session_excluded=[2, "3", "invalid"],
        )

        self.assertEqual([item["id"] for item in pool], [4])

    def test_explicit_production_company_is_a_hard_constraint(self):
        pool = self._pool(
            "推荐京阿尼旗下的番剧",
            knowledge={
                1: {
                    "summary": "校园日常",
                    "genres": ["日常"],
                    "moods": ["治愈"],
                    "production_companies": ["京都アニメーション"],
                    "year": "2010",
                    "work_type": "TV",
                },
                2: {
                    "summary": "热血群像",
                    "genres": ["战斗"],
                    "moods": ["热血"],
                    "production_companies": ["TRIGGER"],
                    "year": "2015",
                    "work_type": "TV",
                },
            },
        )

        self.assertEqual([item["id"] for item in pool], [1])
        self.assertEqual(
            pool[0]["structured_knowledge"]["production_companies"],
            ["京都アニメーション"],
        )

    def test_current_query_outweighs_long_term_preferences(self):
        pool = self._pool(
            "悬疑推理烧脑反转",
            preferences={
                "preferred_genres": ["战斗", "群像"],
                "preferred_moods": ["热血"],
                "likes": ["成长", "催泪"],
                "dislikes": [],
            },
        )

        self.assertEqual(pool[0]["id"], 3)
        self.assertTrue(all(item["preference_bonus"] <= 0.32 for item in pool))


if __name__ == "__main__":
    unittest.main()
