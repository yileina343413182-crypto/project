# -*- coding: utf-8 -*-
"""RAG indexing, retrieval debug, and evaluation APIs."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Body, Depends

from backend.api.common import error_response, ok
from backend.rag.embeddings import embedding_status
from backend.rag.evaluator import run_builtin_eval
from backend.rag.indexer import make_collection_name, run_index_job
from backend.rag.retriever import search_evidence
from backend.rag.storage import (
    count_documents,
    create_index_job,
    get_active_collection,
    get_collection_metadata,
    get_eval_run,
    get_index_job,
    init_rag_tables,
    list_eval_runs,
    list_index_jobs,
    update_index_job,
)
from backend.rag.vector_store import chroma_available
from backend.security import get_current_user_id

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-index")


def _ensure_tables():
    init_rag_tables()


router = APIRouter(prefix="/api/rag", dependencies=[Depends(_ensure_tables)])


def _submit_index_job(job_id: int, collection_name: str, anime_id: int | None = None, activate: bool = True) -> None:
    _executor.submit(_run_index_worker, job_id, collection_name, anime_id, activate)


def _run_index_worker(job_id: int, collection_name: str, anime_id: int | None, activate: bool) -> None:
    try:
        run_index_job(job_id, collection_name, anime_id=anime_id, activate=activate)
    except Exception as exc:  # pragma: no cover
        logger.exception("RAG index job %s failed", job_id)
        update_index_job(job_id, status="failed", error=str(exc), progress=100, current_step="failed")


@router.post("/index/rebuild")
def rebuild_index(_user_id: int = Depends(get_current_user_id)):
    collection_name = make_collection_name()
    job_id = create_index_job("rebuild", collection_name)
    _submit_index_job(job_id, collection_name, activate=True)
    return ok({"job_id": job_id, "collection_name": collection_name, "status": "queued"})


@router.post("/index/anime/{anime_id}")
def index_anime(anime_id: int, _user_id: int = Depends(get_current_user_id)):
    collection_name = get_active_collection() or make_collection_name("rag_partial")
    job_id = create_index_job("anime", collection_name, anime_id=anime_id)
    _submit_index_job(job_id, collection_name, anime_id=anime_id, activate=True)
    return ok({"job_id": job_id, "collection_name": collection_name, "anime_id": anime_id, "status": "queued"})


@router.get("/index/jobs/{job_id}")
def index_job(job_id: int, _user_id: int = Depends(get_current_user_id)):
    job = get_index_job(job_id)
    if not job:
        return error_response("index job not found", 404)
    return ok(job)


@router.get("/index/status")
def index_status(_user_id: int = Depends(get_current_user_id)):
    active = get_active_collection()
    metadata = get_collection_metadata(active)
    embedding = embedding_status()
    return ok({
        "active_collection": active,
        "document_count": count_documents(active) if active else 0,
        "recent_jobs": list_index_jobs(),
        "embedding": embedding,
        "embedding_provider": embedding.get("provider"),
        "embedding_model": embedding.get("model"),
        "embedding_dimension": (metadata or {}).get("embedding_dimension", 0),
        "model_matches_active_index": bool(metadata and metadata.get("embedding_provider") == embedding.get("provider") and metadata.get("embedding_model") == embedding.get("model")),
        "active_collection_metadata": metadata,
        "chroma_available": chroma_available(),
    })


@router.post("/search")
def search(
    body: dict | None = Body(default=None),
    _user_id: int = Depends(get_current_user_id),
):
    body = body or {}
    query = (body.get("query") or "").strip()
    anime_id = body.get("anime_id")
    top_k = int(body.get("top_k") or 6)
    if not query:
        return error_response("query cannot be empty")
    return ok(search_evidence(query, anime_id=anime_id, top_k=top_k))


@router.post("/eval/run")
def eval_run(
    body: dict | None = Body(default=None),
    _user_id: int = Depends(get_current_user_id),
):
    body = body or {}
    top_k = int(body.get("top_k") or 5)
    return ok(run_builtin_eval(top_k=top_k))


@router.get("/eval/runs")
def eval_runs(_user_id: int = Depends(get_current_user_id)):
    return ok(list_eval_runs())


@router.get("/eval/runs/{run_id}")
def eval_run_detail(run_id: int, _user_id: int = Depends(get_current_user_id)):
    run = get_eval_run(run_id)
    if not run:
        return error_response("eval run not found", 404)
    return ok(run)
