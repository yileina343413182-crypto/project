# -*- coding: utf-8 -*-
"""Regression tests for database-specific RAG document upserts."""

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.dialects.mysql import dialect as mysql_dialect

from backend.rag.storage import upsert_documents


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


if __name__ == "__main__":
    unittest.main()
