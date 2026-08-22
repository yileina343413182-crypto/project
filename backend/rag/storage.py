# -*- coding: utf-8 -*-
"""RAG 索引任务、文档、活动集合与评估记录的同步持久化层。

索引后台线程使用这里批量写入；检索在 Chroma 无结果时也会查询业务数据库
中的 ``rag_documents``，因此向量索引损坏不会直接造成证据完全不可用。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.database import orm_session
from backend.db.models import (
    RagActiveCollection,
    RagCollectionMetadata,
    RagDocument,
    RagEvalCase,
    RagEvalItem,
    RagEvalRun,
    RagIndexJob,
)
from backend.db.session import get_sync_engine

try:
    import jieba
except Exception:
    jieba = None

_LOW_INFO_TERMS = {"推荐", "动漫", "动画", "想看", "有没有", "一部", "一些", "什么", "可以", "比较", "喜欢"}


def query_terms(query: str) -> list[str]:
    """对查询分词并去重，得到关键词降级检索使用的少量词项。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (query or "").lower())
    raw = list(jieba.cut(cleaned)) if jieba else cleaned.split()
    result = []
    for term in raw:
        term = term.strip()
        if len(term) > 1 and term not in _LOW_INFO_TERMS and not term.isdigit() and term not in result:
            result.append(term)
    return result[:20]


def _date_value(value):
    """把日期转换为可序列化字符串。"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _json_value(value, default):
    """兼容 JSON 列对象与旧数据库中的 JSON 字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def init_rag_tables(db_path=None) -> None:
    """以 checkfirst 模式补建全部 RAG 业务表。"""
    engine = get_sync_engine(db_path=db_path) if db_path else get_sync_engine()
    RagIndexJob.metadata.create_all(
        engine,
        tables=[
            RagIndexJob.__table__,
            RagDocument.__table__,
            RagActiveCollection.__table__,
            RagCollectionMetadata.__table__,
            RagEvalCase.__table__,
            RagEvalRun.__table__,
            RagEvalItem.__table__,
        ],
        checkfirst=True,
    )


# ===== 索引任务与活动集合 =====

def _job_dict(job: RagIndexJob | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "collection_name": job.collection_name,
        "anime_id": job.anime_id,
        "total_docs": job.total_docs,
        "indexed_docs": job.indexed_docs,
        "progress": job.progress,
        "current_step": job.current_step,
        "error": job.error,
        "created_at": _date_value(job.created_at),
        "started_at": _date_value(job.started_at),
        "finished_at": _date_value(job.finished_at),
        "updated_at": _date_value(job.updated_at),
    }


def create_index_job(job_type: str, collection_name: str, anime_id: int | None = None) -> int:
    with orm_session() as session:
        job = RagIndexJob(job_type=job_type, collection_name=collection_name, anime_id=anime_id)
        session.add(job)
        session.flush()
        return job.id


def update_index_job(job_id: int, **fields) -> None:
    allowed = {
        "status", "total_docs", "indexed_docs", "progress", "current_step",
        "error", "started_at", "finished_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = datetime.now()
    with orm_session() as session:
        session.execute(update(RagIndexJob).where(RagIndexJob.id == job_id).values(**values))


def get_index_job(job_id: int) -> dict | None:
    with orm_session() as session:
        return _job_dict(session.get(RagIndexJob, job_id))


def list_index_jobs(limit: int = 8) -> list[dict]:
    with orm_session() as session:
        jobs = session.scalars(
            select(RagIndexJob).order_by(RagIndexJob.id.desc()).limit(limit)
        ).all()
        return [_job_dict(job) for job in jobs]


def _execute_upsert(session, model, values: dict, conflict_columns: list, update_columns: list):
    """按 MySQL/SQLite 方言构造等价 upsert，保持同一业务语义。"""
    if session.bind.dialect.name == "mysql":
        statement = mysql_insert(model).values(**values)
        session.execute(
            statement.on_duplicate_key_update(
                **{column: getattr(statement.inserted, column) for column in update_columns}
            )
        )
    else:
        statement = sqlite_insert(model).values(**values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=conflict_columns,
                set_={column: getattr(statement.excluded, column) for column in update_columns},
            )
        )


