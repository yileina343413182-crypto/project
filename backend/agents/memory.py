# -*- coding: utf-8 -*-
"""Persistence helpers for Agent Center sessions, tasks, and preferences."""

from __future__ import annotations

import json
from typing import Any

from backend.database import get_db


def _json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def init_agent_tables(db_path=None):
    conn = get_db(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            input TEXT,
            result TEXT,
            error TEXT,
            progress INTEGER NOT NULL DEFAULT 0,
            current_step TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            likes TEXT NOT NULL DEFAULT '[]',
            dislikes TEXT NOT NULL DEFAULT '[]',
            preferred_moods TEXT NOT NULL DEFAULT '[]',
            preferred_genres TEXT NOT NULL DEFAULT '[]',
            feedback TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def create_agent_session(user_id: int, agent_type: str, title: str) -> int:
    init_agent_tables()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agent_sessions (user_id, agent_type, title) VALUES (?, ?, ?)",
        (user_id, agent_type, title[:80] or agent_type),
    )
    conn.commit()
    session_id = cur.lastrowid
    conn.close()
    return session_id


def touch_agent_session(session_id: int):
    conn = get_db()
    conn.execute(
        "UPDATE agent_sessions SET updated_at = datetime('now', 'localtime') WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    conn.close()


def save_agent_message(session_id: int, role: str, content: str, metadata: dict | None = None) -> int:
    init_agent_tables()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agent_messages (session_id, role, content, metadata) VALUES (?, ?, ?, ?)",
        (session_id, role, content, _json_dumps(metadata) if metadata else None),
    )
    conn.commit()
    msg_id = cur.lastrowid
    conn.close()
    touch_agent_session(session_id)
    return msg_id


