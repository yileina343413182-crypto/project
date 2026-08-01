# -*- coding: utf-8 -*-
"""RAG indexing, retrieval debug, and evaluation APIs."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.api.common import error_response, ok
from backend.rag.embeddings import embedding_status
from backend.rag.evaluator import run_builtin_eval
from backend.rag.indexer import make_collection_name, run_index_job
from backend.rag.retriever import search_evidence
from backend.rag.async_storage import (
    count_documents,
    create_index_job,
    get_active_collection,
    get_collection_metadata,
    get_eval_run,
    get_index_job,
    list_eval_runs,
    list_index_jobs,
)
from backend.rag.storage import update_index_job
from backend.rag.vector_store import chroma_available
from backend.db.session import get_async_session
from backend.security import get_current_user_id

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-index")


router = APIRouter(prefix="/api/rag")


def _submit_index_job(job_id: int, collection_name: str, anime_id: int | None = None, activate: bool = True) -> None:
    _executor.submit(_run_index_worker, job_id, collection_name, anime_id, activate)


def _run_index_worker(job_id: int, collection_name: str, anime_id: int | None, activate: bool) -> None:
    try:
        run_index_job(job_id, collection_name, anime_id=anime_id, activate=activate)
    except Exception as exc:  # pragma: no cover
        logger.exception("RAG index job %s failed", job_id)
        update_index_job(job_id, status="failed", error=str(exc), progress=100, current_step="failed")


@router.post("/index/rebuild")
async def rebuild_index(
    _user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    collection_name = make_collection_name()
    job_id = await create_index_job(db, "rebuild", collection_name)
    await db.commit()
    _submit_index_job(job_id, collection_name, activate=True)
    return ok({"job_id": job_id, "collection_name": collection_name, "status": "queued"})


@router.post("/index/anime/{anime_id}")
async def index_anime(
    anime_id: int,
    _user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    collection_name = await get_active_collection(db) or make_collection_name("rag_partial")
    job_id = await create_index_job(db, "anime", collection_name, anime_id=anime_id)
    await db.commit()
    _submit_index_job(job_id, collection_name, anime_id=anime_id, activate=True)
    return ok({"job_id": job_id, "collection_name": collection_name, "anime_id": anime_id, "status": "queued"})


@router.get("/index/jobs/{job_id}")
async def index_job(
    job_id: int,
    _user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    job = await get_index_job(db, job_id)
    if not job:
        return error_response("index job not found", 404)
    return ok(job)


@router.get("/index/status")
async def index_status(
    _user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    active = await get_active_collection(db)
    metadata = await get_collection_metadata(db, active)
    embedding = embedding_status()
    return ok({
        "active_collection": active,
        "document_count": await count_documents(db, active) if active else 0,
        "recent_jobs": await list_index_jobs(db),
        "embedding": embedding,
        "embedding_provider": embedding.get("provider"),
        "embedding_model": embedding.get("model"),
        "embedding_dimension": (metadata or {}).get("embedding_dimension", 0),
        "model_matches_active_index": bool(metadata and metadata.get("embedding_provider") == embedding.get("provider") and metadata.get("embedding_model") == embedding.get("model")),
        "active_collection_metadata": metadata,
        "chroma_available": chroma_available(),
    })


@router.post("/search")
async def search(
    body: dict | None = Body(default=None),
    _user_id: int = Depends(get_current_user_id),
):
    body = body or {}
    query = (body.get("query") or "").strip()
    anime_id = body.get("anime_id")
    top_k = int(body.get("top_k") or 6)
    if not query:
        return error_response("query cannot be empty")
    result = await run_in_threadpool(search_evidence, query, anime_id, top_k)
    return ok(result)


@router.post("/eval/run")
async def eval_run(
    body: dict | None = Body(default=None),
    _user_id: int = Depends(get_current_user_id),
):
    body = body or {}
    top_k = int(body.get("top_k") or 5)
    return ok(await run_in_threadpool(run_builtin_eval, top_k))


@router.get("/eval/runs")
async def eval_runs(
    _user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    return ok(await list_eval_runs(db))


@router.get("/eval/runs/{run_id}")
async def eval_run_detail(
    run_id: int,
    _user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    run = await get_eval_run(db, run_id)
    if not run:
        return error_response("eval run not found", 404)
    return ok(run)
