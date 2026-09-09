# -*- coding: utf-8 -*-
"""轻量 Okapi BM25 倒排索引，不依赖具体数据库方言。"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import log


BM25_K1 = 1.5
BM25_B = 0.75


class BM25Index:
    """构建后只读的 BM25 索引，可安全供并发检索共享。"""

    def __init__(self, tokenized_documents: list[list[str]]):
        self.document_lengths = tuple(len(tokens) for tokens in tokenized_documents)
        self.document_count = len(tokenized_documents)
        self.average_document_length = (
            sum(self.document_lengths) / self.document_count
            if self.document_count
            else 0.0
        )
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for document_index, tokens in enumerate(tokenized_documents):
            for term, frequency in Counter(tokens).items():
                postings[term].append((document_index, frequency))
        self.postings = dict(postings)
        self.inverse_document_frequency = {
            term: log(
                1.0
                + (self.document_count - len(term_postings) + 0.5)
                / (len(term_postings) + 0.5)
            )
            for term, term_postings in self.postings.items()
        }

    def rank(
        self,
        query_terms: list[str],
        *,
        top_k: int,
        allowed_document_indexes: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        """返回 ``(文档下标, BM25 分数)``，同分时保持文档顺序稳定。"""
        if top_k <= 0 or not query_terms or not self.document_count:
            return []

        scores: dict[int, float] = defaultdict(float)
        average_length = self.average_document_length or 1.0
        for term in dict.fromkeys(query_terms):
            idf = self.inverse_document_frequency.get(term)
            if idf is None:
                continue
            for document_index, term_frequency in self.postings[term]:
                if (
                    allowed_document_indexes is not None
                    and document_index not in allowed_document_indexes
                ):
                    continue
                document_length = self.document_lengths[document_index]
                denominator = term_frequency + BM25_K1 * (
                    1.0 - BM25_B + BM25_B * document_length / average_length
                )
                scores[document_index] += idf * (
                    term_frequency * (BM25_K1 + 1.0) / denominator
                )

        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:top_k]
