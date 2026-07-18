# -*- coding: utf-8 -*-
"""RAG indexing, retrieval debug, and evaluation APIs."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

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

logger = logging.getLogger(__name__)
rag_bp = Blueprint("rag", __name__, url_prefix="/api/rag")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-index")


def _ok(data=None, msg="success"):
    return jsonify({"code": 200, "msg": msg, "data": data})


def _err(msg, code=400):
    return jsonify({"code": code, "msg": msg, "data": None}), code


@rag_bp.before_request
def _ensure_tables():
    init_rag_tables()


def _submit_index_job(job_id: int, collection_name: str, anime_id: int | None = None, activate: bool = True) -> None:
    _executor.submit(_run_index_worker, job_id, collection_name, anime_id, activate)


def _run_index_worker(job_id: int, collection_name: str, anime_id: int | None, activate: bool) -> None:
    try:
        run_index_job(job_id, collection_name, anime_id=anime_id, activate=activate)
    except Exception as exc:  # pragma: no cover
        logger.exception("RAG index job %s failed", job_id)
        update_index_job(job_id, status="failed", error=str(exc), progress=100, current_step="failed")


@rag_bp.route("/index/rebuild", methods=["POST"])
@jwt_required()
def rebuild_index():
    collection_name = make_collection_name()
    job_id = create_index_job("rebuild", collection_name)
    _submit_index_job(job_id, collection_name, activate=True)
    return _ok({"job_id": job_id, "collection_name": collection_name, "status": "queued"})


@rag_bp.route("/index/anime/<int:anime_id>", methods=["POST"])
@jwt_required()
def index_anime(anime_id):
    collection_name = get_active_collection() or make_collection_name("rag_partial")
    job_id = create_index_job("anime", collection_name, anime_id=anime_id)
    _submit_index_job(job_id, collection_name, anime_id=anime_id, activate=True)
    return _ok({"job_id": job_id, "collection_name": collection_name, "anime_id": anime_id, "status": "queued"})


@rag_bp.route("/index/jobs/<int:job_id>", methods=["GET"])
@jwt_required()
def index_job(job_id):
    job = get_index_job(job_id)
    if not job:
        return _err("index job not found", 404)
    return _ok(job)


@rag_bp.route("/index/status", methods=["GET"])
@jwt_required()
def index_status():
    active = get_active_collection()
    metadata = get_collection_metadata(active)
    embedding = embedding_status()
    return _ok({
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


@rag_bp.route("/search", methods=["POST"])
@jwt_required()
def search():
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    anime_id = body.get("anime_id")
    top_k = int(body.get("top_k") or 6)
    if not query:
        return _err("query cannot be empty")
    return _ok(search_evidence(query, anime_id=anime_id, top_k=top_k))


@rag_bp.route("/eval/run", methods=["POST"])
@jwt_required()
def eval_run():
    body = request.get_json(silent=True) or {}
    top_k = int(body.get("top_k") or 5)
    return _ok(run_builtin_eval(top_k=top_k))


@rag_bp.route("/eval/runs", methods=["GET"])
@jwt_required()
def eval_runs():
    return _ok(list_eval_runs())


@rag_bp.route("/eval/runs/<int:run_id>", methods=["GET"])
@jwt_required()
def eval_run_detail(run_id):
    run = get_eval_run(run_id)
    if not run:
        return _err("eval run not found", 404)
    return _ok(run)