def set_active_collection(collection_name: str) -> None:
    """原子更新唯一活动集合指针，后续检索立即切换。"""
    now = datetime.now()
    with orm_session() as session:
        _execute_upsert(
            session,
            RagActiveCollection,
            {"id": 1, "collection_name": collection_name, "updated_at": now},
            [RagActiveCollection.id],
            ["collection_name", "updated_at"],
        )


def get_active_collection() -> str | None:
    with orm_session() as session:
        return session.scalar(
            select(RagActiveCollection.collection_name).where(RagActiveCollection.id == 1)
        )


def set_collection_metadata(
    collection_name: str,
    provider: str,
    model: str,
    dimension: int,
    document_count: int,
) -> None:
    """保存集合使用的 Embedding 配置和构建统计。"""
    with orm_session() as session:
        _execute_upsert(
            session,
            RagCollectionMetadata,
            {
                "collection_name": collection_name,
                "embedding_provider": provider,
                "embedding_model": model,
                "embedding_dimension": dimension,
                "document_count": document_count,
            },
            [RagCollectionMetadata.collection_name],
            ["embedding_provider", "embedding_model", "embedding_dimension", "document_count"],
        )


def get_collection_metadata(collection_name: str | None) -> dict | None:
    if not collection_name:
        return None
    with orm_session() as session:
        record = session.get(RagCollectionMetadata, collection_name)
        if record is None:
            return None
        return {
            "collection_name": record.collection_name,
            "embedding_provider": record.embedding_provider,
            "embedding_model": record.embedding_model,
            "embedding_dimension": record.embedding_dimension,
            "document_count": record.document_count,
            "created_at": _date_value(record.created_at),
        }


# ===== 可重建的 RAG 文档副本与关键词检索 =====

def upsert_documents(collection_name: str, docs: list[dict]) -> None:
    """按文档 ID 幂等写入内容、元数据与稳定内容哈希。"""
    if not docs:
        return
    now = datetime.now()
    values = [
        {
            "collection_name": collection_name,
            "doc_id": doc["doc_id"],
            "source_type": doc["source_type"],
            "anime_id": doc["metadata"].get("anime_id"),
            "anime_name": doc["metadata"].get("anime_name", ""),
            "comment_id": doc["metadata"].get("comment_id"),
            "content": doc["content"],
            "metadata": doc["metadata"],
            "content_hash": doc["content_hash"],
            "updated_at": now,
        }
        for doc in docs
    ]
    update_columns = (
        "source_type", "anime_id", "anime_name", "comment_id", "content",
        "metadata", "content_hash", "updated_at",
    )
    with orm_session() as session:
        table = RagDocument.__table__
        if session.bind.dialect.name == "mysql":
            statement = mysql_insert(table).values(values)
            session.execute(
                statement.on_duplicate_key_update(
                    **{column: statement.inserted[column] for column in update_columns}
                )
            )
        else:
            statement = sqlite_insert(table).values(values)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[table.c.collection_name, table.c.doc_id],
                    set_={column: statement.excluded[column] for column in update_columns},
                )
            )


def count_documents(collection_name: str | None = None) -> int:
    statement = select(func.count()).select_from(RagDocument)
    if collection_name:
        statement = statement.where(RagDocument.collection_name == collection_name)
    with orm_session() as session:
        return int(session.scalar(statement) or 0)


