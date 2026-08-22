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


def route_recommendation_turn(
    query: str,
    history: list[dict] | None = None,
    *,
    has_recommendation_context: bool = False,
    has_attachment: bool = False,
    initial_turn: bool = False,
) -> dict[str, Any]:
    """按确定性优先级选择唯一动作；本函数不允许调用检索或写偏好。"""
    text = str(query or "").strip()
    normalized = _normalize(text)

    if has_attachment:
        decision = RecommendationTurnDecision(
            action="recommendation",
            reason="图片附件需要进入推荐分析流程",
            matched_signals=["attachment"],
        )
    elif any(re.search(pattern, text, re.IGNORECASE) for pattern in _NO_RECOMMEND_PATTERNS):
        decision = RecommendationTurnDecision(
            action="chat",
            reason="用户明确表示本轮不需要推荐",
            matched_signals=["recommendation_opt_out"],
        )
    else:
        recommendation_signals = [
            pattern
            for pattern in _RECOMMEND_PATTERNS
            if re.search(pattern, text, re.IGNORECASE)
        ]
        if recommendation_signals:
            decision = RecommendationTurnDecision(
                action="recommendation",
                reason="用户明确提出新的推荐请求",
                matched_signals=recommendation_signals[:3],
            )
        elif normalized in _CHAT_ONLY or any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in _SOCIAL_CHAT_PATTERNS
        ):
            decision = RecommendationTurnDecision(
                action="chat",
                reason="低信息问候或礼貌性对话",
                matched_signals=[normalized or "empty"],
            )
        elif has_pending_preference(history):
            decision = RecommendationTurnDecision(
                action="preference_answer",
                reason="正在回答上一轮偏好问题",
                matched_signals=["pending_preference"],
            )
        elif has_recommendation_context and any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in _FOLLOWUP_PATTERNS
        ):
            decision = RecommendationTurnDecision(
                action="followup",
                reason="问题指向已有推荐结果",
                matched_signals=["recommendation_followup"],
            )
        elif has_recommendation_context:
            decision = RecommendationTurnDecision(
                action="followup",
                reason="已有推荐上下文，按普通追问处理",
                matched_signals=["existing_recommendation_context"],
            )
        elif initial_turn:
            decision = RecommendationTurnDecision(
                action="recommendation",
                reason="推荐页面首轮的非社交输入按筛选条件处理",
                matched_signals=["initial_recommendation_query"],
            )
        else:
            decision = RecommendationTurnDecision(
                action="chat",
                reason="未检测到明确推荐请求",
                matched_signals=["no_recommendation_signal"],
            )
    return decision.model_dump()


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
        max_chars=3000,
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
