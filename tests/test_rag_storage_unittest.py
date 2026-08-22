# -*- coding: utf-8 -*-
"""Regression tests for database-specific RAG document upserts."""

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.dialects.mysql import dialect as mysql_dialect

from backend.rag.storage import get_anime_documents, upsert_documents


class _CapturingSession:
    bind = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))

    def __init__(self):
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        statement.compile(dialect=mysql_dialect())


class RagStorageTest(unittest.TestCase):
    def test_mysql_document_upsert_handles_metadata_column(self):
        session = _CapturingSession()

        @contextmanager
        def fake_orm_session():
            yield session

        docs = [{
            "doc_id": "anime:1:profile",
            "source_type": "anime_profile",
            "content": "test content",
            "metadata": {"anime_id": 1, "anime_name": "test"},
            "content_hash": "hash",
        }]

        with patch("backend.rag.storage.orm_session", fake_orm_session):
            upsert_documents("rag_test", docs)

        self.assertIsNotNone(session.statement)
        self.assertIn(
            "ON DUPLICATE KEY UPDATE",
            str(session.statement.compile(dialect=mysql_dialect())),
        )

    def test_exact_anime_document_preserves_structured_metadata(self):
        row = SimpleNamespace(
            id=1,
            doc_id="anime:7:platform_availability",
            source_type="platform_availability",
            anime_id=7,
            anime_name="测试动画",
            content="地区=日本，观看平台=b站",
            document_metadata={
                "verification_status": "verified",
                "viewing_platform": "b站",
            },
        )

        class FakeSession:
            def scalars(self, _statement):
                return SimpleNamespace(all=lambda: [row])

        @contextmanager
        def fake_orm_session():
            yield FakeSession()

        with patch("backend.rag.storage.orm_session", fake_orm_session):
            documents = get_anime_documents(
                7,
                {"platform_availability"},
                collection_name="rag_test",
                limit=1,
            )

        self.assertEqual(documents[0]["doc_id"], row.doc_id)
        self.assertEqual(documents[0]["source_type"], "platform_availability")
        self.assertEqual(documents[0]["metadata"]["anime_id"], 7)
        self.assertEqual(documents[0]["metadata"]["viewing_platform"], "b站")


if __name__ == "__main__":
    unittest.main()