def list_agent_sessions(user_id: int) -> list[dict[str, Any]]:
    init_agent_tables()
    conn = get_db()
    rows = conn.execute(
        """SELECT id, agent_type, title, status, created_at, updated_at
           FROM agent_sessions WHERE user_id = ? ORDER BY updated_at DESC, id DESC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_agent_session(user_id: int, session_id: int) -> dict | None:
    init_agent_tables()
    conn = get_db()
    session = conn.execute(
        """SELECT id, agent_type, title, status, created_at, updated_at
           FROM agent_sessions WHERE user_id = ? AND id = ?""",
        (user_id, session_id),
    ).fetchone()
    if not session:
        conn.close()
        return None
    rows = conn.execute(
        """SELECT id, role, content, metadata, created_at
           FROM agent_messages WHERE session_id = ? ORDER BY id ASC""",
        (session_id,),
    ).fetchall()
    conn.close()
    messages = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _json_loads(item.get("metadata"), None)
        messages.append(item)
    result = dict(session)
    result["messages"] = messages
    return result


def delete_agent_session(user_id: int, session_id: int) -> int:
    init_agent_tables()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM agent_sessions WHERE user_id = ? AND id = ?", (user_id, session_id))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected


def create_agent_task(user_id: int, session_id: int, agent_type: str, input_data: dict | None = None) -> int:
    init_agent_tables()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO agent_tasks (user_id, session_id, agent_type, input, status, progress, current_step)
           VALUES (?, ?, ?, ?, 'queued', 0, 'queued')""",
        (user_id, session_id, agent_type, _json_dumps(input_data or {})),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    touch_agent_session(session_id)
    return task_id


def _decode_task(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["input"] = _json_loads(item.get("input"), {})
    item["result"] = _json_loads(item.get("result"), None)
    return item


def get_agent_task(user_id: int, task_id: int) -> dict | None:
    init_agent_tables()
    conn = get_db()
    row = conn.execute(
        """SELECT id, user_id, session_id, agent_type, status, input, result, error, progress,
                  current_step, created_at, started_at, finished_at, updated_at
           FROM agent_tasks WHERE user_id = ? AND id = ?""",
        (user_id, task_id),
    ).fetchone()
    conn.close()
    return _decode_task(row)


def get_agent_task_by_id(task_id: int) -> dict | None:
    init_agent_tables()
    conn = get_db()
    row = conn.execute(
        """SELECT id, user_id, session_id, agent_type, status, input, result, error, progress,
                  current_step, created_at, started_at, finished_at, updated_at
           FROM agent_tasks WHERE id = ?""",
        (task_id,),
    ).fetchone()
    conn.close()
    return _decode_task(row)


def get_running_task_for_session(user_id: int, session_id: int) -> dict | None:
    init_agent_tables()
    conn = get_db()
    row = conn.execute(
        """SELECT id, user_id, session_id, agent_type, status, input, result, error, progress,
                  current_step, created_at, started_at, finished_at, updated_at
           FROM agent_tasks
           WHERE user_id = ? AND session_id = ? AND status IN ('queued', 'running')
           ORDER BY id DESC LIMIT 1""",
        (user_id, session_id),
    ).fetchone()
    conn.close()
    return _decode_task(row)


def update_agent_task(task_id: int, **fields) -> None:
    if not fields:
        return
    allowed = {"status", "result", "error", "progress", "current_step", "started_at", "finished_at"}
    updates = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "result" and value is not None:
            value = _json_dumps(value)
        updates.append(f"{key} = ?")
        params.append(value)
    if not updates:
        return
    updates.append("updated_at = datetime('now', 'localtime')")
    params.append(task_id)
    conn = get_db()
    conn.execute(f"UPDATE agent_tasks SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()


def mark_task_running(task_id: int, current_step: str = "running") -> None:
    update_agent_task(
        task_id,
        status="running",
        progress=10,
        current_step=current_step,
    )
    conn = get_db()
    conn.execute(
        """UPDATE agent_tasks
           SET started_at = COALESCE(started_at, datetime('now', 'localtime')),
               updated_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (task_id,),
    )
    conn.commit()
    conn.close()


def mark_task_succeeded(task_id: int, result: dict, current_step: str = "completed") -> None:
    update_agent_task(
        task_id,
        status="succeeded",
        result=result,
        error=None,
        progress=100,
        current_step=current_step,
    )
    conn = get_db()
    conn.execute(
        "UPDATE agent_tasks SET finished_at = datetime('now', 'localtime') WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def mark_task_failed(task_id: int, error: str, result: dict | None = None) -> None:
    update_agent_task(
        task_id,
        status="failed",
        result=result,
        error=error,
        progress=100,
        current_step="failed",
    )
    conn = get_db()
    conn.execute(
        "UPDATE agent_tasks SET finished_at = datetime('now', 'localtime') WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def get_user_preferences(user_id: int) -> dict[str, Any]:
    init_agent_tables()
    conn = get_db()
    row = conn.execute(
        "SELECT likes, dislikes, preferred_moods, preferred_genres, feedback FROM user_preferences WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        conn.execute("INSERT OR IGNORE INTO user_preferences (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
        return {"likes": [], "dislikes": [], "preferred_moods": [], "preferred_genres": [], "feedback": []}
    conn.close()
    return {
        "likes": _json_loads(row["likes"], []),
        "dislikes": _json_loads(row["dislikes"], []),
        "preferred_moods": _json_loads(row["preferred_moods"], []),
        "preferred_genres": _json_loads(row["preferred_genres"], []),
        "feedback": _json_loads(row["feedback"], []),
    }


def update_user_preferences(user_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    current = get_user_preferences(user_id)
    allowed = ["likes", "dislikes", "preferred_moods", "preferred_genres", "feedback"]
    for key in allowed:
        values = updates.get(key)
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values]
        merged = list(current.get(key, []))
        for value in values:
            if isinstance(value, str):
                value = value.strip()
            if value and value not in merged:
                merged.append(value)
        limit = 30 if key == "feedback" else 20
        current[key] = merged[-limit:]

    conn = get_db()
    conn.execute(
        """INSERT INTO user_preferences
           (user_id, likes, dislikes, preferred_moods, preferred_genres, feedback, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
           ON CONFLICT(user_id) DO UPDATE SET
             likes = excluded.likes,
             dislikes = excluded.dislikes,
             preferred_moods = excluded.preferred_moods,
             preferred_genres = excluded.preferred_genres,
             feedback = excluded.feedback,
             updated_at = datetime('now', 'localtime')""",
        (
            user_id,
            _json_dumps(current["likes"]),
            _json_dumps(current["dislikes"]),
            _json_dumps(current["preferred_moods"]),
            _json_dumps(current["preferred_genres"]),
            _json_dumps(current["feedback"]),
        ),
    )
    conn.commit()
    conn.close()
    return current
