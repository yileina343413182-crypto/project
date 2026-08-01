# -*- coding: utf-8 -*-
"""Agent Center API router."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from backend.api.common import error_response, ok
from backend.agents.memory import (
    create_agent_session,
    create_agent_task,
    delete_agent_session,
    get_agent_session,
    get_agent_task,
    get_running_task_for_session,
    init_agent_tables,
    list_agent_sessions,
    save_agent_message,
)
from backend.agents.opinion_agent import analyze_public_opinion
from backend.agents.recommend_agent import run_recommendation_agent
from backend.agents.task_queue import submit_agent_task
from backend.security import get_current_user_id

def _ensure_tables():
    init_agent_tables()


router = APIRouter(prefix="/api/agent", dependencies=[Depends(_ensure_tables)])


def _task_payload(task_id: int, session_id: int, status: str = "queued") -> dict:
    return {"task_id": task_id, "session_id": session_id, "status": status}


def _run_opinion_task(session_id: int, anime_id: int | None, name: str | None, query: str) -> dict:
    result = analyze_public_opinion(anime_id=anime_id, name=name, query=query)
    payload = {"session_id": session_id, **result}
    if result.get("error"):
        save_agent_message(session_id, "agent", result["error"], payload)
        return payload

    report = result.get("report") or {}
    save_agent_message(session_id, "agent", report.get("summary", "舆情诊断完成"), payload)
    return payload


def _run_recommendation_task(
    task_id: int,
    user_id: int,
    session_id: int,
    message: str,
    history: list[dict] | None = None,
) -> dict:
    result = run_recommendation_agent(
        user_id,
        message,
        history=history or [],
        task_id=task_id,
    )
    payload = {"session_id": session_id, **result}
    content = result["result"].get("clarifying_question") or "推荐结果已生成"
    save_agent_message(session_id, "agent", content, payload)
    return payload


@router.post("/opinion/analyze")
def analyze_opinion(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
):
    body = body or {}
    anime_id = body.get("anime_id")
    name = (body.get("name") or "").strip() or None
    query = (body.get("query") or "").strip()

    if anime_id is None and not name:
        return error_response("anime_id 或 name 至少提供一个")

    session_id = create_agent_session(user_id, "opinion", query or name or f"舆情诊断 #{anime_id}")
    save_agent_message(session_id, "user", query or f"分析动漫 {name or anime_id}", {"anime_id": anime_id, "name": name})
    task_id = create_agent_task(user_id, session_id, "opinion", {"anime_id": anime_id, "name": name, "query": query})
    submit_agent_task(task_id, _run_opinion_task, session_id, anime_id, name, query)
    return ok(_task_payload(task_id, session_id))


@router.post("/recommend/start")
def start_recommendation(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
):
    body = body or {}
    query = (body.get("query") or "").strip()
    if not query:
        return error_response("query 不能为空")

    session_id = create_agent_session(user_id, "recommendation", query[:40] or "推荐 Agent 2.0")
    save_agent_message(session_id, "user", query)
    task_id = create_agent_task(user_id, session_id, "recommendation", {"query": query, "history": []})
    submit_agent_task(task_id, _run_recommendation_task, task_id, user_id, session_id, query, [])
    return ok(_task_payload(task_id, session_id))


@router.post("/recommend/message")
def recommendation_message(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
):
    body = body or {}
    session_id = body.get("session_id")
    message = (body.get("message") or "").strip()
    if not session_id:
        return error_response("session_id 不能为空")
    if not message:
        return error_response("message 不能为空")

    session_id = int(session_id)
    session = get_agent_session(user_id, session_id)
    if not session or session.get("agent_type") != "recommendation":
        return error_response("推荐会话不存在或无权访问", 404)

    running = get_running_task_for_session(user_id, session_id)
    if running:
        return error_response("当前会话已有 Agent 任务正在执行，请稍后再发送", 409)

    save_agent_message(session_id, "user", message)
    history = session.get("messages", [])[-8:]
    task_id = create_agent_task(user_id, session_id, "recommendation", {"message": message, "history": history})
    submit_agent_task(task_id, _run_recommendation_task, task_id, user_id, session_id, message, history)
    return ok(_task_payload(task_id, session_id))


@router.get("/tasks/{task_id}")
def task_detail(task_id: int, user_id: int = Depends(get_current_user_id)):
    task = get_agent_task(user_id, task_id)
    if not task:
        return error_response("任务不存在或无权访问", 404)
    return ok(task)


@router.get("/sessions")
def sessions(user_id: int = Depends(get_current_user_id)):
    return ok(list_agent_sessions(user_id))


@router.get("/sessions/{session_id}")
def session_detail(session_id: int, user_id: int = Depends(get_current_user_id)):
    session = get_agent_session(user_id, session_id)
    if not session:
        return error_response("会话不存在或无权访问", 404)
    return ok(session)


@router.delete("/sessions/{session_id}")
def remove_session(session_id: int, user_id: int = Depends(get_current_user_id)):
    affected = delete_agent_session(user_id, session_id)
    if affected == 0:
        return error_response("会话不存在或无权删除", 404)
    return ok(msg="删除成功")
