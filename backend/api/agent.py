# -*- coding: utf-8 -*-
"""Agent 中心接口：创建会话/任务、追加多轮消息并轮询执行结果。

接口中的数据库操作使用异步会话；耗时 Agent 工作通过 Redis + Celery 交给
独立 Worker 执行，避免阻塞请求事件循环并支持多进程恢复。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Body, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.common import error_response, ok
from backend.agents.async_memory import (
    create_agent_session,
    create_agent_task,
    delete_agent_session,
    get_agent_session,
    get_agent_task,
    get_agent_task_by_request_id,
    get_next_turn_seq,
    get_running_task_for_session,
    list_agent_sessions,
    save_agent_message,
)
from backend.agents.memory import save_agent_message as save_agent_message_sync
from backend.agents.opinion_agent import analyze_public_opinion
from backend.agents.recommend_followup import (
    extract_last_recommendation_context,
    run_recommendation_followup,
)
from backend.agents.recommend_agent import run_recommendation_agent
from backend.agents.task_queue import submit_agent_task
from backend.agents.watch_guide import (
    build_watch_guide_event,
    classify_offer_reply,
    format_watch_guide_offer_question,
    generate_watch_guide,
    normalize_anime_title,
    reconstruct_watch_guide_state,
    resolve_anime_subject,
    save_watch_guide_with_message,
    should_offer_watch_guide,
)
from backend.db.async_repository import (
    delete_watch_guide as delete_watch_guide_record,
    get_watch_guide as get_watch_guide_record,
    list_watch_guides as list_watch_guide_records,
)
from backend.db.session import get_async_session
from backend.security import get_current_user_id

router = APIRouter(prefix="/api/agent")


def _task_payload(
    task_id: int,
    session_id: int,
    status: str = "queued",
    *,
    client_request_id: str | None = None,
    reused: bool = False,
) -> dict:
    """生成创建任务后立即返回给前端的最小轮询凭据。"""
    return {
        "task_id": task_id,
        "session_id": session_id,
        "status": status,
        "client_request_id": client_request_id,
        "reused": reused,
    }


def _client_request_id(body: dict) -> str | None:
    """读取客户端幂等键；旧客户端未提供时生成服务端兼容值。"""
    value = body.get("client_request_id")
    if value is None:
        return uuid4().hex
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= 64 else None


def _reused_task_payload(task: dict) -> dict:
    return _task_payload(
        task["id"],
        task["session_id"],
        task["status"],
        client_request_id=task.get("client_request_id"),
        reused=True,
    )


def _run_opinion_task(
    session_id: int,
    anime_id: int | None,
    name: str | None,
    query: str,
    *,
    task_id: int | None = None,
) -> dict:
    """在 Celery Worker 中执行舆情 Agent，并把最终回答写回会话消息。"""
    result = analyze_public_opinion(anime_id=anime_id, name=name, query=query)
    payload = {"session_id": session_id, **result}
    if result.get("error"):
        save_agent_message_sync(
            session_id,
            "agent",
            result["error"],
            payload,
            source_task_id=task_id,
            task_outcome="failed" if task_id is not None else None,
        )
        return payload

    report = result.get("report") or {}
    save_agent_message_sync(
        session_id,
        "agent",
        report.get("summary", "舆情诊断完成"),
        payload,
        source_task_id=task_id,
        task_outcome="succeeded" if task_id is not None else None,
    )
    return payload


def _run_recommendation_task(
    task_id: int,
    user_id: int,
    session_id: int,
    message: str,
    history: list[dict] | None = None,
    recommendation_context: dict | None = None,
    watch_state: dict | None = None,
    turn_action: str = "normal",
) -> dict:
    """执行推荐图或推荐后的文本追问，并持久化本轮回答。"""
    if recommendation_context is not None:
        state = watch_state or {
            "pending_offer": None,
            "active_target": None,
            "offered_keys": [],
        }
        pending_offer = state.get("pending_offer") or {}
        pending_anime = pending_offer.get("anime") or {}
        offer_id = str(pending_offer.get("offer_id") or "")

        if turn_action == "accept" and pending_anime.get("name"):
            accepted = build_watch_guide_event(
                "accepted",
                pending_anime,
                offer_id=offer_id or None,
            )
            source_answer = str(pending_offer.get("source_answer") or "")
            if not source_answer and history:
                source_answer = str((history[-1] or {}).get("content") or "")
            try:
                guide_result = generate_watch_guide(
                    pending_anime,
                    source_answer,
                    history or [],
                    recommendation_context,
                )
                answer = (
                    f"已将《{pending_anime['name']}》加入“待看番剧指南”。"
                    "你可以点击页面顶部的同名入口查看完整观看计划。"
                )
                payload = {
                    "session_id": session_id,
                    "response_mode": "conversation",
                    "answer": answer,
                    "anime_target": pending_anime,
                    "watch_guide_events": [accepted],
                    "offer_id": offer_id,
                }
                return save_watch_guide_with_message(
                    user_id,
                    session_id,
                    pending_anime,
                    guide_result,
                    payload,
                    task_id=task_id,
                )
            except Exception as exc:
                failed = build_watch_guide_event(
                    "failed",
                    pending_anime,
                    offer_id=offer_id or None,
                    reason=type(exc).__name__,
                )
                answer = (
                    f"这次没能保存《{pending_anime['name']}》的观看指南。"
                    "你的对话没有丢失，可以稍后在新对话中重新尝试。"
                )
                payload = {
                    "session_id": session_id,
                    "response_mode": "conversation",
                    "answer": answer,
                    "anime_target": pending_anime,
                    "watch_guide_events": [accepted, failed],
                    "watch_guide_failure": type(exc).__name__,
                }
                save_agent_message_sync(
                    session_id,
                    "agent",
                    answer,
                    payload,
                    source_task_id=task_id,
                    task_outcome="succeeded",
                )
                return payload

        if turn_action == "decline" and pending_anime.get("name"):
            answer = f"好的，这次不把《{pending_anime['name']}》加入待看番剧指南。"
            payload = {
                "session_id": session_id,
                "response_mode": "conversation",
                "answer": answer,
                "anime_target": pending_anime,
                "watch_guide_events": [
                    build_watch_guide_event(
                        "declined",
                        pending_anime,
                        offer_id=offer_id or None,
                    )
                ],
            }
            save_agent_message_sync(
                session_id,
                "agent",
                answer,
                payload,
                source_task_id=task_id,
                task_outcome="succeeded",
            )
            return payload

        events = []
        if turn_action == "other" and pending_anime.get("name"):
            events.append(
                build_watch_guide_event(
                    "ignored",
                    pending_anime,
                    offer_id=offer_id or None,
                )
            )

        anime = resolve_anime_subject(
            message,
            recommendation_context,
            state.get("active_target"),
        )
        followup_context = dict(recommendation_context)
        if anime is not None:
            followup_context["active_target"] = anime
        result = run_recommendation_followup(
            message,
            history or [],
            followup_context,
        )
        recommendation_names = {
            normalize_anime_title(item.get("name"))
            for item in (recommendation_context.get("recommendations") or [])
            if isinstance(item, dict) and item.get("name")
        }
        target_is_saved_recommendation = (
            anime is not None
            and normalize_anime_title(anime.get("name")) in recommendation_names
        )
        answer_supports_offer = not result.get("fallback") or target_is_saved_recommendation
        if answer_supports_offer and should_offer_watch_guide(message, anime, state, user_id):
            offer_id = f"{session_id}:{task_id}"
            result["answer"] = (
                f"{result['answer'].rstrip()}\n\n"
                f"{format_watch_guide_offer_question(anime)}"
            )
            events.append(
                build_watch_guide_event(
                    "offered",
                    anime,
                    offer_id=offer_id,
                )
            )
        payload = {"session_id": session_id, **result}
        if anime is not None:
            payload["anime_target"] = anime
        if events:
            payload["watch_guide_events"] = events
        save_agent_message_sync(
            session_id,
            "agent",
            result["answer"],
            payload,
            source_task_id=task_id,
            task_outcome="succeeded",
        )
        return payload

    result = run_recommendation_agent(
        user_id,
        message,
        history=history or [],
        task_id=task_id,
    )
    payload = {"session_id": session_id, **result}
    content = result["result"].get("clarifying_question") or "推荐结果已生成"
    save_agent_message_sync(
        session_id,
        "agent",
        content,
        payload,
        source_task_id=task_id,
        task_outcome="succeeded",
    )
    return payload


@router.post("/opinion/analyze")
async def analyze_opinion(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """创建舆情分析会话与后台任务，立即返回任务 ID。"""
    body = body or {}
    client_request_id = _client_request_id(body)
    if client_request_id is None:
        return error_response("client_request_id 必须是 1-64 个字符的字符串")
    anime_id = body.get("anime_id")
    name = (body.get("name") or "").strip() or None
    query = (body.get("query") or "").strip()

    if anime_id is None and not name:
        return error_response("anime_id 或 name 至少提供一个")

    existing = await get_agent_task_by_request_id(
        db,
        user_id,
        "opinion",
        client_request_id,
    )
    if existing:
        return ok(_reused_task_payload(existing))

    try:
        session_id = await create_agent_session(
            db,
            user_id,
            "opinion",
            query or name or f"舆情诊断 #{anime_id}",
        )
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
            client_request_id=client_request_id,
            turn_seq=1,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_agent_task_by_request_id(
            db,
            user_id,
            "opinion",
            client_request_id,
        )
        if existing:
            return ok(_reused_task_payload(existing))
        raise
    submit_agent_task(task_id, "opinion")
    return ok(
        _task_payload(
            task_id,
            session_id,
            client_request_id=client_request_id,
        )
    )


@router.post("/recommend/start")
async def start_recommendation(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """创建推荐会话，并用首条用户消息启动推荐任务。"""
    body = body or {}
    client_request_id = _client_request_id(body)
    if client_request_id is None:
        return error_response("client_request_id 必须是 1-64 个字符的字符串")
    query = (body.get("query") or "").strip()
    if not query:
        return error_response("query 不能为空")

    existing = await get_agent_task_by_request_id(
        db,
        user_id,
        "recommendation",
        client_request_id,
    )
    if existing:
        return ok(_reused_task_payload(existing))

    try:
        session_id = await create_agent_session(
            db,
            user_id,
            "recommendation",
            query[:40] or "推荐 Agent 2.0",
        )
        await save_agent_message(db, session_id, "user", query)
        task_id = await create_agent_task(
            db,
            user_id,
            session_id,
            "recommendation",
            {"query": query, "history": []},
            client_request_id=client_request_id,
            turn_seq=1,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_agent_task_by_request_id(
            db,
            user_id,
            "recommendation",
            client_request_id,
        )
        if existing:
            return ok(_reused_task_payload(existing))
        raise
    submit_agent_task(task_id, "recommendation")
    return ok(
        _task_payload(
            task_id,
            session_id,
            client_request_id=client_request_id,
        )
    )


@router.post("/recommend/message")
async def recommendation_message(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """在既有推荐会话中追加消息，同一会话同时只允许一个运行中任务。"""
    body = body or {}
    client_request_id = _client_request_id(body)
    if client_request_id is None:
        return error_response("client_request_id 必须是 1-64 个字符的字符串")
    session_id = body.get("session_id")
    message = (body.get("message") or "").strip()
    if not session_id:
        return error_response("session_id 不能为空")
    if not message:
        return error_response("message 不能为空")

    existing = await get_agent_task_by_request_id(
        db,
        user_id,
        "recommendation",
        client_request_id,
    )
    if existing:
        return ok(_reused_task_payload(existing))

    session_id = int(session_id)
    session = await get_agent_session(db, user_id, session_id, for_update=True)
    if not session or session.get("agent_type") != "recommendation":
        return error_response("推荐会话不存在或无权访问", 404)

    existing = await get_agent_task_by_request_id(
        db,
        user_id,
        "recommendation",
        client_request_id,
    )
    if existing:
        return ok(_reused_task_payload(existing))

    running = await get_running_task_for_session(db, user_id, session_id)
    if running:
        return error_response("当前会话已有 Agent 任务正在执行，请稍后再发送", 409)

    session_messages = session.get("messages", [])
    history = session_messages[-8:]
    recommendation_context = extract_last_recommendation_context(session_messages)
    watch_state = reconstruct_watch_guide_state(session_messages)
    pending_offer = watch_state.get("pending_offer")
    turn_action = (
        classify_offer_reply(message, pending_offer)
        if pending_offer is not None
        else "normal"
    )
    response_mode = "conversation" if recommendation_context is not None else "recommendation"
    await save_agent_message(db, session_id, "user", message)
    turn_seq = await get_next_turn_seq(db, session_id)
    try:
        task_id = await create_agent_task(
            db,
            user_id,
            session_id,
            "recommendation",
            {
                "message": message,
                "history": history,
                "response_mode": response_mode,
                "recommendation_context": recommendation_context,
                "watch_guide_turn": {
                    "action": turn_action,
                    "state": watch_state,
                },
            },
            client_request_id=client_request_id,
            turn_seq=turn_seq,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_agent_task_by_request_id(
            db,
            user_id,
            "recommendation",
            client_request_id,
        )
        if existing:
            return ok(_reused_task_payload(existing))
        raise
    submit_agent_task(task_id, "recommendation")
    return ok(
        _task_payload(
            task_id,
            session_id,
            client_request_id=client_request_id,
        )
    )


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


@router.get("/watch-guides")
async def watch_guides(
    page: int = 1,
    page_size: int = 20,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """分页列出当前用户保存的待看番剧指南摘要。"""
    return ok(await list_watch_guide_records(db, user_id, page, page_size))


@router.get("/watch-guides/{guide_id}")
async def watch_guide_detail(
    guide_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """读取当前用户的一份完整观看指南。"""
    guide = await get_watch_guide_record(db, guide_id, user_id)
    if guide is None:
        return error_response("观看指南不存在或无权访问", 404)
    return ok(guide)


@router.delete("/watch-guides/{guide_id}")
async def remove_watch_guide(
    guide_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
):
    """只删除当前用户选中的一份观看指南。"""
    affected = await delete_watch_guide_record(db, guide_id, user_id)
    if affected == 0:
        return error_response("观看指南不存在或无权删除", 404)
    return ok(msg="删除成功")


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
    session = await get_agent_session(db, user_id, session_id, for_update=True)
    if not session:
        return error_response("会话不存在或无权删除", 404)
    running = await get_running_task_for_session(db, user_id, session_id)
    if running:
        return error_response("当前会话仍有 Agent 任务正在执行，暂时不能删除", 409)
    affected = await delete_agent_session(db, user_id, session_id)
    if affected == 0:
        return error_response("会话不存在或无权删除", 404)
    return ok(msg="删除成功")
