# -*- coding: utf-8 -*-
"""推荐会话的有限动作路由与无检索闲聊回答。"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from backend.agents.model_factory import get_chat_model
from backend.agents.prompt_security import inspect_untrusted_text
from backend.agents.recommend_context import compact_history
from backend.agents.schemas import RecommendationTurnDecision
from backend.config import (
    LLM_MODEL,
    RECOMMEND_FOLLOWUP_MAX_TOKENS,
    RECOMMEND_LLM_TIMEOUT,
)
from backend.prompts.registry import get_prompt, prompt_trace


_RECOMMEND_PATTERNS = (
    r"(?:推荐(?!结果|理由|原因)|安利)(?:一下|几部|三部|些)?",
    r"(?:再|重新|继续)(?:给我)?(?:推荐|来)(?:几部|三部|一批|些)?",
    r"(?:换|再来)(?:几部|三部|一批|一组|些)",
    r"(?:想|要|准备)(?:看|追)(?:一部|几部|三部|点|些)?",
    r"(?:找|挑|选)(?:一部|几部|三部|点|些)?(?:番|动漫|动画|作品)",
    r"(?:还有|有没有)(?:别的|其他|类似的)?(?:推荐|番|动漫|动画|作品)",
    r"类似.+(?:的|吗|呢|作品|番|动漫|动画)",
    r"不知道(?:该|要)?看什么",
)
_NO_RECOMMEND_PATTERNS = (
    r"(?:不要|不用|别|不需要)(?:再)?推荐",
    r"不想要推荐",
    r"(?:不想|不要|不准备)(?:看|追)(?:番|动漫|动画)?",
)
_FOLLOWUP_PATTERNS = (
    r"为什么(?:会|要)?推荐",
    r"(?:这|那|它|第一|第二|第三)(?:部|个)?.*(?:剧情|角色|人物|结局|平台|哪里看|多少集|适合|讲什么)",
    r"(?:剧情|角色|人物|结局|观看顺序|多少集|哪里看|播放平台)(?:是|有|呢|吗|怎么样)",
)
_WATCH_GUIDE_PATTERNS = (
    r"(?:加入|添加|放入|保存到).*(?:待看番剧指南|观看指南|待看列表|指南)",
    r"(?:帮我|请|麻烦).*(?:加入|添加|保存).*(?:待看|指南)",
)
_CHAT_ONLY = {
    "你好", "你好呀", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "你在吗",
    "谢谢", "谢谢你", "感谢", "好的谢谢", "晚安", "早上好", "下午好", "晚上好",
    "你是谁", "你能做什么", "辛苦了",
}
_SOCIAL_CHAT_PATTERNS = (
    r"天气(?:怎么样|如何|不错|真好|真差)",
    r"(?:陪我|和我)?聊(?:会儿|聊天)",
    r"讲个笑话",
    r"你(?:今天|最近)?怎么样",
)


def _normalize(value: str) -> str:
    return re.sub(r"[\s，。！？!?、；;,.]+", "", str(value or "").casefold())


def _metadata_result(message: dict) -> dict:
    metadata = message.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            return {}
    if not isinstance(metadata, dict):
        return {}
    result = metadata.get("result") or {}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return {}
    if not isinstance(result, dict):
        return {}
    nested = result.get("result")
    return nested if isinstance(nested, dict) else result


def has_pending_preference(history: list[dict] | None) -> bool:
    """最近一次 Agent 消息若在等待偏好回答，本轮应恢复推荐图。"""
    for message in reversed(history or []):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            return False
        if message.get("role") not in {"agent", "assistant"}:
            continue
        result = _metadata_result(message)
        return bool(result.get("need_clarification") and result.get("preference_stage"))
    return False


def _compact_router_context(context: dict | None) -> dict:
    """路由只发送作品名和理由，不发送评论正文、证据或检索诊断。"""
    data = context if isinstance(context, dict) else {}
    recommendations = []
    for item in (data.get("recommendations") or [])[:3]:
        if not isinstance(item, dict):
            continue
        recommendations.append({
            "anime_id": item.get("anime_id"),
            "name": str(item.get("name") or "")[:255],
            "reason": str(item.get("reason") or "")[:1000],
            "match_tags": [
                str(tag)[:80]
                for tag in (item.get("match_tags") or [])[:8]
            ],
        })
    return {
        "recommendations": recommendations,
        "active_target": data.get("active_target") or None,
    }


def _compact_watch_state(state: dict | None) -> dict:
    data = state if isinstance(state, dict) else {}
    pending = data.get("pending_offer") or {}
    return {
        "pending_offer": {
            "offer_id": str(pending.get("offer_id") or "")[:128],
            "anime": pending.get("anime") or None,
        } if pending else None,
        "active_target": data.get("active_target") or None,
    }


def route_recommendation_turn(
    query: str,
    history: list[dict] | None = None,
    *,
    has_recommendation_context: bool = False,
    recommendation_context: dict | None = None,
    memory_context: dict | None = None,
    watch_guide_state: dict | None = None,
    has_attachment: bool = False,
    initial_turn: bool = False,
) -> dict[str, Any]:
    """极简寒暄本地直返，其余输入统一经过一次结构化模型路由。"""
    text = str(query or "").strip()
    normalized = _normalize(text)
    context = recommendation_context or {}
    memories = memory_context or {}
    state = watch_guide_state or {}
    if normalized in _CHAT_ONLY and not has_attachment and not state.get("pending_offer"):
        return RecommendationTurnDecision(
            action="chat",
            reason="极简问候或礼貌性对话",
            matched_signals=[normalized or "empty"],
            confidence=1.0,
        ).model_dump()

    has_context = bool(has_recommendation_context or context)
    pending_preference = has_pending_preference(history)
    query_check = inspect_untrusted_text(text, source="user_input", max_chars=1200)
    history_check = inspect_untrusted_text(
        json.dumps(compact_history(history), ensure_ascii=False),
        source="conversation_history",
        max_chars=3500,
    )
    context_check = inspect_untrusted_text(
        json.dumps(_compact_router_context(context), ensure_ascii=False),
        source="recommendation_context",
        max_chars=6500,
    )
    memory_check = inspect_untrusted_text(
        json.dumps(memories, ensure_ascii=False),
        source="recommendation_context_memory",
        max_chars=6000,
    )
    state_check = inspect_untrusted_text(
        json.dumps(_compact_watch_state(state), ensure_ascii=False),
        source="watch_guide_state",
        max_chars=2500,
    )
    prompt_template = get_prompt("recommendation_router")
    trace = prompt_trace(
        "recommendation_router",
        LLM_MODEL,
        0,
        False,
        prompt=prompt_template,
    )
    model = get_chat_model(
        0,
        timeout=RECOMMEND_LLM_TIMEOUT,
        max_tokens=320,
    )
    if model is None:
        decision = _fallback_turn_decision(
            text,
            has_context=has_context,
            has_attachment=has_attachment,
            initial_turn=initial_turn,
            pending_preference=pending_preference,
            watch_guide_state=state,
        )
        trace["fallback"] = True
        decision["prompt_trace"] = trace
        return decision

    prompt = prompt_template.render(
        query=query_check["sanitized_text"],
        history=history_check["sanitized_text"],
        memory_context=memory_check["sanitized_text"],
        recommendation_context=context_check["sanitized_text"],
        watch_guide_state=state_check["sanitized_text"],
        has_attachment=str(bool(has_attachment)).lower(),
        initial_turn=str(bool(initial_turn)).lower(),
        pending_preference=str(bool(pending_preference)).lower(),
    )
    try:
        response = model.with_structured_output(RecommendationTurnDecision).invoke([
            ("system", prompt_template.render_system()),
            ("human", prompt),
        ])
        if hasattr(response, "model_dump"):
            payload = response.model_dump()
        elif isinstance(response, dict):
            payload = dict(response)
        else:
            payload = dict(getattr(response, "__dict__", {}))
        decision = RecommendationTurnDecision(**payload).model_dump()
        if decision["action"] == "followup" and not has_context:
            decision.update(
                action="chat",
                reason="没有可追问的已保存推荐结果，按普通聊天处理",
                target_anime_name="",
            )
        if decision["action"] == "recommendation" and pending_preference:
            decision["resume_preference"] = True
        decision["target_anime_name"] = str(
            decision.get("target_anime_name") or ""
        ).strip()[:255]
        decision["matched_signals"] = [
            str(value)[:120]
            for value in (decision.get("matched_signals") or [])[:5]
            if str(value).strip()
        ]
        decision["prompt_trace"] = trace
        return decision
    except Exception as exc:
        decision = _fallback_turn_decision(
            text,
            has_context=has_context,
            has_attachment=has_attachment,
            initial_turn=initial_turn,
            pending_preference=pending_preference,
            watch_guide_state=state,
        )
        trace["fallback"] = True
        decision["prompt_trace"] = trace
        decision["fallback_reason"] = f"{type(exc).__name__}: {exc}"
        return decision


def _fallback_turn_decision(
    text: str,
    *,
    has_context: bool,
    has_attachment: bool,
    initial_turn: bool,
    pending_preference: bool,
    watch_guide_state: dict,
) -> dict[str, Any]:
    """仅在路由模型不可用时使用，且指南意图优先于“推荐”字样。"""
    pending = (watch_guide_state or {}).get("pending_offer") or {}
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in _WATCH_GUIDE_PATTERNS):
        action, reason, resume = "add_watch_guide", "降级规则识别到加入待看指南", False
    elif pending and _normalize(text) in {"可以", "好的", "好", "加入", "保存", "需要", "要"}:
        action, reason, resume = "add_watch_guide", "降级规则识别到指南确认", False
    elif any(re.search(pattern, text, re.IGNORECASE) for pattern in _NO_RECOMMEND_PATTERNS):
        action, reason, resume = "chat", "降级规则识别到非推荐请求", False
    elif pending_preference:
        action, reason, resume = "recommendation", "降级恢复未完成的偏好问答", True
    elif has_attachment:
        action, reason, resume = "recommendation", "降级按图片推荐处理", False
    elif any(re.search(pattern, text, re.IGNORECASE) for pattern in _RECOMMEND_PATTERNS):
        action, reason, resume = "recommendation", "降级规则识别到新推荐请求", False
    elif has_context and any(re.search(pattern, text, re.IGNORECASE) for pattern in _FOLLOWUP_PATTERNS):
        action, reason, resume = "followup", "降级规则识别到推荐结果追问", False
    elif has_context:
        action, reason, resume = "followup", "降级沿用已有推荐上下文", False
    elif initial_turn:
        action, reason, resume = "recommendation", "降级按推荐页首轮输入处理", False
    else:
        action, reason, resume = "chat", "降级为普通聊天", False
    return RecommendationTurnDecision(
        action=action,
        reason=reason,
        matched_signals=["router_model_fallback"],
        resume_preference=resume,
        confidence=0.35,
    ).model_dump()


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return re.sub(r"<think>.*?</think>", "", str(content or ""), flags=re.DOTALL).strip()


def _chat_fallback(query: str) -> str:
    normalized = _normalize(query)
    if normalized in {"谢谢", "谢谢你", "感谢", "好的谢谢", "辛苦了"}:
        return "不客气！想继续聊动漫，或者需要新的推荐时，直接告诉我就好。"
    if normalized in {"你是谁", "你能做什么"}:
        return "我是动漫推荐助手。可以先陪你聊聊，也会在你明确提出推荐需求时再检索并筛选作品。"
    return "你好！可以先随便聊聊；如果想找番，也可以告诉我喜欢的题材、氛围或想避开的内容。"


def run_recommendation_chat(
    query: str,
    history: list[dict] | None = None,
    memory_context: dict | None = None,
    on_text_delta: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """生成不访问候选池和 RAG 的普通对话回答。"""
    query_check = inspect_untrusted_text(query, source="user_input", max_chars=1200)
    safe_query = query_check["sanitized_text"]
    normalized = _normalize(safe_query)
    if normalized in _CHAT_ONLY:
        answer = _chat_fallback(safe_query)
        if on_text_delta is not None:
            on_text_delta(answer)
        return {"response_mode": "conversation", "answer": answer, "fallback": False}

    prompt_template = get_prompt("recommendation_chat")
    compact = compact_history(history)
    history_check = inspect_untrusted_text(
        json.dumps(compact, ensure_ascii=False),
        source="conversation_history",
        max_chars=5000,
    )
    memory_check = inspect_untrusted_text(
        json.dumps(memory_context or {}, ensure_ascii=False),
        source="recommendation_context_memory",
        max_chars=6000,
    )
    trace = prompt_trace(
        "recommendation_chat",
        LLM_MODEL,
        0,
        False,
        prompt=prompt_template,
    )
    model = get_chat_model(
        0.4,
        timeout=RECOMMEND_LLM_TIMEOUT,
        max_tokens=RECOMMEND_FOLLOWUP_MAX_TOKENS,
    )
    if model is None:
        trace["fallback"] = True
        return {
            "response_mode": "conversation",
            "answer": _chat_fallback(safe_query),
            "prompt_trace": trace,
            "fallback": True,
        }

    prompt = prompt_template.render(
        query=safe_query,
        history=history_check["sanitized_text"],
        memory_context=memory_check["sanitized_text"],
    )
    try:
        answer = _response_text(
            model.invoke([
                ("system", prompt_template.render_system()),
                ("human", prompt),
            ])
        )
        if not answer:
            raise ValueError("LLM returned an empty chat answer")
        if on_text_delta is not None:
            on_text_delta(answer)
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
            "answer": _chat_fallback(safe_query),
            "prompt_trace": trace,
            "fallback": True,
            "fallback_reason": f"{type(exc).__name__}: {exc}",
        }
