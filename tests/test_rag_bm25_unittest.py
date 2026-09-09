# -*- coding: utf-8 -*-
"""Okapi BM25 排序、过滤与索引缓存的定向回归测试。"""

from contextlib import contextmanager
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import RagDocument
from backend.rag.bm25 import BM25Index
from backend.rag.storage import (
    _invalidate_bm25_cache,
    bm25_search_documents,
    upsert_documents,
)


def _doc(doc_id: str, anime_id: int, content: str) -> dict:
    return {
        "doc_id": doc_id,
        "source_type": "comment",
        "content": content,
        "metadata": {
            "anime_id": anime_id,
            "anime_name": f"anime-{anime_id}",
            "comment_id": int(doc_id.rsplit(":", 1)[-1]),
        },
        "content_hash": f"hash-{doc_id}-{content}",
    }


class BM25IndexTest(unittest.TestCase):
    def test_term_frequency_and_length_normalization_affect_order(self):
        index = BM25Index([
            ["rare", "rare"],
            ["rare", "padding", "padding", "padding"],
            ["other"],
        ])

        ranked = index.rank(["rare"], top_k=3)

        self.assertEqual([document_index for document_index, _score in ranked], [0, 1])
        self.assertGreater(ranked[0][1], ranked[1][1])

    def test_allowed_document_filter_is_applied_before_ranking(self):
        index = BM25Index([["term"], ["term", "term"], ["other"]])

        ranked = index.rank(["term"], top_k=3, allowed_document_indexes={0})

        self.assertEqual([document_index for document_index, _score in ranked], [0])


class BM25StorageTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        RagDocument.__table__.create(self.engine)
        self.session_factory = sessionmaker(
            self.engine,
            expire_on_commit=False,
            future=True,
        )
        _invalidate_bm25_cache()

    def tearDown(self):
        _invalidate_bm25_cache()
        self.engine.dispose()

    @contextmanager
    def orm_session(self):
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def test_search_returns_bm25_score_and_respects_anime_filter(self):
        documents = [
            _doc("comment:1", 1, "治愈 日常 治愈"),
            _doc("comment:2", 2, "治愈 治愈 治愈"),
            _doc("comment:3", 1, "热血 战斗"),
        ]
        with patch("backend.rag.storage.orm_session", self.orm_session):
            upsert_documents("rag_test", documents)
            result = bm25_search_documents(
                "治愈",
                "rag_test",
                anime_id=1,
                top_k=5,
            )

        self.assertEqual([item["metadata"]["doc_id"] for item in result], ["comment:1"])
        self.assertGreater(result[0]["bm25_score"], 0)
        self.assertGreater(result[0]["similarity"], 0)
        self.assertLessEqual(result[0]["similarity"], 1)
        self.assertEqual(result[0]["metadata"]["source_type"], "comment")

    def test_upsert_invalidates_cached_corpus_after_same_document_changes(self):
        with patch("backend.rag.storage.orm_session", self.orm_session):
            upsert_documents("rag_cache", [_doc("comment:1", 1, "太空 冒险")])
            self.assertTrue(bm25_search_documents("太空", "rag_cache"))

            upsert_documents("rag_cache", [_doc("comment:1", 1, "悬疑 推理")])
            result = bm25_search_documents("悬疑", "rag_cache")

        self.assertEqual(result[0]["metadata"]["doc_id"], "comment:1")

    def test_search_covers_documents_beyond_previous_two_thousand_limit(self):
        documents = [
            _doc(f"comment:{index}", 1, "ordinary filler")
            for index in range(1, 2001)
        ]
        documents.append(_doc("comment:2001", 1, "unique needle"))
        with patch("backend.rag.storage.orm_session", self.orm_session):
            upsert_documents("rag_complete", documents)
            result = bm25_search_documents("needle", "rag_complete", top_k=1)

        self.assertEqual(result[0]["metadata"]["doc_id"], "comment:2001")


if __name__ == "__main__":
    unittest.main()
