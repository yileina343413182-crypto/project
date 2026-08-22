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
from backend.rag.knowledge import load_knowledge_records
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


def _text_list(value) -> list[str]:
    if not isinstance(value, list):
        value = [value]
    return [str(item).strip() for item in value if str(item or "").strip()]


def _knowledge_documents(anime: dict, record: dict | None) -> list[dict]:
    """把有来源的知识记录转成三类文档；缺失平台必须显式标记未验证。"""
    aid = int(anime["id"])
    anime_name = anime.get("name", "")
    record = record if isinstance(record, dict) else {}
    docs: list[dict] = []
    knowledge = record.get("knowledge") or {}
    if isinstance(knowledge, dict):
        source = str(knowledge.get("source") or "").strip()
        summary = str(knowledge.get("summary") or "").strip()
        genres = _text_list(knowledge.get("genres"))
        moods = _text_list(knowledge.get("moods"))
        character_types = _text_list(knowledge.get("character_types"))
        studio = str(knowledge.get("studio") or "未验证").strip()
        year = str(knowledge.get("year") or "未验证").strip()
        episodes = str(knowledge.get("episodes") or "未验证").strip()
        work_type = str(knowledge.get("work_type") or "未验证").strip()
        updated_at = str(knowledge.get("updated_at") or "未提供").strip()
        if source and (summary or genres or moods or character_types):
            docs.append(_doc(
                f"anime:{aid}:knowledge",
                "anime_knowledge",
                (
                    f"《{anime_name}》无剧透简介：{summary or '未验证'}\n"
                    f"题材标签：{'、'.join(genres) or '未验证'}\n"
                    f"氛围标签：{'、'.join(moods) or '未验证'}\n"
                    f"角色类型：{'、'.join(character_types) or '未验证'}\n"
                    f"制作公司：{studio}\n年代：{year}\n集数：{episodes}\n"
                    f"作品类型：{work_type}\n数据来源：{source}\n数据更新时间：{updated_at}"
                ),
                {
                    "anime_id": aid,
                    "anime_name": anime_name,
                    "data_source": source,
                    "data_updated_at": updated_at,
                    "verification_status": "verified",
                },
            ))

    relations = record.get("relations") or []
    relation_lines = []
    relation_sources = []
    relation_updated = []
    for relation in relations if isinstance(relations, list) else []:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source") or "").strip()
        related_name = str(relation.get("related_name") or "").strip()
        relation_type = str(relation.get("relation_type") or "关联作品").strip()
        if not source or not related_name:
            continue
        relation_lines.append(f"{relation_type}：《{related_name}》")
        relation_sources.append(source)
        relation_updated.append(str(relation.get("updated_at") or "未提供").strip())
    if relation_lines:
        docs.append(_doc(
            f"anime:{aid}:relations",
            "anime_relation",
            (
                f"《{anime_name}》有来源依据的作品关系：{'；'.join(relation_lines)}。\n"
                f"数据来源：{'；'.join(dict.fromkeys(relation_sources))}\n"
                f"数据更新时间：{'；'.join(dict.fromkeys(relation_updated))}"
            ),
            {
                "anime_id": aid,
                "anime_name": anime_name,
                "data_source": ";".join(dict.fromkeys(relation_sources)),
                "data_updated_at": ";".join(dict.fromkeys(relation_updated)),
                "verification_status": "verified",
            },
        ))

    availability = record.get("platform_availability") or []
    availability_lines = []
    verified_platforms = []
    availability_sources = []
    availability_updated = []
    for item in availability if isinstance(availability, list) else []:
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "未验证").strip()
        region = str(item.get("region") or "未验证").strip()
        status = str(item.get("status") or "unverified").strip().lower()
        source = str(item.get("source") or "未提供").strip()
        updated_at = str(item.get("updated_at") or "未提供").strip()
        availability_lines.append(
            f"地区={region}，观看平台={platform}，验证状态={status}，来源={source}，更新时间={updated_at}"
        )
        if status == "verified" and platform and platform != "未验证":
            verified_platforms.append(platform)
        availability_sources.append(source)
        availability_updated.append(updated_at)

    if not availability_lines:
        collection_source = str(anime.get("platform") or "未提供").strip()
        availability_lines.append(
            "地区=未验证，观看平台=未验证，验证状态=unverified，"
            f"来源=项目评论采集来源字段({collection_source})，更新时间=未提供；"
            "Bangumi/Bilibili 在此仅表示数据来源，不代表实际播放平台"
        )
        availability_sources.append(f"collection_source:{collection_source}")
        availability_updated.append("未提供")
    verification_status = "verified" if verified_platforms else "unverified"
    docs.append(_doc(
        f"anime:{aid}:platform_availability",
        "platform_availability",
        f"《{anime_name}》播放平台可用性：\n" + "\n".join(availability_lines),
        {
            "anime_id": aid,
            "anime_name": anime_name,
            "data_source": ";".join(dict.fromkeys(availability_sources)),
            "data_updated_at": ";".join(dict.fromkeys(availability_updated)),
            "verification_status": verification_status,
            "viewing_platform": ";".join(dict.fromkeys(verified_platforms)),
        },
    ))
    return docs


def build_documents(anime_id: int | None = None, comment_limit_per_anime: int = 800) -> list[dict]:
    """构建动漫描述、统计、主题及受限数量评论的文档集合。"""
    anime_items = get_all_anime()
    if anime_id is not None:
        anime_items = [item for item in anime_items if int(item["id"]) == int(anime_id)]

    knowledge_records = load_knowledge_records()
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
            f"{anime_name} collection_source={anime.get('platform', '')} comments={anime.get('comment_count', 0)}",
            base,
        ))
        docs.extend(_knowledge_documents(anime, knowledge_records.get(aid)))

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

    update_index_job(job_id, current_step="upserting_chroma", indexed_docs=0, progress=65)
    store = ChromaVectorStore()
    embedding_client = EmbeddingClient()
    chroma_count = store.upsert(collection_name, docs, embedding_client)
    verified = False
    verification_error = ""
    index_status = store.persisted_index_status(collection_name)
    if chroma_count == total and docs:
        if not index_status.get("index_files_complete"):
            verification_error = index_status.get("error") or "persisted HNSW files are incomplete"
        else:
            try:
                sample = store.query(
                    collection_name,
                    docs[0]["content"],
                    top_k=1,
                    embedding_client=embedding_client,
                )
                verified = bool(sample)
                if not verified:
                    verification_error = "fresh vector query returned no evidence"
            except Exception as exc:
                verification_error = f"{type(exc).__name__}: {str(exc)[:300]}"
    elif embedding_client.available and docs:
        verification_error = f"vector count mismatch: expected={total}, actual={chroma_count}"

    if verified:
        set_collection_metadata(
            collection_name,
            EMBEDDING_PROVIDER,
            EMBEDDING_MODEL,
            store.last_embedding_dimension,
            chroma_count,
        )
    if activate and verified:
        set_active_collection(collection_name)

    mode = "chroma" if verified else "sqlite_keyword_fallback"
    status = "failed" if embedding_client.available and docs and not verified else "succeeded"
    update_index_job(
        job_id,
        status=status,
        indexed_docs=chroma_count,
        progress=100,
        current_step="completed" if verified else "vector_verification_failed",
        error=verification_error or None,
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    return {
        "status": status,
        "collection_name": collection_name,
        "total_docs": total,
        "indexed_docs": chroma_count,
        "chroma_docs": chroma_count,
        "verified": verified,
        "activated": bool(activate and verified),
        "verification_error": verification_error,
        "index_status": index_status,
        "mode": mode,
    }
