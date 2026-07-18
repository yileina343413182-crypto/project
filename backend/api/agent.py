# -*- coding: utf-8 -*-
"""Agent Center API blueprint."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

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

agent_bp = Blueprint("agent", __name__, url_prefix="/api/agent")


def _ok(data=None, msg="success"):
    return jsonify({"code": 200, "msg": msg, "data": data})


def _err(msg, code=400):
    return jsonify({"code": code, "msg": msg, "data": None}), code


@agent_bp.before_request
def _ensure_tables():
    init_agent_tables()


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


def _run_recommendation_task(user_id: int, session_id: int, message: str, history: list[dict] | None = None) -> dict:
    result = run_recommendation_agent(user_id, message, history=history or [])
    payload = {"session_id": session_id, **result}
    content = result["result"].get("clarifying_question") or "推荐结果已生成"
    save_agent_message(session_id, "agent", content, payload)
    return payload


@agent_bp.route("/opinion/analyze", methods=["POST"])
@jwt_required()
def analyze_opinion():
    user_id = int(get_jwt_identity())
    body = request.get_json(silent=True) or {}
    anime_id = body.get("anime_id")
    name = (body.get("name") or "").strip() or None
    query = (body.get("query") or "").strip()

    if anime_id is None and not name:
        return _err("anime_id 或 name 至少提供一个")

    session_id = create_agent_session(user_id, "opinion", query or name or f"舆情诊断 #{anime_id}")
    save_agent_message(session_id, "user", query or f"分析动漫 {name or anime_id}", {"anime_id": anime_id, "name": name})
    task_id = create_agent_task(user_id, session_id, "opinion", {"anime_id": anime_id, "name": name, "query": query})
    submit_agent_task(task_id, _run_opinion_task, session_id, anime_id, name, query)
    return _ok(_task_payload(task_id, session_id))


@agent_bp.route("/recommend/start", methods=["POST"])
@jwt_required()
def start_recommendation():
    user_id = int(get_jwt_identity())
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return _err("query 不能为空")

    session_id = create_agent_session(user_id, "recommendation", query[:40] or "推荐 Agent 2.0")
    save_agent_message(session_id, "user", query)
    task_id = create_agent_task(user_id, session_id, "recommendation", {"query": query, "history": []})
    submit_agent_task(task_id, _run_recommendation_task, user_id, session_id, query, [])
    return _ok(_task_payload(task_id, session_id))


@agent_bp.route("/recommend/message", methods=["POST"])
@jwt_required()
def recommendation_message():
    user_id = int(get_jwt_identity())
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    message = (body.get("message") or "").strip()
    if not session_id:
        return _err("session_id 不能为空")
    if not message:
        return _err("message 不能为空")

    session_id = int(session_id)
    session = get_agent_session(user_id, session_id)
    if not session or session.get("agent_type") != "recommendation":
        return _err("推荐会话不存在或无权访问", 404)

    running = get_running_task_for_session(user_id, session_id)
    if running:
        return _err("当前会话已有 Agent 任务正在执行，请稍后再发送", 409)

    save_agent_message(session_id, "user", message)
    history = session.get("messages", [])[-8:]
    task_id = create_agent_task(user_id, session_id, "recommendation", {"message": message, "history": history})
    submit_agent_task(task_id, _run_recommendation_task, user_id, session_id, message, history)
    return _ok(_task_payload(task_id, session_id))


@agent_bp.route("/tasks/<int:task_id>", methods=["GET"])
@jwt_required()
def task_detail(task_id):
    user_id = int(get_jwt_identity())
    task = get_agent_task(user_id, task_id)
    if not task:
        return _err("任务不存在或无权访问", 404)
    return _ok(task)


@agent_bp.route("/sessions", methods=["GET"])
@jwt_required()
def sessions():
    user_id = int(get_jwt_identity())
    return _ok(list_agent_sessions(user_id))


@agent_bp.route("/sessions/<int:session_id>", methods=["GET"])
@jwt_required()
def session_detail(session_id):
    user_id = int(get_jwt_identity())
    session = get_agent_session(user_id, session_id)
    if not session:
        return _err("会话不存在或无权访问", 404)
    return _ok(session)


@agent_bp.route("/sessions/<int:session_id>", methods=["DELETE"])
@jwt_required()
def remove_session(session_id):
    user_id = int(get_jwt_identity())
    affected = delete_agent_session(user_id, session_id)
    if affected == 0:
        return _err("会话不存在或无权删除", 404)
    return _ok(msg="删除成功")