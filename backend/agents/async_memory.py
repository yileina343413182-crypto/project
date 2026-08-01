"""AsyncSession Agent persistence for FastAPI request handlers only."""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AgentMessage, AgentSession, AgentTask


def _value(value, default=None):
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str) and default is not None:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value if value is not None else default


async def create_agent_session(
    session: AsyncSession, user_id: int, agent_type: str, title: str
) -> int:
    record = AgentSession(user_id=user_id, agent_type=agent_type, title=title[:80] or agent_type)
    session.add(record)
    await session.flush()
    return record.id


async def save_agent_message(
    session: AsyncSession,
    session_id: int,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> int:
    record = AgentMessage(
        session_id=session_id,
        role=role,
        content=content,
        message_metadata=metadata,
    )
    session.add(record)
    await session.execute(
        update(AgentSession)
        .where(AgentSession.id == session_id)
        .values(updated_at=datetime.now())
    )
    await session.flush()
    return record.id


async def create_agent_task(
    session: AsyncSession,
    user_id: int,
    session_id: int,
    agent_type: str,
    input_data: dict | None = None,
) -> int:
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
    await session.execute(
        update(AgentSession)
        .where(AgentSession.id == session_id)
        .values(updated_at=datetime.now())
    )
    await session.flush()
    return task.id


async def get_agent_session(
    session: AsyncSession,
    user_id: int,
    session_id: int,
    *,
    for_update: bool = False,
) -> dict | None:
    statement = select(AgentSession).where(
        AgentSession.user_id == user_id,
        AgentSession.id == session_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        return None
    messages = (
        await session.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.id)
        )
    ).all()
    return {
        "id": record.id,
        "agent_type": record.agent_type,
        "title": record.title,
        "status": record.status,
        "created_at": _value(record.created_at),
        "updated_at": _value(record.updated_at),
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "metadata": _value(message.message_metadata, None),
                "created_at": _value(message.created_at),
            }
            for message in messages
        ],
    }


def _task_dict(task: AgentTask | None) -> dict | None:
    if task is None:
        return None
    return {
        "id": task.id,
        "user_id": task.user_id,
        "session_id": task.session_id,
        "agent_type": task.agent_type,
        "status": task.status,
        "input": _value(task.input_data, {}),
        "result": _value(task.result, None),
        "error": task.error,
        "progress": task.progress,
        "current_step": task.current_step,
        "created_at": _value(task.created_at),
        "started_at": _value(task.started_at),
        "finished_at": _value(task.finished_at),
        "updated_at": _value(task.updated_at),
    }


async def get_agent_task(session: AsyncSession, user_id: int, task_id: int) -> dict | None:
    task = await session.scalar(
        select(AgentTask).where(AgentTask.user_id == user_id, AgentTask.id == task_id)
    )
    return _task_dict(task)


async def get_running_task_for_session(
    session: AsyncSession, user_id: int, session_id: int
) -> dict | None:
    task = await session.scalar(
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


async def list_agent_sessions(session: AsyncSession, user_id: int) -> list[dict]:
    records = (
        await session.scalars(
            select(AgentSession)
            .where(AgentSession.user_id == user_id)
            .order_by(AgentSession.updated_at.desc(), AgentSession.id.desc())
        )
    ).all()
    return [
        {
            "id": record.id,
            "agent_type": record.agent_type,
            "title": record.title,
            "status": record.status,
            "created_at": _value(record.created_at),
            "updated_at": _value(record.updated_at),
        }
        for record in records
    ]


async def delete_agent_session(session: AsyncSession, user_id: int, session_id: int) -> int:
    result = await session.execute(
        delete(AgentSession).where(
            AgentSession.user_id == user_id,
            AgentSession.id == session_id,
        )
    )
    return result.rowcount