def get_anime_documents(
    anime_id: int,
    source_types: set[str] | tuple[str, ...],
    collection_name: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """按作品和来源类型读取活动集合中的结构化文档。"""
    collection_name = collection_name or get_active_collection()
    cleaned_types = {str(value).strip() for value in source_types if str(value).strip()}
    if not collection_name or not cleaned_types or limit <= 0:
        return []
    with orm_session() as session:
        rows = session.scalars(
            select(RagDocument)
            .where(
                RagDocument.collection_name == collection_name,
                RagDocument.anime_id == int(anime_id),
                RagDocument.source_type.in_(cleaned_types),
            )
            .order_by(RagDocument.updated_at.desc(), RagDocument.id)
            .limit(limit)
        ).all()

    result = []
    for rank, row in enumerate(rows, start=1):
        metadata = _json_value(row.document_metadata, {})
        metadata.setdefault("doc_id", row.doc_id)
        metadata.setdefault("anime_id", row.anime_id)
        metadata.setdefault("anime_name", row.anime_name or "")
        metadata.setdefault("source_type", row.source_type)
        result.append({
            "doc_id": row.doc_id,
            "content": row.content,
            "full_content": row.content,
            "metadata": metadata,
            "similarity": 1.0,
            "rank": rank,
            "source_type": row.source_type,
            "source_label": _source_label(metadata),
        })
    return result


def keyword_search_documents(
    query: str,
    collection_name: str | None,
    anime_id: int | None = None,
    top_k: int = 6,
) -> list[dict]:
    """在数据库文档中做关键词匹配，作为 Chroma 检索的第一层降级。"""
    collection_name = collection_name or get_active_collection()
    if not collection_name:
        return []
    filters = [RagDocument.collection_name == collection_name]
    if anime_id is not None:
        filters.append(RagDocument.anime_id == anime_id)
    with orm_session() as session:
        rows = session.scalars(
            select(RagDocument)
            .where(*filters)
            .order_by(RagDocument.updated_at.desc())
            .limit(2000)
        ).all()

    terms = query_terms(query)
    query_lower = query.lower()
    scored = []
    for row in rows:
        content = row.content or ""
        content_lower = content.lower()
        score = 4 if query_lower and query_lower in content_lower else 0
        score += sum(1 for term in terms if term and term in content_lower)
        if not terms and not query_lower:
            score = 1
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], len(item[1].content or "")), reverse=True)

    result = []
    for rank, (score, row) in enumerate(scored[:top_k], start=1):
        metadata = _json_value(row.document_metadata, {})
        result.append(
            {
                "content": row.content,
                "metadata": metadata,
                "similarity": round(min(0.95, 0.45 + score * 0.08), 4),
                "rank": rank,
                "source_label": _source_label(metadata),
            }
        )
    return result


def _source_label(metadata: dict) -> str:
    """把内部来源类型转换为可展示的证据标签。"""
    source_type = metadata.get("source_type", "")
    anime_name = metadata.get("anime_name", "")
    comment_id = metadata.get("comment_id")
    if source_type == "comment" and comment_id:
        return f"{anime_name} comment #{comment_id}"
    return f"{anime_name} {source_type}".strip()


# ===== RAG 评估运行与逐项结果 =====

def create_eval_run() -> int:
    """创建 running 状态的评估批次。"""
    with orm_session() as session:
        run = RagEvalRun(status="running", metrics={})
        session.add(run)
        session.flush()
        return run.id


def finish_eval_run(
    run_id: int,
    metrics: dict,
    status: str = "succeeded",
    error: str | None = None,
) -> None:
    """写入汇总指标并将评估批次推进到终态。"""
    with orm_session() as session:
        session.execute(
            update(RagEvalRun)
            .where(RagEvalRun.id == run_id)
            .values(status=status, metrics=metrics, error=error, finished_at=datetime.now())
        )


def save_eval_item(
    run_id: int,
    query: str,
    passed: bool,
    metrics: dict,
    evidence: list,
    case_id: int | None = None,
    error: str | None = None,
) -> None:
    with orm_session() as session:
        session.add(
            RagEvalItem(
                run_id=run_id,
                case_id=case_id,
                query=query,
                passed=passed,
                metrics=metrics,
                evidence=evidence,
                error=error,
            )
        )


def _eval_run_dict(run: RagEvalRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "metrics": _json_value(run.metrics, {}),
        "error": run.error,
        "created_at": _date_value(run.created_at),
        "finished_at": _date_value(run.finished_at),
    }


def list_eval_runs(limit: int = 12) -> list[dict]:
    with orm_session() as session:
        runs = session.scalars(
            select(RagEvalRun).order_by(RagEvalRun.id.desc()).limit(limit)
        ).all()
        return [_eval_run_dict(run) for run in runs]


def get_eval_run(run_id: int) -> dict | None:
    with orm_session() as session:
        run = session.get(RagEvalRun, run_id)
        if run is None:
            return None
        items = session.scalars(
            select(RagEvalItem)
            .where(RagEvalItem.run_id == run_id)
            .order_by(RagEvalItem.id)
        ).all()
        data = _eval_run_dict(run)
        data["items"] = [
            {
                "id": item.id,
                "case_id": item.case_id,
                "query": item.query,
                "passed": bool(item.passed),
                "metrics": _json_value(item.metrics, {}),
                "evidence": _json_value(item.evidence, []),
                "error": item.error,
                "created_at": _date_value(item.created_at),
            }
            for item in items
        ]
        return data
