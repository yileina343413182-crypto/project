# -*- coding: utf-8 -*-
"""混合检索、RRF 融合与 Rerank 的定向回归测试。"""

import os
import threading
import unittest
from unittest.mock import Mock, patch

for _key_name in ("EMBEDDING_API_KEY", "RERANK_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "LLM_API_KEY"):
    os.environ[_key_name] = ""

from backend.rag.reranker import BailianReranker
from backend.rag.retriever import _rrf_fuse, search_evidence


def _item(doc_id, similarity):
    return {
        "content": f"content {doc_id}",
        "metadata": {"doc_id": doc_id, "source_type": "comment"},
        "similarity": similarity,
        "source_label": doc_id,
    }


class HybridRetrieverTest(unittest.TestCase):
    def test_rrf_rewards_documents_found_by_both_routes(self):
        fused = _rrf_fuse(
            [_item("vector-only", 0.9), _item("both", 0.8)],
            [_item("keyword-only", 0.7), _item("both", 0.6)],
        )
        self.assertEqual(fused[0]["metadata"]["doc_id"], "both")
        self.assertEqual(fused[0]["vector_rank"], 2)
        self.assertEqual(fused[0]["keyword_rank"], 2)

    @patch("backend.rag.retriever.get_collection_metadata")
    @patch("backend.rag.retriever.get_active_collection", return_value="active")
    @patch("backend.rag.retriever.BailianReranker")
    @patch("backend.rag.retriever.keyword_search_documents")
    @patch("backend.rag.retriever.ChromaVectorStore")
    @patch("backend.rag.retriever.EmbeddingClient")
    def test_vector_and_keyword_run_concurrently_before_rerank(
        self,
        embedding_cls,
        vector_store_cls,
        keyword_search,
        reranker_cls,
        _active_collection,
        collection_metadata,
    ):
        from backend.rag.retriever import EMBEDDING_MODEL, EMBEDDING_PROVIDER

        embedding_cls.return_value.available = True
        collection_metadata.return_value = {
            "embedding_provider": EMBEDDING_PROVIDER,
            "embedding_model": EMBEDDING_MODEL,
        }
        barrier = threading.Barrier(2)

        def vector_result(*args, **kwargs):
            barrier.wait(timeout=1)
            return [_item("vector", 0.9), _item("both", 0.8)]

        def keyword_result(*args, **kwargs):
            barrier.wait(timeout=1)
            return [_item("keyword", 0.7), _item("both", 0.6)]

        vector_store_cls.return_value.query.side_effect = vector_result
        keyword_search.side_effect = keyword_result
        reranker = reranker_cls.return_value
        reranker.available = True

        def reverse_rerank(_query, candidates, top_k):
            return [dict(candidates[-1], rerank_score=0.99, rank=1)][:top_k]

        reranker.rerank.side_effect = reverse_rerank
        result = search_evidence("query", top_k=1)

        self.assertEqual(result["mode"], "hybrid")
        self.assertFalse(result["fallback"])
        self.assertTrue(result["rerank_applied"])
        self.assertEqual(result["retrieval_counts"], {"vector": 2, "keyword": 2, "fused": 3})
        self.assertEqual(len(reranker.rerank.call_args.args[1]), 3)
        self.assertEqual(result["evidence"][0]["rerank_score"], 0.99)

    @patch("backend.rag.retriever.get_collection_metadata", return_value=None)
    @patch("backend.rag.retriever.get_active_collection", return_value="active")
    @patch("backend.rag.retriever.BailianReranker")
    @patch("backend.rag.retriever.keyword_search_documents", return_value=[_item("keyword", 0.7)])
    @patch("backend.rag.retriever.EmbeddingClient")
    def test_keyword_remains_available_without_vector_or_reranker(
        self,
        embedding_cls,
        _keyword_search,
        reranker_cls,
        _active_collection,
        _collection_metadata,
    ):
        embedding_cls.return_value.available = False
        reranker_cls.return_value.available = False
        reranker_cls.return_value.rerank.return_value = None

        result = search_evidence("query", top_k=1)

        self.assertEqual(result["mode"], "keyword")
        self.assertTrue(result["fallback"])
        self.assertFalse(result["rerank_applied"])
        self.assertEqual(result["evidence"][0]["doc_id"], "keyword")


class BailianRerankerTest(unittest.TestCase):
    @patch("backend.rag.reranker.requests.post")
    def test_model_scores_replace_rrf_order(self, post):
        response = Mock(ok=True)
        response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.92},
                {"index": 0, "relevance_score": 0.41},
            ]
        }
        post.return_value = response
        reranker = BailianReranker(
            api_key="test-key",
            base_url="https://example.test/reranks",
            model="qwen3-rerank",
        )

        result = reranker.rerank("query", [_item("a", 0.8), _item("b", 0.7)], top_k=2)

        self.assertEqual([item["metadata"]["doc_id"] for item in result], ["b", "a"])
        self.assertGreater(result[0]["rerank_score"], result[1]["rerank_score"])
        self.assertEqual(post.call_args.kwargs["json"]["top_n"], 2)


if __name__ == "__main__":
    unittest.main()
