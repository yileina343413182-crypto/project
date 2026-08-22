"""FastAPI 请求专用的异步 Agent 会话、消息和任务持久化。

这里不执行耗时 Agent；只在请求事务中创建记录或读取状态。Celery Worker 使用
``agents.memory`` 中对应的同步函数更新任务。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AgentAttachment, AgentMessage, AgentSession, AgentTask


def _value(value, default=None):
    """把日期和可能的 JSON 字符串转换为 API 可返回的 Python 值。"""
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
    """创建 Agent 会话并 flush，立即取得会话 ID。"""
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
    """保存一条用户/Agent 消息，并更新会话最后活动时间。"""
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


def _attachment_dict(record: AgentAttachment, *, include_storage: bool = False) -> dict:
    result = {
        "id": record.id,
        "mime_type": record.mime_type,
        "byte_size": record.byte_size,
        "width": record.width,
        "height": record.height,
        "created_at": _value(record.created_at),
    }
    if include_storage:
        result["content"] = bytes(record.content)
        result["session_id"] = record.session_id
        result["message_id"] = record.message_id
    return result


async def create_agent_attachment(
    session: AsyncSession,
    user_id: int,
    stored: dict,
) -> dict:
    record = AgentAttachment(user_id=user_id, **stored)
    session.add(record)
    await session.flush()
    return _attachment_dict(record)


async def get_agent_attachment(
    session: AsyncSession,
    user_id: int,
    attachment_id: int,
    *,
    include_storage: bool = False,
) -> dict | None:
    record = await session.scalar(
        select(AgentAttachment).where(
            AgentAttachment.id == int(attachment_id),
            AgentAttachment.user_id == int(user_id),
        )
    )
    return _attachment_dict(record, include_storage=include_storage) if record else None


async def get_unbound_agent_attachment(
    session: AsyncSession,
    user_id: int,
    attachment_id: int,
) -> dict | None:
    row = (
        await session.execute(
            select(
                AgentAttachment.id,
                AgentAttachment.mime_type,
                AgentAttachment.byte_size,
                AgentAttachment.width,
                AgentAttachment.height,
                AgentAttachment.created_at,
            ).where(
                AgentAttachment.id == int(attachment_id),
                AgentAttachment.user_id == int(user_id),
                AgentAttachment.session_id.is_(None),
                AgentAttachment.message_id.is_(None),
            )
        )
    ).mappings().first()
    if row is None:
        return None
    result = dict(row)
    result["created_at"] = _value(result.get("created_at"))
    return result


async def bind_agent_attachment(
    session: AsyncSession,
    user_id: int,
    attachment_id: int,
    session_id: int,
    message_id: int,
) -> bool:
    """把一次上传原子绑定到一条用户消息，绑定后不能被其他请求复用。"""
    result = await session.execute(
        update(AgentAttachment)
        .where(
            AgentAttachment.id == int(attachment_id),
            AgentAttachment.user_id == int(user_id),
            AgentAttachment.session_id.is_(None),
            AgentAttachment.message_id.is_(None),
        )
        .values(session_id=int(session_id), message_id=int(message_id))
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


async def delete_unbound_agent_attachment(
    session: AsyncSession,
    user_id: int,
    attachment_id: int,
) -> bool:
    result = await session.execute(
        delete(AgentAttachment).where(
            AgentAttachment.id == int(attachment_id),
            AgentAttachment.user_id == int(user_id),
            AgentAttachment.session_id.is_(None),
            AgentAttachment.message_id.is_(None),
        )
    )
    return bool(result.rowcount)


async def purge_stale_unbound_attachments(
    session: AsyncSession,
    user_id: int,
    *,
    max_age_hours: int = 24,
) -> int:
    """清理当前用户因关闭页面而遗留的未绑定上传。"""
    result = await session.execute(
        delete(AgentAttachment).where(
            AgentAttachment.user_id == int(user_id),
            AgentAttachment.session_id.is_(None),
            AgentAttachment.message_id.is_(None),
            AgentAttachment.created_at < datetime.now() - timedelta(hours=max_age_hours),
        )
    )
    return int(result.rowcount or 0)


async def create_agent_task(
    session: AsyncSession,
    user_id: int,
    session_id: int,
    agent_type: str,
    input_data: dict | None = None,
    *,
    client_request_id: str | None = None,
    turn_seq: int | None = None,
) -> int:
    """创建 queued 状态的后台任务记录。"""
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
    await session.execute(
        update(AgentSession)
        .where(AgentSession.id == session_id)
        .values(updated_at=datetime.now())
    )
    await session.flush()
    return task.id


async def get_agent_task_by_request_id(
    session: AsyncSession,
    user_id: int,
    agent_type: str,
    client_request_id: str,
) -> dict | None:
    """按用户、Agent 类型和客户端幂等键读取既有任务。"""
    task = await session.scalar(
        select(AgentTask).where(
            AgentTask.user_id == user_id,
            AgentTask.agent_type == agent_type,
            AgentTask.client_request_id == client_request_id,
        )
    )
    return _task_dict(task)


async def get_next_turn_seq(session: AsyncSession, session_id: int) -> int:
    """在已锁定会话的事务中取得下一轮序号。"""
    current = await session.scalar(
        select(func.max(AgentTask.turn_seq)).where(AgentTask.session_id == session_id)
    )
    if current is not None:
        return int(current) + 1
    legacy_count = await session.scalar(
        select(func.count()).select_from(AgentTask).where(AgentTask.session_id == session_id)
    )
    return int(legacy_count or 0) + 1


async def get_agent_session(
    session: AsyncSession,
    user_id: int,
    session_id: int,
    *,
    for_update: bool = False,
) -> dict | None:
    """读取当前用户的会话和消息；可选加行锁用于追加消息。"""
    if for_update and session.bind.dialect.name == "sqlite":
        # SQLite 忽略 SELECT ... FOR UPDATE；一次原值 UPDATE 会取得写锁，
        # 让“检查运行任务 + 追加任务”在并发请求间真正串行化。
        await session.execute(
            update(AgentSession)
            .where(
                AgentSession.user_id == user_id,
                AgentSession.id == session_id,
            )
            .values(updated_at=AgentSession.updated_at)
        )
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
    active_task = await session.scalar(
        select(AgentTask)
        .where(
            AgentTask.user_id == user_id,
            AgentTask.session_id == session_id,
            AgentTask.status.in_(("queued", "running")),
        )
        .order_by(AgentTask.id.desc())
        .limit(1)
    )
    return {
        "id": record.id,
        "agent_type": record.agent_type,
        "title": record.title,
        "status": record.status,
        "created_at": _value(record.created_at),
        "updated_at": _value(record.updated_at),
        "active_task": _active_task_dict(active_task),
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
    """把任务 ORM 对象转换为前端轮询所需的数据结构。"""
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
        "lease_until": _value(task.lease_until),
        "heartbeat_at": _value(task.heartbeat_at),
        "attempt_count": task.attempt_count,
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


def _active_task_dict(task: AgentTask | None) -> dict | None:
    if task is None:
        return None
    return {
        "task_id": task.id,
        "status": task.status,
        "current_step": task.current_step,
        "client_request_id": task.client_request_id,
        "turn_seq": task.turn_seq,
    }


async def get_agent_task(session: AsyncSession, user_id: int, task_id: int) -> dict | None:
    task = await session.scalar(
        select(AgentTask).where(AgentTask.user_id == user_id, AgentTask.id == task_id)
    )
    return _task_dict(task)


async def get_running_task_for_session(
    session: AsyncSession, user_id: int, session_id: int
) -> dict | None:
    """查找会话中尚未结束的最新任务，用于阻止重复提交。"""
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
    session_ids = [record.id for record in records]
    active_by_session = {}
    if session_ids:
        active_tasks = (
            await session.scalars(
                select(AgentTask)
                .where(
                    AgentTask.user_id == user_id,
                    AgentTask.session_id.in_(session_ids),
                    AgentTask.status.in_(("queued", "running")),
                )
                .order_by(AgentTask.id.desc())
            )
        ).all()
        for task in active_tasks:
            active_by_session.setdefault(task.session_id, _active_task_dict(task))
    return [
        {
            "id": record.id,
            "agent_type": record.agent_type,
            "title": record.title,
            "status": record.status,
            "created_at": _value(record.created_at),
            "updated_at": _value(record.updated_at),
            "active_task": active_by_session.get(record.id),
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
