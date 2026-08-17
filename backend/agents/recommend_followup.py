# -*- coding: utf-8 -*-
"""推荐完成后的普通文本追问。

首轮推荐和偏好澄清仍由 ``recommend_graph`` 负责；本模块只读取既有会话，
不会调用推荐工具，也不会写入长期偏好。
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.agents.model_factory import get_chat_model
from backend.agents.prompt_security import inspect_untrusted_text
from backend.agents.recommend_context import compact_history
from backend.config import (
    LLM_MODEL,
    RECOMMEND_FOLLOWUP_MAX_TOKENS,
    RECOMMEND_LLM_TIMEOUT,
)
from backend.prompts.registry import get_prompt, prompt_trace


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _recommendation_result(message: dict) -> dict:
    """兼容当前和旧任务元数据层级，取出一次结构化推荐结果。"""
    metadata = _as_dict(message.get("metadata"))
    result = _as_dict(metadata.get("result"))
    nested = _as_dict(result.get("result"))
    return nested or result


def _compact_recommendation(result: dict) -> dict:
    """只保留回答追问所需字段，避免把执行诊断和整条证据链重复送入模型。"""
    recommendations = []
    for item in (result.get("recommendations") or [])[:3]:
        if not isinstance(item, dict):
            continue
        evidence = _as_dict(item.get("evidence"))
        comments = []
        for comment in (evidence.get("comments") or [])[:3]:
            if not isinstance(comment, dict):
                continue
            comments.append(
                {
                    "content": str(comment.get("content") or "")[:240],
                    "sentiment_label": comment.get("sentiment_label", ""),
                    "likes": comment.get("likes", 0),
                    "platform": comment.get("platform", ""),
                }
            )
        recommendations.append(
            {
                "anime_id": item.get("anime_id"),
                "name": item.get("name", ""),
                "platform": item.get("platform", ""),
                "reason": str(item.get("reason") or "")[:800],
                "match_tags": (item.get("match_tags") or [])[:10],
                "sentiment": evidence.get("sentiment") or {},
                "topics": (evidence.get("topics") or [])[:8],
                "representative_comments": comments,
            }
        )
    return {"recommendations": recommendations}


def extract_last_recommendation_context(messages: list[dict] | None) -> dict | None:
    """找到会话中最近一次成功推荐；澄清问题不视为推荐完成。"""
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") not in {"agent", "assistant"}:
            continue
        result = _recommendation_result(message)
        if result.get("need_clarification"):
            continue
        compact = _compact_recommendation(result)
        if compact["recommendations"]:
            return compact
    return None


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        content = "\n".join(parts)
    text = str(content or "")
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _fallback_answer(query: str, context: dict) -> str:
    recommendations = context.get("recommendations") or []
    active_target = _as_dict(context.get("active_target"))
    active_name = str(active_target.get("name") or "").casefold()
    normalized_query = query.casefold()
    active_matches = [
        item
        for item in recommendations
        if active_name
        and str(item.get("name") or "").casefold() == active_name
    ]
    if active_name and not active_matches:
        active_label = active_target.get("name") or "这部作品"
        return (
            f"当前详细对话模型暂时不可用，而《{active_label}》不在刚才已保存的推荐结果中，"
            "所以我现在无法可靠补充它的剧情、角色或观看信息，也不会用其他作品的资料代替回答。"
            "模型服务恢复后，可以继续追问这部作品。"
        )
    matched = [
        item
        for item in recommendations
        if (
            active_name
            and str(item.get("name") or "").casefold() == active_name
        )
        or str(item.get("name") or "").casefold() in normalized_query
    ]
    selected = matched or recommendations
    details = []
    for item in selected[:3]:
        name = item.get("name") or "这部作品"
        reason = str(item.get("reason") or "").strip()
        details.append(f"《{name}》\n{reason}" if reason else f"《{name}》是刚才推荐列表中的作品。")
    body = "\n\n".join(details)
    return (
        "当前详细对话模型暂时不可用。以下先依据刚才已经生成并保存的推荐信息回答：\n\n"
        f"{body}\n\n"
        "由于本地降级模式无法可靠补充推荐记录之外的设定或剧情细节，我没有猜测未知信息；"
        "模型服务恢复后，可以继续围绕剧情、人物、观看顺序或相似作品追问。"
    )


def run_recommendation_followup(
    query: str,
    history: list[dict] | None,
    recommendation_context: dict,
) -> dict:
    """基于最近推荐与对话历史生成详细普通文本，不返回推荐 Schema。"""
    prompt_template = get_prompt("recommendation_followup")
    compact = compact_history(history)
    query_check = inspect_untrusted_text(query, source="user_input", max_chars=1200)
    history_check = inspect_untrusted_text(
        json.dumps(compact, ensure_ascii=False),
        source="conversation_history",
        max_chars=4000,
    )
    recommendation_check = inspect_untrusted_text(
        json.dumps(recommendation_context, ensure_ascii=False),
        source="recommendation_context",
        max_chars=6000,
    )
    security = {
        "risk": max(
            (query_check, history_check, recommendation_check),
            key=lambda item: item["score"],
        )["risk"],
        "flags": sorted(
            {
                flag
                for inspection in (query_check, history_check, recommendation_check)
                for flag in inspection["flags"]
            }
        ),
    }
    trace = prompt_trace(
        "recommendation_followup",
        LLM_MODEL,
        0,
        False,
        prompt=prompt_template,
    )
    trace["security"] = security

    model = get_chat_model(
        0.3,
        timeout=RECOMMEND_LLM_TIMEOUT,
        max_tokens=RECOMMEND_FOLLOWUP_MAX_TOKENS,
    )
    if model is None:
        trace["fallback"] = True
        return {
            "response_mode": "conversation",
            "answer": _fallback_answer(query_check["sanitized_text"], recommendation_context),
            "prompt_trace": trace,
            "fallback": True,
        }

    prompt = prompt_template.render(
        query=query_check["sanitized_text"],
        history=history_check["sanitized_text"],
        recommendations=recommendation_check["sanitized_text"],
    )
    try:
        answer = _response_text(
            model.invoke(
                [
                    ("system", prompt_template.render_system()),
                    ("human", prompt),
                ]
            )
        )
        if not answer:
            raise ValueError("LLM returned an empty follow-up answer")
        return {
            "response_mode": "conversation",
            "answer": answer,
            "prompt_trace": trace,
            "fallback": False,
        }
    except Exception as exc:
        trace["fallback"] = True
        return {
            "response_mode": "conversation",
            "answer": _fallback_answer(query_check["sanitized_text"], recommendation_context),
            "prompt_trace": trace,
            "fallback": True,
            "fallback_reason": f"{type(exc).__name__}: {exc}",
        }
