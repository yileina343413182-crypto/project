# -*- coding: utf-8 -*-
"""后台 Agent 使用的同步 ORM 持久化层。

它与 ``async_memory`` 访问相同业务表，但适用于线程池中的同步工作流；
不要把一个 SQLAlchemy Session 跨请求或跨线程传入这里。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.database import orm_session
from backend.db.models import AgentMessage, AgentSession, AgentTask, User, UserPreference
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
            AgentMessage.__table__,
            AgentTask.__table__,
            UserPreference.__table__,
        ],
        checkfirst=True,
    )


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
) -> int:
    """保存消息和结构化元数据，同时刷新会话更新时间。"""
    with orm_session() as session:
        record = AgentMessage(
            session_id=session_id,
            role=role,
            content=content,
            message_metadata=metadata,
        )
        session.add(record)
        session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(updated_at=datetime.now())
        )
        session.flush()
        return record.id


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
) -> int:
    """创建 queued 任务，真正执行由 task_queue 负责。"""
    with orm_session() as session:
        task = AgentTask(
            user_id=user_id,
            session_id=session_id,
            agent_type=agent_type,
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
    allowed = {"status", "result", "error", "progress", "current_step", "started_at", "finished_at"}
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
        if task is None:
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
        if task is None:
            return
        task.status = "succeeded"
        task.result = result
        task.error = None
        task.progress = 100
        task.current_step = current_step
        task.finished_at = now
        task.updated_at = now


def mark_task_failed(task_id: int, error: str, result: dict | None = None) -> None:
    """记录截断后的异常信息并将任务推进到 failed 终态。"""
    now = datetime.now()
    with orm_session() as session:
        task = session.get(AgentTask, task_id)
        if task is None:
            return
        task.status = "failed"
        task.result = result
        task.error = error
        task.progress = 100
        task.current_step = "failed"
        task.finished_at = now
        task.updated_at = now


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
