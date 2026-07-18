# -*- coding: utf-8 -*-
"""SQLite persistence for RAG index jobs, documents, collections, and evals."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.database import get_db
try:
    import jieba
except Exception:
    jieba = None
_LOW_INFO_TERMS = {"推荐", "动漫", "动画", "想看", "有没有", "一部", "一些", "什么", "可以", "比较", "喜欢"}
def query_terms(query: str) -> list[str]:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (query or "").lower())
    raw = list(jieba.cut(cleaned)) if jieba else cleaned.split()
    result = []
    for term in raw:
        term = term.strip()
        if len(term) > 1 and term not in _LOW_INFO_TERMS and not term.isdigit() and term not in result:
            result.append(term)
    return result[:20]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None, default: Any):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def init_rag_tables(db_path=None) -> None:
    conn = get_db(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rag_index_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            collection_name TEXT NOT NULL,
            anime_id INTEGER,
            total_docs INTEGER NOT NULL DEFAULT 0,
            indexed_docs INTEGER NOT NULL DEFAULT 0,
            progress INTEGER NOT NULL DEFAULT 0,
            current_step TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS rag_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_name TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            anime_id INTEGER,
            anime_name TEXT,
            comment_id INTEGER,
            content TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(collection_name, doc_id)
        );

        CREATE TABLE IF NOT EXISTS rag_active_collections (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            collection_name TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS rag_collection_metadata (
            collection_name TEXT PRIMARY KEY,
            embedding_provider TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dimension INTEGER NOT NULL DEFAULT 0,
            document_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS rag_eval_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            query TEXT NOT NULL,
            expected_anime_id INTEGER,
            expected_source_type TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS rag_eval_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'running',
            metrics TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS rag_eval_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            case_id INTEGER,
            query TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            metrics TEXT NOT NULL DEFAULT '{}',
            evidence TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (run_id) REFERENCES rag_eval_runs(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def create_index_job(job_type: str, collection_name: str, anime_id: int | None = None) -> int:
    init_rag_tables()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO rag_index_jobs (job_type, collection_name, anime_id)
           VALUES (?, ?, ?)""",
        (job_type, collection_name, anime_id),
    )
    conn.commit()
    job_id = cur.lastrowid
    conn.close()
    return int(job_id)


