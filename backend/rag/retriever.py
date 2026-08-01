# -*- coding: utf-8 -*-
"""Hybrid RAG retriever with Chroma-first and SQLite keyword fallback."""

from __future__ import annotations
import json
from sqlalchemy import or_, select

from backend.database import get_sentiment_stats, get_topics, orm_session
from backend.db.models import Anime, Comment
from backend.rag.embeddings import EMBEDDING_MODEL, EMBEDDING_PROVIDER, EmbeddingClient
from backend.rag.storage import get_active_collection, get_collection_metadata, keyword_search_documents, query_terms
from backend.rag.vector_store import ChromaVectorStore


def search_evidence(query: str, anime_id: int | None = None, top_k: int = 6) -> dict:
    collection_name = get_active_collection()
    embedding_client = EmbeddingClient()
    mode = "chroma"
    evidence = []
    collection_metadata = get_collection_metadata(collection_name)
    model_matches = bool(collection_metadata and collection_metadata.get("embedding_provider") == EMBEDDING_PROVIDER and collection_metadata.get("embedding_model") == EMBEDDING_MODEL)

    if collection_name and embedding_client.available and model_matches:
        evidence = ChromaVectorStore().query(collection_name, query, top_k=top_k, anime_id=anime_id, embedding_client=embedding_client)

    if not evidence:
        mode = "keyword"
        evidence = keyword_search_documents(query, collection_name, anime_id=anime_id, top_k=top_k)

    if not evidence:
        mode = "live_database"
        evidence = _live_database_evidence(query, anime_id=anime_id, top_k=top_k)

    return {
        "query": query,
        "collection_name": collection_name,
        "collection_metadata": collection_metadata,
        "model_matches_active_index": model_matches,
        "fallback_reason": "" if model_matches else "embedding_model_mismatch",
        "mode": mode,
        "fallback": mode != "chroma",
        "top_k": top_k,
        "evidence": _normalize_evidence(evidence[:top_k]),
    }


def evidence_doc_ids(evidence: list[dict]) -> list[str]:
    ids = []
    for item in evidence:
        doc_id = (item.get("metadata") or {}).get("doc_id")
        if doc_id and doc_id not in ids:
            ids.append(doc_id)
    return ids


def _normalize_evidence(evidence: list[dict]) -> list[dict]:
    result = []
    for idx, item in enumerate(evidence, start=1):
        metadata = item.get("metadata") or {}
        result.append({
            "doc_id": metadata.get("doc_id", ""),
            "source_type": metadata.get("source_type", ""),
            "content": item.get("content", ""),
            "metadata": metadata,
            "similarity": item.get("similarity", 0),
            "rank": item.get("rank", idx),
            "source_label": item.get("source_label", ""),
        })
    return result


def _live_database_evidence(query: str, anime_id: int | None = None, top_k: int = 6) -> list[dict]:
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
