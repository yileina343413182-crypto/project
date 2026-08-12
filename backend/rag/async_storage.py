"""FastAPI 请求使用的异步 RAG 状态查询与轻量写入。

真正的批量文档 upsert 和索引构建在线程池中使用同步 ``rag.storage``；这里
主要创建任务、读取活动集合和返回评估记录。
"""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    RagActiveCollection,
    RagCollectionMetadata,
    RagDocument,
    RagEvalItem,
    RagEvalRun,
    RagIndexJob,
)


def _value(value, default=None):
    """把日期和 JSON 字符串转换为 API 可返回的 Python 值。"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and default is not None:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value if value is not None else default


def _job(job: RagIndexJob | None) -> dict | None:
    """把索引任务 ORM 对象转换为状态接口结构。"""
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
        "created_at": _value(job.created_at),
        "started_at": _value(job.started_at),
        "finished_at": _value(job.finished_at),
        "updated_at": _value(job.updated_at),
    }


async def create_index_job(
    session: AsyncSession, job_type: str, collection_name: str, anime_id: int | None = None
) -> int:
    """创建 pending 索引任务并取得 ID，构建工作随后在线程池执行。"""
    record = RagIndexJob(job_type=job_type, collection_name=collection_name, anime_id=anime_id)
    session.add(record)
    await session.flush()
    return record.id


async def get_index_job(session: AsyncSession, job_id: int) -> dict | None:
    return _job(await session.get(RagIndexJob, job_id))


async def list_index_jobs(session: AsyncSession, limit: int = 8) -> list[dict]:
    jobs = (
        await session.scalars(select(RagIndexJob).order_by(RagIndexJob.id.desc()).limit(limit))
    ).all()
    return [_job(job) for job in jobs]


async def get_active_collection(session: AsyncSession) -> str | None:
    """读取当前检索入口指向的活动集合名。"""
    return await session.scalar(
        select(RagActiveCollection.collection_name).where(RagActiveCollection.id == 1)
    )


async def count_documents(session: AsyncSession, collection_name: str | None = None) -> int:
    """统计指定集合文档数；未指定时统计活动集合。"""
    statement = select(func.count()).select_from(RagDocument)
    if collection_name:
        statement = statement.where(RagDocument.collection_name == collection_name)
    return int(await session.scalar(statement) or 0)


async def get_collection_metadata(session: AsyncSession, collection_name: str | None) -> dict | None:
    """读取集合使用的 Embedding 模型、维度和构建摘要。"""
    if not collection_name:
        return None
    record = await session.get(RagCollectionMetadata, collection_name)
    if record is None:
        return None
    return {
        "collection_name": record.collection_name,
        "embedding_provider": record.embedding_provider,
        "embedding_model": record.embedding_model,
        "embedding_dimension": record.embedding_dimension,
        "document_count": record.document_count,
        "created_at": _value(record.created_at),
    }


def _run(run: RagEvalRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "metrics": _value(run.metrics, {}),
        "error": run.error,
        "created_at": _value(run.created_at),
        "finished_at": _value(run.finished_at),
    }


async def list_eval_runs(session: AsyncSession, limit: int = 12) -> list[dict]:
    runs = (
        await session.scalars(select(RagEvalRun).order_by(RagEvalRun.id.desc()).limit(limit))
    ).all()
    return [_run(run) for run in runs]


async def get_eval_run(session: AsyncSession, run_id: int) -> dict | None:
    run = await session.get(RagEvalRun, run_id)
    if run is None:
        return None
    items = (
        await session.scalars(
            select(RagEvalItem).where(RagEvalItem.run_id == run_id).order_by(RagEvalItem.id)
        )
    ).all()
    data = _run(run)
    data["items"] = [
        {
            "id": item.id,
            "case_id": item.case_id,
            "query": item.query,
            "passed": bool(item.passed),
            "metrics": _value(item.metrics, {}),
            "evidence": _value(item.evidence, []),
            "error": item.error,
            "created_at": _value(item.created_at),
        }
        for item in items
    ]
    return data
