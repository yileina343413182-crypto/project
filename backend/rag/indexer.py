# -*- coding: utf-8 -*-
"""把动漫、情感统计、主题和评论转换为可检索 RAG 文档并构建索引。"""

from __future__ import annotations

import json
import logging
import time
from typing import Iterable
from sqlalchemy import select

from backend.database import get_all_anime, get_aspect_sentiment, get_sentiment_stats, get_sentiment_trend, get_topics, orm_session
from backend.db.models import Comment
from backend.rag.embeddings import EMBEDDING_MODEL, EMBEDDING_PROVIDER, EmbeddingClient, stable_content_hash
from backend.rag.storage import set_active_collection, set_collection_metadata, update_index_job, upsert_documents
from backend.rag.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)


def make_collection_name(prefix: str = "rag") -> str:
    """生成带时间戳的新集合名，避免重建时覆盖当前活动索引。"""
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"


def _doc(doc_id: str, source_type: str, content: str, metadata: dict) -> dict:
    """统一文档结构，并附加稳定内容哈希用于幂等更新。"""
    metadata = {
        "doc_id": doc_id,
        "source_type": source_type,
        "anime_id": metadata.get("anime_id"),
        "anime_name": metadata.get("anime_name", ""),
        "comment_id": metadata.get("comment_id"),
        "sentiment_label": metadata.get("sentiment_label", ""),
        "platform": metadata.get("platform", ""),
        "publish_time": metadata.get("publish_time", ""),
        **metadata,
    }
    return {
        "doc_id": doc_id,
        "source_type": source_type,
        "content": content,
        "metadata": metadata,
        "content_hash": stable_content_hash(content),
    }


def build_documents(anime_id: int | None = None, comment_limit_per_anime: int = 800) -> list[dict]:
    """构建动漫描述、统计、主题及受限数量评论的文档集合。"""
    anime_items = get_all_anime()
    if anime_id is not None:
        anime_items = [item for item in anime_items if int(item["id"]) == int(anime_id)]

    docs: list[dict] = []
    for anime in anime_items:
        aid = int(anime["id"])
        anime_name = anime.get("name", "")
        base = {
            "anime_id": aid,
            "anime_name": anime_name,
            "platform": anime.get("platform", ""),
        }
        docs.append(_doc(
            f"anime:{aid}:profile",
            "anime_profile",
            f"{anime_name} platform={anime.get('platform', '')} comments={anime.get('comment_count', 0)}",
            base,
        ))

        stats = get_sentiment_stats(aid)
        trend = get_sentiment_trend(aid)[:30]
        aspect = get_aspect_sentiment(aid)
        docs.append(_doc(
            f"anime:{aid}:sentiment_summary",
            "sentiment_summary",
            "Sentiment stats: "
            + json.dumps(stats, ensure_ascii=False)
            + " Trend: "
            + json.dumps(trend, ensure_ascii=False)
            + " Aspect: "
            + json.dumps(aspect, ensure_ascii=False),
            base,
        ))

        for topic in get_topics(aid):
            keywords = topic.get("keywords") or []
            words = [item.get("word", "") for item in keywords if isinstance(item, dict)]
            docs.append(_doc(
                f"anime:{aid}:topic:{topic.get('topic_id')}",
                "topic",
                f"{anime_name} topic {topic.get('topic_id')}: {' / '.join(words)}",
                {**base, "topic_id": topic.get("topic_id"), "weight": topic.get("weight")},
            ))

        docs.extend(_comment_documents(aid, anime_name, anime.get("platform", ""), comment_limit_per_anime))
    return docs


def _comment_documents(anime_id: int, anime_name: str, platform: str, limit: int) -> Iterable[dict]:
    """流式读取指定动漫评论，避免一次加载整张评论表。"""
    with orm_session() as session:
        rows = session.execute(
            select(
                Comment.id,
                Comment.content,
                Comment.sentiment_label,
                Comment.sentiment_score,
                Comment.likes,
                Comment.platform,
                Comment.publish_time,
            )
            .where(Comment.anime_id == anime_id, Comment.content != "")
            .order_by(Comment.likes.desc(), Comment.id)
            .limit(limit)
        ).mappings().all()
    for row in rows:
        metadata = {
            "anime_id": anime_id,
            "anime_name": anime_name,
            "comment_id": row["id"],
            "sentiment_label": row["sentiment_label"] or "",
            "sentiment_score": row["sentiment_score"] or 0,
            "likes": row["likes"] or 0,
            "platform": row["platform"] or platform or "",
            "publish_time": str(row["publish_time"] or ""),
        }
        yield _doc(
            f"anime:{anime_id}:comment:{row['id']}",
            "comment",
            str(row["content"]),
            metadata,
        )


def run_index_job(job_id: int, collection_name: str, anime_id: int | None = None, activate: bool = True) -> dict:
    """完成文档构建、数据库 upsert、向量写入和可选活动集合切换。"""
    update_index_job(job_id, status="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"), current_step="building_documents", progress=8)
    # 先写可重建的数据库文档副本，再尝试向量化；向量不可用时仍可关键词检索。
    docs = build_documents(anime_id=anime_id)
    total = len(docs)
    update_index_job(job_id, total_docs=total, current_step="saving_sqlite_documents", progress=25)
    upsert_documents(collection_name, docs)

    update_index_job(job_id, current_step="upserting_chroma", indexed_docs=total, progress=65)
    store = ChromaVectorStore()
    embedding_client = EmbeddingClient()
    chroma_count = store.upsert(collection_name, docs, embedding_client)
    verified = False
    if chroma_count == total and docs:
        sample = store.query(collection_name, docs[0]["content"], top_k=1, embedding_client=embedding_client)
        verified = bool(sample)
        set_collection_metadata(collection_name, EMBEDDING_PROVIDER, EMBEDDING_MODEL, store.last_embedding_dimension, chroma_count)
    if activate and verified:
        set_active_collection(collection_name)

    mode = "chroma" if verified else "sqlite_keyword_fallback"
    update_index_job(
        job_id,
        status="succeeded",
        indexed_docs=total,
        progress=100,
        current_step="completed",
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    return {
        "collection_name": collection_name,
        "total_docs": total,
        "indexed_docs": total,
        "chroma_docs": chroma_count,
        "verified": verified,
        "mode": mode,
    }