def update_index_job(job_id: int, **fields) -> None:
    allowed = {
        "status", "total_docs", "indexed_docs", "progress", "current_step",
        "error", "started_at", "finished_at",
    }
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)
    if not updates:
        return
    updates.append("updated_at = datetime('now', 'localtime')")
    params.append(job_id)
    conn = get_db()
    conn.execute(f"UPDATE rag_index_jobs SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()


def get_index_job(job_id: int) -> dict | None:
    init_rag_tables()
    conn = get_db()
    row = conn.execute(
        """SELECT id, job_type, status, collection_name, anime_id, total_docs, indexed_docs,
                  progress, current_step, error, created_at, started_at, finished_at, updated_at
           FROM rag_index_jobs WHERE id = ?""",
        (job_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_index_jobs(limit: int = 8) -> list[dict]:
    init_rag_tables()
    conn = get_db()
    rows = conn.execute(
        """SELECT id, job_type, status, collection_name, anime_id, total_docs, indexed_docs,
                  progress, current_step, error, created_at, started_at, finished_at, updated_at
           FROM rag_index_jobs ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_active_collection(collection_name: str) -> None:
    init_rag_tables()
    conn = get_db()
    conn.execute(
        """INSERT INTO rag_active_collections (id, collection_name, updated_at)
           VALUES (1, ?, datetime('now', 'localtime'))
           ON CONFLICT(id) DO UPDATE SET
             collection_name = excluded.collection_name,
             updated_at = datetime('now', 'localtime')""",
        (collection_name,),
    )
    conn.commit()
    conn.close()


def get_active_collection() -> str | None:
    init_rag_tables()
    conn = get_db()
    row = conn.execute("SELECT collection_name FROM rag_active_collections WHERE id = 1").fetchone()
    conn.close()
    return row["collection_name"] if row else None


def set_collection_metadata(collection_name: str, provider: str, model: str, dimension: int, document_count: int) -> None:
    init_rag_tables()
    conn = get_db()
    conn.execute("""INSERT INTO rag_collection_metadata
        (collection_name, embedding_provider, embedding_model, embedding_dimension, document_count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(collection_name) DO UPDATE SET embedding_provider=excluded.embedding_provider,
        embedding_model=excluded.embedding_model, embedding_dimension=excluded.embedding_dimension,
        document_count=excluded.document_count""", (collection_name, provider, model, dimension, document_count))
    conn.commit(); conn.close()


def get_collection_metadata(collection_name: str | None) -> dict | None:
    if not collection_name: return None
    init_rag_tables(); conn = get_db()
    row = conn.execute("SELECT collection_name, embedding_provider, embedding_model, embedding_dimension, document_count, created_at FROM rag_collection_metadata WHERE collection_name = ?", (collection_name,)).fetchone()
    conn.close(); return dict(row) if row else None


def upsert_documents(collection_name: str, docs: list[dict]) -> None:
    if not docs:
        return
    init_rag_tables()
    conn = get_db()
    conn.executemany(
        """INSERT INTO rag_documents
           (collection_name, doc_id, source_type, anime_id, anime_name, comment_id, content, metadata, content_hash, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
           ON CONFLICT(collection_name, doc_id) DO UPDATE SET
             source_type = excluded.source_type,
             anime_id = excluded.anime_id,
             anime_name = excluded.anime_name,
             comment_id = excluded.comment_id,
             content = excluded.content,
             metadata = excluded.metadata,
             content_hash = excluded.content_hash,
             updated_at = datetime('now', 'localtime')""",
        [
            (
                collection_name,
                doc["doc_id"],
                doc["source_type"],
                doc["metadata"].get("anime_id"),
                doc["metadata"].get("anime_name", ""),
                doc["metadata"].get("comment_id"),
                doc["content"],
                _dumps(doc["metadata"]),
                doc["content_hash"],
            )
            for doc in docs
        ],
    )
    conn.commit()
    conn.close()


def count_documents(collection_name: str | None = None) -> int:
    init_rag_tables()
    conn = get_db()
    if collection_name:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM rag_documents WHERE collection_name = ?",
            (collection_name,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM rag_documents").fetchone()
    conn.close()
    return int(row["cnt"] if row else 0)


def keyword_search_documents(
    query: str,
    collection_name: str | None,
    anime_id: int | None = None,
    top_k: int = 6,
) -> list[dict]:
    init_rag_tables()
    if not collection_name:
        collection_name = get_active_collection()
    if not collection_name:
        return []

    terms = query_terms(query)
    conn = get_db()
    params: list[Any] = [collection_name]
    where = "collection_name = ?"
    if anime_id is not None:
        where += " AND anime_id = ?"
        params.append(anime_id)
    rows = conn.execute(
        f"""SELECT doc_id, source_type, anime_id, anime_name, comment_id, content, metadata
            FROM rag_documents WHERE {where}
            ORDER BY updated_at DESC LIMIT 2000""",
        params,
    ).fetchall()
    conn.close()

    scored = []
    query_l = query.lower()
    for row in rows:
        content = row["content"] or ""
        content_l = content.lower()
        score = 0
        if query_l and query_l in content_l:
            score += 4
        for term in terms:
            if term and term in content_l:
                score += 1
        if not terms and not query_l:
            score = 1
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], len(item[1]["content"] or "")), reverse=True)

    result = []
    for rank, (score, row) in enumerate(scored[:top_k], start=1):
        metadata = _loads(row["metadata"], {})
        similarity = min(0.95, 0.45 + score * 0.08)
        result.append({
            "content": row["content"],
            "metadata": metadata,
            "similarity": round(similarity, 4),
            "rank": rank,
            "source_label": _source_label(metadata),
        })
    return result


def _source_label(metadata: dict) -> str:
    source_type = metadata.get("source_type", "")
    anime_name = metadata.get("anime_name", "")
    comment_id = metadata.get("comment_id")
    if source_type == "comment" and comment_id:
        return f"{anime_name} comment #{comment_id}"
    return f"{anime_name} {source_type}".strip()


def create_eval_run() -> int:
    init_rag_tables()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO rag_eval_runs (status) VALUES ('running')")
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return int(run_id)


def finish_eval_run(run_id: int, metrics: dict, status: str = "succeeded", error: str | None = None) -> None:
    conn = get_db()
    conn.execute(
        """UPDATE rag_eval_runs
           SET status = ?, metrics = ?, error = ?, finished_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (status, _dumps(metrics), error, run_id),
    )
    conn.commit()
    conn.close()


def save_eval_item(run_id: int, query: str, passed: bool, metrics: dict, evidence: list, case_id: int | None = None, error: str | None = None) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO rag_eval_items (run_id, case_id, query, passed, metrics, evidence, error)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, case_id, query, 1 if passed else 0, _dumps(metrics), _dumps(evidence), error),
    )
    conn.commit()
    conn.close()


def list_eval_runs(limit: int = 12) -> list[dict]:
    init_rag_tables()
    conn = get_db()
    rows = conn.execute(
        """SELECT id, status, metrics, error, created_at, finished_at
           FROM rag_eval_runs ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["metrics"] = _loads(item.get("metrics"), {})
        result.append(item)
    return result


def get_eval_run(run_id: int) -> dict | None:
    init_rag_tables()
    conn = get_db()
    run = conn.execute(
        "SELECT id, status, metrics, error, created_at, finished_at FROM rag_eval_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not run:
        conn.close()
        return None
    rows = conn.execute(
        """SELECT id, case_id, query, passed, metrics, evidence, error, created_at
           FROM rag_eval_items WHERE run_id = ? ORDER BY id ASC""",
        (run_id,),
    ).fetchall()
    conn.close()
    data = dict(run)
    data["metrics"] = _loads(data.get("metrics"), {})
    data["items"] = []
    for row in rows:
        item = dict(row)
        item["passed"] = bool(item["passed"])
        item["metrics"] = _loads(item.get("metrics"), {})
        item["evidence"] = _loads(item.get("evidence"), [])
        data["items"].append(item)
    return data
