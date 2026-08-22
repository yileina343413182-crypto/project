# -*- coding: utf-8 -*-
"""后台 Agent 使用的同步 ORM 持久化层。

它与 ``async_memory`` 访问相同业务表，但适用于 Celery Worker 中的同步工作流；
不要把一个 SQLAlchemy Session 跨请求或跨线程传入这里。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, inspect, or_, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.database import orm_session
from backend.db.models import AgentAttachment, AgentMessage, AgentSession, AgentTask, User, UserAnimeStatus, UserPreference, WatchGuide
from backend.db.session import get_sync_engine


def _date_value(value):
    """把日期转换为可写入 JSON 响应的字符串。"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _json_value(value, default):
    """兼容原生 JSON 值和旧数据库中的 JSON 字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def init_agent_tables(db_path=None):
    """以 checkfirst 模式补建 Agent 相关表，不修改已有数据。"""
    engine = get_sync_engine(db_path=db_path) if db_path else get_sync_engine()
    User.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            AgentSession.__table__,
            AgentAttachment.__table__,
            AgentMessage.__table__,
            AgentTask.__table__,
            UserPreference.__table__,
            WatchGuide.__table__,
            UserAnimeStatus.__table__,
        ],
        checkfirst=True,
    )
    _ensure_agent_task_concurrency_schema(engine)
    _ensure_agent_delivery_schema(engine)


def _ensure_agent_task_concurrency_schema(engine) -> None:
    """为未经过 Alembic 的旧 SQLite/本机数据库补充 M1 的可空字段和索引。"""
    inspector = inspect(engine)
    if not inspector.has_table(AgentTask.__tablename__):
        return

    columns = {column["name"] for column in inspector.get_columns(AgentTask.__tablename__)}
    statements = []
    if "client_request_id" not in columns:
        statements.append(
            "ALTER TABLE agent_tasks ADD COLUMN client_request_id VARCHAR(64) NULL"
        )
    if "turn_seq" not in columns:
        statements.append("ALTER TABLE agent_tasks ADD COLUMN turn_seq INTEGER NULL")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    index_names = {
        index.get("name")
        for index in inspect(engine).get_indexes(AgentTask.__tablename__)
        if index.get("name")
    }
    for index in AgentTask.__table__.indexes:
        if index.name in {
            "ux_agent_tasks_user_agent_request",
            "ux_agent_tasks_session_turn",
        } and index.name not in index_names:
            index.create(bind=engine, checkfirst=True)


def _ensure_agent_delivery_schema(engine) -> None:
    """为未经过 Alembic 的旧本机数据库补充 M2 投递、租约与消息幂等字段。"""
    inspector = inspect(engine)
    if inspector.has_table(AgentMessage.__tablename__):
        message_columns = {
            column["name"]
            for column in inspector.get_columns(AgentMessage.__tablename__)
        }
        if "source_task_id" not in message_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE agent_messages ADD COLUMN source_task_id INTEGER NULL")
                )

    if inspector.has_table(AgentTask.__tablename__):
        task_columns = {
            column["name"] for column in inspector.get_columns(AgentTask.__tablename__)
        }
        definitions = {
            "celery_task_id": "VARCHAR(64) NULL",
            "worker_id": "VARCHAR(255) NULL",
            "lease_until": "DATETIME NULL",
            "heartbeat_at": "DATETIME NULL",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        }
        statements = [
            f"ALTER TABLE agent_tasks ADD COLUMN {name} {definition}"
            for name, definition in definitions.items()
            if name not in task_columns
        ]
        if statements:
            with engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))

    for table, allowed_names in (
        (
            AgentMessage.__table__,
            {"ux_agent_messages_source_task_id"},
        ),
        (
            AgentTask.__table__,
            {
                "ix_agent_tasks_status_lease_until",
                "ix_agent_tasks_celery_task_id",
            },
        ),
    ):
        if not inspect(engine).has_table(table.name):
            continue
        index_names = {
            index.get("name")
            for index in inspect(engine).get_indexes(table.name)
            if index.get("name")
        }
        for index in table.indexes:
            if index.name in allowed_names and index.name not in index_names:
                index.create(bind=engine, checkfirst=True)


# ===== 会话与消息 =====

def create_agent_session(user_id: int, agent_type: str, title: str) -> int:
    """创建会话并返回自增 ID。"""
    with orm_session() as session:
        record = AgentSession(
            user_id=user_id,
            agent_type=agent_type,
            title=title[:80] or agent_type,
        )
        session.add(record)
        session.flush()
        return record.id


def touch_agent_session(session_id: int):
    with orm_session() as session:
        session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(updated_at=datetime.now())
        )


def save_agent_message(
    session_id: int,
    role: str,
    content: str,
    metadata: dict | None = None,
    *,
    source_task_id: int | None = None,
    task_outcome: str | None = None,
) -> int:
    """保存消息并刷新会话；任务消息可与任务终态在同一事务中幂等提交。"""
    if task_outcome not in {None, "succeeded", "failed"}:
        raise ValueError("task_outcome must be succeeded, failed or None")
    with orm_session() as session:
        task = None
        if source_task_id is not None:
            task = session.scalar(
                select(AgentTask)
                .where(AgentTask.id == int(source_task_id))
                .with_for_update()
            )
            if task is None or task.session_id != int(session_id):
                raise LookupError("task does not exist or does not belong to session")
            existing = session.scalar(
                select(AgentMessage).where(
                    AgentMessage.source_task_id == int(source_task_id)
                )
            )
            if existing is not None:
                if task_outcome is not None and task.status not in {"succeeded", "failed"}:
                    _finish_task_record(
                        task,
                        _json_value(existing.message_metadata, metadata or {}),
                        task_outcome,
                    )
                return existing.id

        record = AgentMessage(
            session_id=session_id,
            role=role,
            content=content,
            message_metadata=metadata,
            source_task_id=source_task_id,
        )
        session.add(record)
        session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(updated_at=datetime.now())
        )
        session.flush()
        if task is not None and task_outcome is not None:
            _finish_task_record(task, metadata or {}, task_outcome)
        return record.id


def _finish_task_record(
    task: AgentTask,
    result: dict,
    outcome: str,
    *,
    error: str | None = None,
) -> None:
    """在调用方现有事务内写入不可逆的 AgentTask 终态。"""
    if task.status in {"succeeded", "failed"}:
        return
    now = datetime.now()
    task.status = outcome
    task.result = result
    task.error = (error or str(result.get("error") or "")) if outcome == "failed" else None
    task.progress = 100
    task.current_step = "failed" if outcome == "failed" else "completed"
    task.finished_at = now
    task.updated_at = now
    task.worker_id = None
    task.lease_until = None
    task.heartbeat_at = None


def list_agent_sessions(user_id: int) -> list[dict[str, Any]]:
    with orm_session() as session:
        rows = session.scalars(
            select(AgentSession)
            .where(AgentSession.user_id == user_id)
            .order_by(AgentSession.updated_at.desc(), AgentSession.id.desc())
        ).all()
        return [
            {
                "id": row.id,
                "agent_type": row.agent_type,
                "title": row.title,
                "status": row.status,
                "created_at": _date_value(row.created_at),
                "updated_at": _date_value(row.updated_at),
            }
            for row in rows
        ]


def get_agent_session(user_id: int, session_id: int) -> dict | None:
    """读取会话详情及完整消息序列，并校验会话归属。"""
    with orm_session() as session:
        record = session.scalar(
            select(AgentSession).where(
                AgentSession.user_id == user_id,
                AgentSession.id == session_id,
            )
        )
        if record is None:
            return None
        messages = session.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.id)
        ).all()
        return {
            "id": record.id,
            "agent_type": record.agent_type,
            "title": record.title,
            "status": record.status,
            "created_at": _date_value(record.created_at),
            "updated_at": _date_value(record.updated_at),
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "metadata": _json_value(message.message_metadata, None),
                    "created_at": _date_value(message.created_at),
                }
                for message in messages
            ],
        }


def delete_agent_session(user_id: int, session_id: int) -> int:
    with orm_session() as session:
        result = session.execute(
            delete(AgentSession).where(
                AgentSession.user_id == user_id,
                AgentSession.id == session_id,
            )
        )
        return result.rowcount


# ===== 后台任务状态机 =====

def create_agent_task(
    user_id: int,
    session_id: int,
    agent_type: str,
    input_data: dict | None = None,
    *,
    client_request_id: str | None = None,
    turn_seq: int | None = None,
) -> int:
    """创建 queued 任务，真正执行由 task_queue 负责。"""
    with orm_session() as session:
        task = AgentTask(
            user_id=user_id,
            session_id=session_id,
            agent_type=agent_type,
            client_request_id=client_request_id,
            turn_seq=turn_seq,
            input_data=input_data or {},
            status="queued",
            progress=0,
            current_step="queued",
        )
        session.add(task)
        session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(updated_at=datetime.now())
        )
        session.flush()
        return task.id


def _task_dict(task: AgentTask | None) -> dict | None:
    if task is None:
        return None
    return {
        "id": task.id,
        "user_id": task.user_id,
        "session_id": task.session_id,
        "agent_type": task.agent_type,
        "client_request_id": task.client_request_id,
        "turn_seq": task.turn_seq,
        "celery_task_id": task.celery_task_id,
        "worker_id": task.worker_id,
        "lease_until": _date_value(task.lease_until),
        "heartbeat_at": _date_value(task.heartbeat_at),
        "attempt_count": task.attempt_count,
        "status": task.status,
        "input": _json_value(task.input_data, {}),
        "result": _json_value(task.result, None),
        "error": task.error,
        "progress": task.progress,
        "current_step": task.current_step,
        "created_at": _date_value(task.created_at),
        "started_at": _date_value(task.started_at),
        "finished_at": _date_value(task.finished_at),
        "updated_at": _date_value(task.updated_at),
    }


def get_agent_task(user_id: int, task_id: int) -> dict | None:
    with orm_session() as session:
        return _task_dict(
            session.scalar(
                select(AgentTask).where(
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
            )
        )


def get_agent_task_by_id(task_id: int) -> dict | None:
    with orm_session() as session:
        return _task_dict(session.get(AgentTask, task_id))


def get_running_task_for_session(user_id: int, session_id: int) -> dict | None:
    with orm_session() as session:
        task = session.scalar(
            select(AgentTask)
            .where(
                AgentTask.user_id == user_id,
                AgentTask.session_id == session_id,
                AgentTask.status.in_(("queued", "running")),
            )
            .order_by(AgentTask.id.desc())
            .limit(1)
        )
        return _task_dict(task)


def update_agent_task(task_id: int, **fields) -> None:
    """只更新调用方显式提供的任务字段。"""
    allowed = {
        "status",
        "result",
        "error",
        "progress",
        "current_step",
        "started_at",
        "finished_at",
        "celery_task_id",
        "worker_id",
        "lease_until",
        "heartbeat_at",
        "attempt_count",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = datetime.now()
    with orm_session() as session:
        session.execute(update(AgentTask).where(AgentTask.id == task_id).values(**values))


def mark_task_running(task_id: int, current_step: str = "running") -> None:
    now = datetime.now()
    with orm_session() as session:
        task = session.get(AgentTask, task_id)
        if task is None or task.status in {"succeeded", "failed"}:
            return
        task.status = "running"
        task.progress = 10
        task.current_step = current_step
        task.started_at = task.started_at or now
        task.updated_at = now


def mark_task_succeeded(task_id: int, result: dict, current_step: str = "completed") -> None:
    """原子写入结果并将任务推进到 succeeded 终态。"""
    now = datetime.now()
    with orm_session() as session:
        task = session.get(AgentTask, task_id)
        if task is None or task.status in {"succeeded", "failed"}:
            return
        _finish_task_record(task, result, "succeeded")
        task.current_step = current_step


def mark_task_failed(task_id: int, error: str, result: dict | None = None) -> None:
    """记录截断后的异常信息并将任务推进到 failed 终态。"""
    now = datetime.now()
    with orm_session() as session:
        task = session.get(AgentTask, task_id)
        if task is None or task.status in {"succeeded", "failed"}:
            return
        _finish_task_record(task, result or {}, "failed", error=error)


def register_agent_task_delivery(task_id: int, celery_task_id: str) -> bool:
    """记录最近一次 Celery 投递 ID；终态任务不会被重新投递。"""
    now = datetime.now()
    with orm_session() as session:
        task = session.get(AgentTask, int(task_id))
        if task is None or task.status in {"succeeded", "failed"}:
            return False
        task.celery_task_id = str(celery_task_id)[:64]
        task.current_step = "queued"
        task.updated_at = now
        return True


def claim_agent_task(task_id: int, worker_id: str, lease_seconds: int) -> dict:
    """用数据库租约抢占任务，重复投递只能有一个 Worker 获得执行权。"""
    now = datetime.now()
    with orm_session() as session:
        task = session.get(AgentTask, int(task_id))
        if task is None:
            return {"claim_state": "missing"}
        if task.status in {"succeeded", "failed"}:
            return {"claim_state": "terminal", "task": _task_dict(task)}

        claimable = or_(
            AgentTask.status == "queued",
            and_(
                AgentTask.status == "running",
                or_(
                    AgentTask.lease_until.is_(None),
                    AgentTask.lease_until <= now,
                ),
            ),
        )
        result = session.execute(
            update(AgentTask)
            .where(AgentTask.id == int(task_id), claimable)
            .values(
                status="running",
                progress=10,
                current_step="running_agent",
                worker_id=str(worker_id)[:255],
                heartbeat_at=now,
                lease_until=now + timedelta(seconds=max(1, int(lease_seconds))),
                attempt_count=AgentTask.attempt_count + 1,
                started_at=task.started_at or now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if not result.rowcount:
            session.expire_all()
            current = session.get(AgentTask, int(task_id))
            state = "terminal" if current and current.status in {"succeeded", "failed"} else "busy"
            return {"claim_state": state, "task": _task_dict(current)}
        session.expire_all()
        return {
            "claim_state": "claimed",
            "task": _task_dict(session.get(AgentTask, int(task_id))),
        }


def heartbeat_agent_task(task_id: int, worker_id: str, lease_seconds: int) -> bool:
    """仅由当前租约持有者续租，避免旧 Worker 延长新 Worker 的租约。"""
    now = datetime.now()
    with orm_session() as session:
        result = session.execute(
            update(AgentTask)
            .where(
                AgentTask.id == int(task_id),
                AgentTask.status == "running",
                AgentTask.worker_id == str(worker_id)[:255],
            )
            .values(
                heartbeat_at=now,
                lease_until=now + timedelta(seconds=max(1, int(lease_seconds))),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)


def get_agent_message_by_task_id(task_id: int) -> dict | None:
    """读取任务已经提交的唯一最终消息，用于重投递快速恢复。"""
    with orm_session() as session:
        message = session.scalar(
            select(AgentMessage).where(AgentMessage.source_task_id == int(task_id))
        )
        if message is None:
            return None
        return {
            "id": message.id,
            "session_id": message.session_id,
            "content": message.content,
            "metadata": _json_value(message.message_metadata, {}),
        }


def take_recoverable_agent_tasks(stale_seconds: int, limit: int = 100) -> list[dict]:
    """把遗留 queued/租约过期 running 任务重置为 queued，并返回待重投递项。"""
    now = datetime.now()
    queued_before = now - timedelta(seconds=max(1, int(stale_seconds)))
    with orm_session() as session:
        tasks = session.scalars(
            select(AgentTask)
            .where(
                or_(
                    and_(AgentTask.status == "queued", AgentTask.updated_at < queued_before),
                    and_(
                        AgentTask.status == "running",
                        or_(
                            AgentTask.lease_until.is_(None),
                            AgentTask.lease_until < now,
                        ),
                    ),
                )
            )
            .order_by(AgentTask.id)
            .limit(max(1, min(int(limit), 500)))
            .with_for_update()
        ).all()
        recovered = []
        for task in tasks:
            task.status = "queued"
            task.progress = 0
            task.current_step = "requeued"
            task.worker_id = None
            task.lease_until = None
            task.heartbeat_at = None
            task.updated_at = now
            recovered.append({"id": task.id, "agent_type": task.agent_type})
        return recovered


# ===== 番剧观看状态 =====

def get_non_recommendable_anime_ids(user_id: int) -> set[int]:
    """返回当前用户明确标记为已看过或观看中的动漫 ID。"""
    with orm_session() as session:
        values = session.scalars(
            select(UserAnimeStatus.anime_id).where(
                UserAnimeStatus.user_id == int(user_id),
                UserAnimeStatus.status.in_(("watched", "watching")),
            )
        ).all()
    return {int(value) for value in values}


# ===== 用户长期偏好 =====

def _ensure_preferences(session, user_id: int) -> None:
    """按数据库方言执行幂等 upsert，保证每个用户只有一条偏好记录。"""
    values = {
        "user_id": user_id,
        "likes": [],
        "dislikes": [],
        "preferred_moods": [],
        "preferred_genres": [],
        "feedback": [],
    }
    if session.bind.dialect.name == "mysql":
        statement = mysql_insert(UserPreference).values(**values)
        session.execute(statement.on_duplicate_key_update(user_id=statement.inserted.user_id))
    else:
        statement = sqlite_insert(UserPreference).values(**values)
        session.execute(statement.on_conflict_do_nothing(index_elements=[UserPreference.user_id]))


def _preference_dict(record: UserPreference) -> dict[str, Any]:
    return {
        "likes": _json_value(record.likes, []),
        "dislikes": _json_value(record.dislikes, []),
        "preferred_moods": _json_value(record.preferred_moods, []),
        "preferred_genres": _json_value(record.preferred_genres, []),
        "feedback": _json_value(record.feedback, []),
    }


def get_user_preferences(user_id: int) -> dict[str, Any]:
    with orm_session() as session:
        _ensure_preferences(session, user_id)
        record = session.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
        return _preference_dict(record)


def update_user_preferences(user_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    """只合并白名单偏好字段，去重后返回最新完整偏好。"""
    allowed = ("likes", "dislikes", "preferred_moods", "preferred_genres", "feedback")
    with orm_session() as session:
        _ensure_preferences(session, user_id)
        record = session.scalar(
            select(UserPreference)
            .where(UserPreference.user_id == user_id)
            .with_for_update()
        )
        current = _preference_dict(record)
        for key in allowed:
            values = updates.get(key)
            if values is None:
                continue
            if not isinstance(values, list):
                values = [values]
            merged = list(current[key])
            for value in values:
                if isinstance(value, str):
                    value = value.strip()
                if value and value not in merged:
                    merged.append(value)
            current[key] = merged[-(30 if key == "feedback" else 20):]
            setattr(record, key, current[key])
        record.updated_at = datetime.now()
        return current
