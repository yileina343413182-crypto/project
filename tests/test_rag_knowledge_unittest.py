# -*- coding: utf-8 -*-
"""知识、关系与播放平台 RAG 文档的定向回归测试。"""

import unittest
from unittest.mock import Mock, patch

from backend.rag.indexer import _knowledge_documents
from backend.rag.knowledge import load_knowledge_records
from scripts.fetch_bangumi_rag_knowledge import (
    _match_score,
    _query_variants,
    _record,
    build_subject_bindings,
)


class RagKnowledgeTest(unittest.TestCase):
    @patch("backend.rag.knowledge.Path.is_file", return_value=True)
    @patch("backend.rag.knowledge.Path.read_text")
    def test_jsonl_loader_indexes_records_by_local_anime_id(self, read_text, _is_file):
        read_text.return_value = (
            '{"anime_id": 7, "knowledge": {"summary": "简介", "source": "source"}}\n'
            '{"anime_id": "invalid"}\n'
        )

        records = load_knowledge_records("knowledge.jsonl")

        self.assertEqual(list(records), [7])

    def test_documents_require_sources_and_keep_platform_verification_explicit(self):
        docs = _knowledge_documents(
            {"id": 7, "name": "测试动画", "platform": "bangumi"},
            {
                "knowledge": {
                    "summary": "无剧透简介。",
                    "genres": ["音乐"],
                    "moods": ["热血"],
                    "character_types": ["少女群像"],
                    "studio": "测试制作公司",
                    "year": 2024,
                    "episodes": 12,
                    "work_type": "TV",
                    "source": "https://example.test/subject/7",
                    "updated_at": "2026-08-21",
                },
                "relations": [
                    {
                        "relation_type": "续作",
                        "related_name": "测试动画 第二季",
                        "source": "https://example.test/relation/7",
                        "updated_at": "2026-08-21",
                    },
                    {"relation_type": "相似", "related_name": "无来源作品"},
                ],
                "platform_availability": [
                    {
                        "platform": "测试平台",
                        "region": "中国大陆",
                        "status": "verified",
                        "source": "https://example.test/platform/7",
                        "updated_at": "2026-08-21",
                    }
                ],
            },
        )

        by_type = {doc["source_type"]: doc for doc in docs}
        self.assertEqual(set(by_type), {
            "anime_knowledge",
            "anime_relation",
            "platform_availability",
        })
        self.assertIn("无剧透简介", by_type["anime_knowledge"]["content"])
        self.assertNotIn("无来源作品", by_type["anime_relation"]["content"])
        self.assertEqual(
            by_type["platform_availability"]["metadata"]["verification_status"],
            "verified",
        )
        self.assertEqual(
            by_type["platform_availability"]["metadata"]["viewing_platform"],
            "测试平台",
        )

    def test_missing_platform_is_unverified_and_collection_source_is_not_availability(self):
        docs = _knowledge_documents(
            {"id": 8, "name": "缺少平台资料", "platform": "bilibili+bangumi"},
            {},
        )

        self.assertEqual(len(docs), 1)
        platform = docs[0]
        self.assertEqual(platform["source_type"], "platform_availability")
        self.assertEqual(platform["metadata"]["verification_status"], "unverified")
        self.assertEqual(platform["metadata"]["viewing_platform"], "")
        self.assertIn("不代表实际播放平台", platform["content"])

    @patch("scripts.fetch_bangumi_rag_knowledge._csv_subject_map", return_value={})
    @patch("scripts.fetch_bangumi_rag_knowledge.get_all_anime")
    def test_reviewed_fuzzy_binding_prevents_known_title_collision(
        self,
        get_all_anime,
        _subject_map,
    ):
        get_all_anime.return_value = [
            {"id": 24, "name": "银魂'"},
            {"id": 130, "name": "刀剑神域进击篇：无星之夜"},
        ]
        session = Mock()

        bindings, failed = build_subject_bindings(session, delay=0)

        self.assertEqual(failed, [])
        self.assertEqual(bindings[24]["subject_id"], 11834)
        self.assertEqual(bindings[130]["subject_id"], 315375)
        self.assertEqual(
            bindings[24]["binding_method"],
            "bangumi_name_fuzzy_reviewed",
        )
        session.post.assert_not_called()

    def test_fuzzy_score_prefers_matching_season_and_movie_markers(self):
        overlord_second = {"name": "オーバーロードⅡ", "name_cn": "OVERLORD 第二季"}
        overlord_first = {"name": "オーバーロード", "name_cn": "OVERLORD"}
        violet_movie = {
            "name": "劇場版 ヴァイオレット・エヴァーガーデン",
            "name_cn": "剧场版 紫罗兰永恒花园",
        }
        violet_tv = {
            "name": "ヴァイオレット・エヴァーガーデン",
            "name_cn": "紫罗兰永恒花园",
        }

        self.assertGreater(
            _match_score("OVERLORD Ⅱ", overlord_second),
            _match_score("OVERLORD Ⅱ", overlord_first),
        )
        self.assertGreater(
            _match_score("紫罗兰永恒花园 剧场版", violet_movie),
            _match_score("紫罗兰永恒花园 剧场版", violet_tv),
        )

    def test_query_variants_keep_season_signal(self):
        variants = _query_variants("小林家的龙女仆 第二季")

        self.assertIn("小林家的龙女仆 2", variants)
        self.assertIn("小林家的龙女仆 S", variants)

    @patch("scripts.fetch_bangumi_rag_knowledge._request_json")
    def test_record_keeps_all_characters_and_configured_platforms(self, request_json):
        request_json.side_effect = [
            {
                "name": "Test Anime",
                "name_cn": "测试动画",
                "summary": "公开简介",
                "date": "2024-01-01",
                "platform": "TV",
                "total_episodes": 12,
                "tags": [{"name": "奇幻"}],
                "rating": {"score": 8.0},
            },
            [
                {"id": index, "name": f"角色{index}", "relation": "主角"}
                for index in range(10)
            ],
            [
                {
                    "id": 2,
                    "name_cn": "测试动画 第二季",
                    "relation": "续集",
                    "type": 2,
                },
                {
                    "id": 3,
                    "name_cn": "测试动画主题曲",
                    "relation": "片头曲",
                    "type": 3,
                },
            ],
        ]

        record = _record(Mock(), {"id": 7, "name": "测试动画"}, 1)

        self.assertEqual(len(record["characters"]), 10)
        self.assertEqual(len(record["knowledge"]["character_types"]), 8)
        self.assertEqual(record["relations"][0]["related_subject_id"], 2)
        self.assertEqual(len(record["relations"]), 1)
        self.assertEqual(
            [item["platform"] for item in record["platform_availability"]],
            ["b站", "囧次元"],
        )
        self.assertTrue(all(
            item["source"] == "project_config:user_provided"
            for item in record["platform_availability"]
        ))
        self.assertTrue(all(
            item["region"] == "日本"
            for item in record["platform_availability"]
        ))


if __name__ == "__main__":
    unittest.main()
