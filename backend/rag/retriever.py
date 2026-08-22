# -*- coding: utf-8 -*-
"""混合 RAG 检索器：Chroma 优先，数据库关键词与实时业务数据依次降级。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import unicodedata

from sqlalchemy import or_, select

from backend.database import get_sentiment_stats, get_topics, orm_session
from backend.db.models import Anime, Comment
from backend.rag.embeddings import EMBEDDING_MODEL, EMBEDDING_PROVIDER, EmbeddingClient
from backend.rag.reranker import BailianReranker
from backend.rag.storage import get_active_collection, get_collection_metadata, keyword_search_documents, query_terms
from backend.rag.vector_store import ChromaVectorStore


logger = logging.getLogger(__name__)
RRF_K = 60
INITIAL_RECALL_TOP_K = 50
RERANK_CANDIDATE_LIMIT = 20


def search_evidence(query: str, anime_id: int | None = None, top_k: int = 6) -> dict:
    """并行召回向量与关键词结果，经 RRF 融合、Rerank 后返回证据。"""
    collection_name = get_active_collection()
    embedding_client = EmbeddingClient()
    collection_metadata = get_collection_metadata(collection_name)
    model_matches = bool(collection_metadata and collection_metadata.get("embedding_provider") == EMBEDDING_PROVIDER and collection_metadata.get("embedding_model") == EMBEDDING_MODEL)
    candidate_k = max(top_k, INITIAL_RECALL_TOP_K)
    vector_evidence = []
    keyword_evidence = []
    vector_error_type = ""
    vector_error = ""

    # 只有活动集合的模型元数据与当前配置一致时才查询向量，避免维度错配。
    vector_enabled = bool(collection_name and embedding_client.available and model_matches)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag-retrieve") as executor:
        keyword_future = executor.submit(
            keyword_search_documents,
            query,
            collection_name,
            anime_id=anime_id,
            top_k=candidate_k,
        )
        vector_future = None
        if vector_enabled:
            vector_future = executor.submit(
                ChromaVectorStore().query,
                collection_name,
                query,
                top_k=candidate_k,
                anime_id=anime_id,
                embedding_client=embedding_client,
            )
        try:
            keyword_evidence = keyword_future.result()
        except Exception as exc:
            logger.warning("Keyword retrieval failed: %s", exc)
        if vector_future is not None:
            try:
                vector_evidence = vector_future.result()
            except Exception as exc:
                vector_error_type = type(exc).__name__
                vector_error = str(exc)[:300]
                logger.warning("Vector retrieval failed: %s", exc)

    fused_evidence = _rrf_fuse(vector_evidence, keyword_evidence)
    deduplicated_evidence = _deduplicate_evidence(fused_evidence)
    rerank_candidates = deduplicated_evidence[:RERANK_CANDIDATE_LIMIT]
    evidence = rerank_candidates
    mode = "hybrid" if vector_evidence and keyword_evidence else "chroma" if vector_evidence else "keyword"
    reranker = BailianReranker()
    reranked = reranker.rerank(query, rerank_candidates, top_k=top_k)
    rerank_applied = reranked is not None
    if reranked is not None:
        evidence = reranked

    # 索引为空或损坏时，最后直接从动漫/评论业务表拼出基础证据。
    if not evidence:
        mode = "live_database"
        evidence = _live_database_evidence(query, anime_id=anime_id, top_k=top_k)

    if not model_matches:
        fallback_reason = "embedding_model_mismatch"
    elif vector_error:
        fallback_reason = "vector_query_failed"
    elif not vector_enabled:
        fallback_reason = "vector_not_configured"
    else:
        fallback_reason = ""

    return {
        "query": query,
        "collection_name": collection_name,
        "collection_metadata": collection_metadata,
        "model_matches_active_index": model_matches,
        "fallback_reason": fallback_reason,
        "mode": mode,
        "fallback": mode in {"keyword", "live_database"},
        "vector_attempted": vector_future is not None,
        "vector_error_type": vector_error_type,
        "vector_error": vector_error,
        "fusion_method": "rrf",
        "rerank_applied": rerank_applied,
        "rerank_fallback_reason": "" if rerank_applied else "reranker_failed" if reranker.available else "reranker_not_configured",
        "retrieval_counts": {
            "vector": len(vector_evidence),
            "keyword": len(keyword_evidence),
            "fused": len(fused_evidence),
        },
        "deduplication": {
            "before": len(fused_evidence),
            "after": len(deduplicated_evidence),
            "removed": len(fused_evidence) - len(deduplicated_evidence),
            "rerank_candidates": len(rerank_candidates),
        },
        "top_k": top_k,
        "evidence": _normalize_evidence(evidence[:top_k]),
    }


def _rrf_fuse(vector_evidence: list[dict], keyword_evidence: list[dict], rrf_k: int = RRF_K) -> list[dict]:
    """按文档 ID 去重并用倒数排名融合两路结果。"""
    fused = {}
    for source, items in (("vector", vector_evidence), ("keyword", keyword_evidence)):
        for rank, item in enumerate(items, start=1):
            metadata = item.get("metadata") or {}
            key = metadata.get("doc_id") or f"content:{item.get('content', '')}"
            if key not in fused:
                fused[key] = {**item, "metadata": metadata, "rrf_score": 0.0}
            fused[key]["rrf_score"] += 1.0 / (rrf_k + rank)
            fused[key][f"{source}_rank"] = rank

    result = sorted(
        fused.values(),
        key=lambda item: (-item["rrf_score"], str((item.get("metadata") or {}).get("doc_id", ""))),
    )
    for rank, item in enumerate(result, start=1):
        item["rrf_score"] = round(item["rrf_score"], 8)
        item["rank"] = rank
    return result


def _deduplicate_evidence(evidence: list[dict]) -> list[dict]:
    """按文档 ID 和标准化正文去重，保留 RRF 排名最高的条目。"""
    result = []
    seen_doc_ids = set()
    seen_contents = set()
    for item in evidence:
        metadata = item.get("metadata") or {}
        doc_id = str(item.get("doc_id") or metadata.get("doc_id") or "").strip()
        content = unicodedata.normalize(
            "NFKC",
            str(item.get("content") or ""),
        ).casefold()
        content_key = " ".join(content.split())
        if doc_id and doc_id in seen_doc_ids:
            continue
        if content_key and content_key in seen_contents:
            continue
        if doc_id:
            seen_doc_ids.add(doc_id)
        if content_key:
            seen_contents.add(content_key)
        result.append(item)
    return result


def evidence_doc_ids(evidence: list[dict]) -> list[str]:
    """按出现顺序提取并去重证据 ID。"""
    ids = []
    for item in evidence:
        doc_id = (item.get("metadata") or {}).get("doc_id")
        if doc_id and doc_id not in ids:
            ids.append(doc_id)
    return ids


def _normalize_evidence(evidence: list[dict]) -> list[dict]:
    """统一不同检索后端的字段，并按 doc_id 去重。"""
    result = []
    for idx, item in enumerate(evidence, start=1):
        metadata = item.get("metadata") or {}
        full_content = str(item.get("full_content") or item.get("content") or "")
        result.append({
            "doc_id": metadata.get("doc_id", ""),
            "source_type": metadata.get("source_type", ""),
            "content": full_content,
            "full_content": full_content,
            "metadata": metadata,
            "similarity": item.get("similarity", 0),
            "rank": item.get("rank", idx),
            "source_label": item.get("source_label", ""),
            "rrf_score": item.get("rrf_score", 0),
            "rerank_score": item.get("rerank_score"),
            "vector_rank": item.get("vector_rank"),
            "keyword_rank": item.get("keyword_rank"),
        })
    return result


def _live_database_evidence(query: str, anime_id: int | None = None, top_k: int = 6) -> list[dict]:
    """绕过索引直接读取业务表，生成最后一层基础证据。"""
    filters = [Comment.content != ""]
    if anime_id is not None:
        filters.append(Comment.anime_id == anime_id)
    terms = query_terms(query)
    if terms:
        filters.append(or_(*(Comment.content.like(f"%{term}%") for term in terms[:8])))
    with orm_session() as session:
        rows = session.execute(
            select(
                Comment.id,
                Comment.anime_id,
                Anime.name.label("anime_name"),
                Comment.content,
                Comment.sentiment_label,
                Comment.sentiment_score,
                Comment.likes,
                Comment.platform,
                Comment.publish_time,
            )
            .join(Anime, Anime.id == Comment.anime_id)
            .where(*filters)
            .order_by(Comment.likes.desc(), Comment.id)
            .limit(top_k)
        ).mappings().all()

    evidence = []
    for rank, row in enumerate(rows, start=1):
        metadata = {
            "doc_id": f"anime:{row['anime_id']}:comment:{row['id']}",
            "source_type": "comment",
            "anime_id": row["anime_id"],
            "anime_name": row["anime_name"],
            "comment_id": row["id"],
            "sentiment_label": row["sentiment_label"] or "",
            "sentiment_score": row["sentiment_score"] or 0,
            "likes": row["likes"] or 0,
            "platform": row["platform"] or "",
            "publish_time": str(row["publish_time"] or ""),
        }
        evidence.append({
            "content": row["content"],
            "metadata": metadata,
            "similarity": 0.42,
            "rank": rank,
            "source_label": f"{row['anime_name']} comment #{row['id']}",
        })

    if not evidence and anime_id is not None and terms:
        return _live_database_evidence("", anime_id=anime_id, top_k=top_k)
    if not evidence and anime_id is not None:
        for rank, topic in enumerate(get_topics(anime_id)[:top_k], start=1):
            keywords = topic.get("keywords") or []
            words = [w.get("word", "") for w in keywords if isinstance(w, dict)]
            metadata = {
                "doc_id": f"anime:{anime_id}:topic:{topic.get('topic_id')}",
                "source_type": "topic",
                "anime_id": anime_id,
                "anime_name": "",
                "comment_id": None,
                "sentiment_label": "",
                "platform": "",
                "publish_time": "",
            }
            evidence.append({
                "content": " / ".join(words),
                "metadata": metadata,
                "similarity": 0.35,
                "rank": rank,
                "source_label": f"topic #{topic.get('topic_id')}",
            })
    if not evidence and anime_id is not None:
        stats = get_sentiment_stats(anime_id)
        if stats.get("total"):
            metadata = {"doc_id": f"anime:{anime_id}:sentiment_summary", "source_type": "sentiment_summary", "anime_id": anime_id, "anime_name": ""}
            evidence.append({"content": json.dumps(stats, ensure_ascii=False), "metadata": metadata, "similarity": .3, "rank": 1, "source_label": "sentiment summary"})
    return evidence
