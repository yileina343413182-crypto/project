# -*- coding: utf-8 -*-
"""RAG 索引持久化验收与安全激活测试。"""

import unittest
from unittest.mock import Mock, patch

from backend.rag.indexer import run_index_job


class RagIndexerVerificationTest(unittest.TestCase):
    def _run(self, index_status, query_result=None):
        store = Mock()
        store.upsert.return_value = 2
        store.persisted_index_status.return_value = index_status
        store.query.return_value = query_result or []
        store.last_embedding_dimension = 1024
        embedding = Mock(available=True)
        docs = [
            {"doc_id": "doc:1", "content": "one", "metadata": {}},
            {"doc_id": "doc:2", "content": "two", "metadata": {}},
        ]
        with (
            patch("backend.rag.indexer.build_documents", return_value=docs),
            patch("backend.rag.indexer.upsert_documents"),
            patch("backend.rag.indexer.update_index_job") as update_job,
            patch("backend.rag.indexer.ChromaVectorStore", return_value=store),
            patch("backend.rag.indexer.EmbeddingClient", return_value=embedding),
            patch("backend.rag.indexer.set_collection_metadata") as set_metadata,
            patch("backend.rag.indexer.set_active_collection") as set_active,
        ):
            result = run_index_job(9, "new_collection", activate=True)
        return result, update_job, set_metadata, set_active

    def test_incomplete_hnsw_files_fail_verification_without_activation(self):
        result, update_job, set_metadata, set_active = self._run(
            {
                "index_files_complete": False,
                "error": "missing persisted HNSW files: header.bin",
            }
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["verified"])
        self.assertFalse(result["activated"])
        self.assertEqual(result["indexed_docs"], 2)
        self.assertIn("header.bin", result["verification_error"])
        set_metadata.assert_not_called()
        set_active.assert_not_called()
        self.assertEqual(update_job.call_args.kwargs["status"], "failed")

    def test_complete_persisted_index_is_verified_before_activation(self):
        result, update_job, set_metadata, set_active = self._run(
            {"index_files_complete": True, "error": ""},
            query_result=[{"doc_id": "doc:1"}],
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["verified"])
        self.assertTrue(result["activated"])
        set_metadata.assert_called_once()
        set_active.assert_called_once_with("new_collection")
        self.assertEqual(update_job.call_args.kwargs["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
