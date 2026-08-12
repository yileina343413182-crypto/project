# -*- coding: utf-8 -*-
"""Agent 中心接口：创建会话/任务、追加多轮消息并轮询执行结果。

接口中的数据库操作使用异步会话；耗时 Agent 工作通过进程内任务队列交给
同步后台线程执行，避免阻塞请求事件循环。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.common import error_response, ok
from backend.agents.async_memory import (
    create_agent_session,
    create_agent_task,
    delete_agent_session,
    get_agent_session,
    get_agent_task,
    get_running_task_for_session,
    list_agent_sessions,
    save_agent_message,
)
from backend.agents.memory import save_agent_message as save_agent_message_sync
from backend.agents.opinion_agent import analyze_public_opinion
from backend.agents.recommend_agent import run_recommendation_agent
from backend.agents.task_queue import submit_agent_task
from backend.db.session import get_async_session
from backend.security import get_current_user_id

router = APIRouter(prefix="/api/agent")


def _task_payload(task_id: int, session_id: int, status: str = "queued") -> dict:
    """生成创建任务后立即返回给前端的最小轮询凭据。"""
    return {"task_id": task_id, "session_id": session_id, "status": status}


def _run_opinion_task(session_id: int, anime_id: int | None, name: str | None, query: str) -> dict:
    """在线程池中执行舆情 Agent，并把最终回答写回会话消息。"""
    result = analyze_public_opinion(anime_id=anime_id, name=name, query=query)
    payload = {"session_id": session_id, **result}
    if result.get("error"):
        save_agent_message_sync(session_id, "agent", result["error"], payload)
        return payload

    report = result.get("report") or {}
    save_agent_message_sync(session_id, "agent", report.get("summary", "舆情诊断完成"), payload)
    return payload


def _run_recommendation_task(
    task_id: int,
    user_id: int,
    session_id: int,
    message: str,
    history: list[dict] | None = None,
) -> dict:
    """在线程池中执行推荐图，并持久化便于下轮对话使用的回答。"""
    result = run_recommendation_agent(
        user_id,
        message,
        history=history or [],
        task_id=task_id,
    )
    payload = {"session_id": session_id, **result}
    content = result["result"].get("clarifying_question") or "推荐结果已生成"
    save_agent_message_sync(session_id, "agent", content, payload)
    return payload


@router.post("/opinion/analyze")
async def analyze_opinion(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """创建舆情分析会话与后台任务，立即返回任务 ID。"""
    body = body or {}
    anime_id = body.get("anime_id")
    name = (body.get("name") or "").strip() or None
    query = (body.get("query") or "").strip()

    if anime_id is None and not name:
        return error_response("anime_id 或 name 至少提供一个")

    session_id = await create_agent_session(db, user_id, "opinion", query or name or f"舆情诊断 #{anime_id}")
    await save_agent_message(
        db,
        session_id,
        "user",
        query or f"分析动漫 {name or anime_id}",
        {"anime_id": anime_id, "name": name},
    )
    task_id = await create_agent_task(
        db,
        user_id,
        session_id,
        "opinion",
        {"anime_id": anime_id, "name": name, "query": query},
    )
    await db.commit()
    submit_agent_task(task_id, _run_opinion_task, session_id, anime_id, name, query)
    return ok(_task_payload(task_id, session_id))


@router.post("/recommend/start")
async def start_recommendation(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """创建推荐会话，并用首条用户消息启动推荐任务。"""
    body = body or {}
    query = (body.get("query") or "").strip()
    if not query:
        return error_response("query 不能为空")

    session_id = await create_agent_session(db, user_id, "recommendation", query[:40] or "推荐 Agent 2.0")
    await save_agent_message(db, session_id, "user", query)
    task_id = await create_agent_task(
        db,
        user_id,
        session_id,
        "recommendation",
        {"query": query, "history": []},
    )
    await db.commit()
    submit_agent_task(task_id, _run_recommendation_task, task_id, user_id, session_id, query, [])
    return ok(_task_payload(task_id, session_id))


@router.post("/recommend/message")
async def recommendation_message(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """在既有推荐会话中追加消息，同一会话同时只允许一个运行中任务。"""
    body = body or {}
    session_id = body.get("session_id")
    message = (body.get("message") or "").strip()
    if not session_id:
        return error_response("session_id 不能为空")
    if not message:
        return error_response("message 不能为空")

    session_id = int(session_id)
    session = await get_agent_session(db, user_id, session_id, for_update=True)
    if not session or session.get("agent_type") != "recommendation":
        return error_response("推荐会话不存在或无权访问", 404)

    running = await get_running_task_for_session(db, user_id, session_id)
    if running:
        return error_response("当前会话已有 Agent 任务正在执行，请稍后再发送", 409)

    await save_agent_message(db, session_id, "user", message)
    history = session.get("messages", [])[-8:]
    task_id = await create_agent_task(
        db,
        user_id,
        session_id,
        "recommendation",
        {"message": message, "history": history},
    )
    await db.commit()
    submit_agent_task(task_id, _run_recommendation_task, task_id, user_id, session_id, message, history)
    return ok(_task_payload(task_id, session_id))


@router.get("/tasks/{task_id}")
async def task_detail(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """返回任务状态，供前端轮询 queued/running/succeeded/failed。"""
    task = await get_agent_task(db, user_id, task_id)
    if not task:
        return error_response("任务不存在或无权访问", 404)
    return ok(task)


@router.get("/sessions")
async def sessions(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """列出当前用户的 Agent 会话摘要。"""
    return ok(await list_agent_sessions(db, user_id))


@router.get("/sessions/{session_id}")
async def session_detail(
    session_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """返回会话详情及按时间排列的消息。"""
    session = await get_agent_session(db, user_id, session_id)
    if not session:
        return error_response("会话不存在或无权访问", 404)
    return ok(session)


@router.delete("/sessions/{session_id}")
async def remove_session(
    session_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """删除当前用户拥有的会话及其级联数据。"""
    affected = await delete_agent_session(db, user_id, session_id)
    if affected == 0:
        return error_response("会话不存在或无权删除", 404)
    return ok(msg="删除成功")
