# -*- coding: utf-8 -*-
"""推荐 Agent 2.0 的确定性 LangGraph 工作流。

主链路为：加载/收集偏好 → 构建候选 → 检索并清洗证据 → 规划只读工具 →
结构化生成 → 业务校验/有限修复 → 成功或本地降级。条件路由和循环次数都
有明确上限，完整状态可由 Checkpoint 恢复。
"""

from __future__ import annotations

import json
import logging
import operator
import os
import re
import sqlite3
import time
from functools import lru_cache
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.redis import RedisSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from langgraph.prebuilt import ToolNode

from backend.agents.memory import get_user_preferences, update_user_preferences
from backend.agents.model_factory import get_chat_model
from backend.agents.prompt_security import (
    inspect_untrusted_text,
    sanitize_evidence_map,
    sanitize_preference_suggestions,
)
from backend.agents.recommend_context import (
    evidence_field_coverage,
    evidence_field_gaps,
    pack_recommendation_context,
    retrieve_candidate_evidence,
    verified_platform_availability,
)
from backend.agents.schemas import AgentStep, RecommendationResponseSchema
from backend.agents.tools import RECOMMEND_TOOLS, build_candidate_pool, timed_step
from backend.config import (
    AGENT_REDIS_KEY_PREFIX,
    CELERY_BROKER_SOCKET_TIMEOUT,
    LLM_MODEL,
    RECOMMEND_CANDIDATE_LIMIT,
    RECOMMEND_CHECKPOINT_BACKEND,
    RECOMMEND_CHECKPOINT_DB,
    RECOMMEND_CHECKPOINT_REDIS_URL,
    RECOMMEND_CHECKPOINT_SQLITE_FALLBACK,
    RECOMMEND_CHECKPOINT_TTL_MINUTES,
    RECOMMEND_GRAPH_RECURSION_LIMIT,
    RECOMMEND_LLM_MAX_TOKENS,
    RECOMMEND_LLM_REPAIR_MAX_TOKENS,
    RECOMMEND_LLM_REPAIR_RETRIES,
    RECOMMEND_LLM_TIMEOUT,
    RECOMMEND_EVIDENCE_CANDIDATES,
    RECOMMEND_REDIS_MAX_CONNECTIONS,
    RECOMMEND_TOOL_MAX_ROUNDS,
)
from backend.prompts.registry import get_prompt, prompt_trace

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """所有图节点共享且可序列化的状态；节点只返回自己更新的字段。"""

    user_id: int
    query: str
    excluded_anime_ids: list[int]
    force_recommendation: bool
    safe_query: str
    search_query: str
    history: list[dict]
    input_security: dict[str, Any]
    evidence_security: dict[str, Any]
    preferences: dict[str, Any]
    preference_updates: dict[str, list[Any]]
    skipped_slots: list[str]
    next_preference_slot: str
    preference_progress: dict[str, Any]
    candidates: list[dict]
    eligible_candidates: list[dict]
    evidence_map: dict[int, list[dict]]
    evidence_coverage: dict[str, Any]
    packed_candidates: list[dict]
    context_budget: dict[str, Any]
    prompt_trace: dict[str, Any]
    prompt: str
    llm_data: dict[str, Any]
    validation_errors: list[str]
    repair_attempts: int
    fallback_reason: str
    result: dict[str, Any]
    fallback: bool
    messages: Annotated[list[BaseMessage], add_messages]
    tool_rounds: int
    remaining_steps: RemainingSteps
    agent_steps: Annotated[list[dict[str, Any]], operator.add]


# 问卷按固定顺序补齐四类偏好；用户可以跳过单项或直接结束问卷。
QUESTIONNAIRE_SLOTS = (
    "preferred_genres",
    "preferred_moods",
    "likes",
    "dislikes",
)

QUESTIONS = {
    "preferred_genres": "第一步，你更想看什么题材？例如科幻、悬疑、恋爱、日常或奇幻。",
    "preferred_moods": "第二步，你希望整体是什么氛围？例如轻松治愈、热血、烧脑或偏沉重。",
    "likes": "第三步，你最看重哪些特点？例如剧情扎实、角色成长、群像塑造或音乐表现；没有特别要求也可以直接说。",
    "dislikes": "最后一步，有没有明确想避开的内容？例如后宫、过度虐心、节奏拖沓；没有可以回答“没有”。",
}

GENRE_TERMS = (
    "科幻", "机甲", "恋爱", "校园", "悬疑", "推理", "奇幻", "冒险", "日常",
    "喜剧", "战斗", "音乐", "运动", "历史", "职场", "群像", "公路", "魔法",
)
MOOD_TERMS = (
    "轻松", "治愈", "热血", "压抑", "温馨", "搞笑", "紧张", "黑暗",
    "浪漫", "感动", "刺激", "平静", "烧脑", "沉重", "爽快", "温柔",
)
SKIP_ANSWERS = {
    "没有", "没有特别要求", "无", "都可以", "随便", "跳过", "不介意", "暂无",
}
SKIP_ALL_ANSWERS = {
    "直接推荐", "不用再问了", "跳过后续", "这些就够了",
}


# ===== 纯函数辅助：解析回答、合并偏好和计算问卷进度 =====

def _recommend_helpers():
    # Imported lazily to preserve the public recommend_agent module as the
    # compatibility facade without creating an import cycle.
    from backend.agents.recommend_agent import (
        _local_result,
        _plain_step,
        _render_bounded_prompt,
        _structured,
        _validate_recommendation,
    )

    return (
        _local_result,
        _plain_step,
        _render_bounded_prompt,
        _structured,
        _validate_recommendation,
    )


def _normalize(value: str) -> str:
    return "".join(str(value or "").lower().split()).strip("，。！？!? ")


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _split_answer(value: str) -> list[str]:
    cleaned = re.sub(
        r"^(?:我(?:比较|更)?(?:喜欢|偏好|想看)|希望|最好是|想要|偏好)\s*",
        "",
        str(value or "").strip(),
    )
    parts = re.split(r"[，,、；;/\n]|\s+(?:和|或)\s+", cleaned)
    return _unique([part.strip("。！？!? ") for part in parts if part.strip()])


def _extract_dislikes(value: str) -> list[str]:
    matches = re.findall(
        r"(?:不要|不想看|不喜欢|避开|讨厌|雷点是)([^，。；;！!?]{1,24})",
        str(value or ""),
    )
    return _unique(matches)


def _extract_slot(slot: str, answer: str) -> list[str]:
    """按当前问卷槽位解析用户回答，优先识别预定义题材/氛围词。"""
    normalized = _normalize(answer)
    if normalized in SKIP_ANSWERS or normalized in SKIP_ALL_ANSWERS:
        return []
    if slot == "preferred_genres":
        known = [term for term in GENRE_TERMS if term in answer]
        return known or _split_answer(answer)
    if slot == "preferred_moods":
        known = [term for term in MOOD_TERMS if term in answer]
        return known or _split_answer(answer)
    if slot == "dislikes":
        return _extract_dislikes(answer) or _split_answer(answer)
    return _split_answer(answer)


def _result_metadata(message: dict) -> dict:
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return {}
    result = metadata.get("result") or {}
    return result if isinstance(result, dict) else {}


def _pending_slot(history: list[dict]) -> str:
    """从历史中找到 Agent 上一次要求用户回答的偏好槽位。"""
    for message in reversed(history or []):
        if message.get("role") not in {"agent", "assistant"}:
            continue
        stage = _result_metadata(message).get("preference_stage")
        return stage if stage in QUESTIONNAIRE_SLOTS else ""
    return ""


def _skipped_slots(history: list[dict], pending: str, query: str) -> set[str]:
    """结合历史问答和本轮输入恢复用户显式跳过的槽位。"""
    skipped: set[str] = set()
    messages = history or []
    for index, message in enumerate(messages):
        if message.get("role") not in {"agent", "assistant"}:
            continue
        slot = _result_metadata(message).get("preference_stage")
        if slot not in QUESTIONNAIRE_SLOTS:
            continue
        answer = next(
            (
                str(item.get("content", ""))
                for item in messages[index + 1:]
                if item.get("role") == "user"
            ),
            "",
        )
        if _normalize(answer) in SKIP_ANSWERS:
            skipped.add(slot)

    normalized = _normalize(query)
    if pending and normalized in SKIP_ANSWERS:
        skipped.add(pending)
    if normalized in SKIP_ALL_ANSWERS:
        skipped.update(QUESTIONNAIRE_SLOTS)
    return skipped


def _merge_preferences(preferences: dict, updates: dict[str, list[Any]]) -> dict:
    merged = {
        key: list(preferences.get(key, []))
        for key in (*QUESTIONNAIRE_SLOTS, "feedback")
    }
    for key, values in updates.items():
        for value in values:
            if value and value not in merged[key]:
                merged[key].append(value)
    return merged


def _progress(preferences: dict, skipped: set[str]) -> tuple[str, dict[str, Any]]:
    """返回下一个待询问槽位和前端展示所需的完成进度。"""
    completed = [
        slot
        for slot in QUESTIONNAIRE_SLOTS
        if preferences.get(slot) or slot in skipped
    ]
    next_slot = next(
        (
            slot
            for slot in QUESTIONNAIRE_SLOTS
            if not preferences.get(slot) and slot not in skipped
        ),
        "",
    )
    return next_slot, {
        "completed": completed,
        "completed_count": len(completed),
        "total": len(QUESTIONNAIRE_SLOTS),
        "current": next_slot,
    }


def _preference_update_payload(
    state: AgentState,
    *,
    applied: dict | None = None,
    suggested: dict | None = None,
    preferences: dict | None = None,
) -> dict[str, Any]:
    applied_updates = dict(
        state.get("preference_updates", {})
        if applied is None
        else applied
    )
    current_preferences = (
        state.get("preferences", {})
        if preferences is None
        else preferences
    )
    return {
        **applied_updates,
        "applied": applied_updates,
        "suggested": dict(suggested or {}),
        "last_query": state.get("query", ""),
        "preferences": current_preferences,
    }


def _search_query(query: str, preferences: dict) -> str:
    """保持本轮问题原文；长期偏好通过独立字段只进入一次。"""
    return str(query or "").strip()


def _eligible_candidates(state: AgentState) -> list[dict]:
    """实际图状态使用 eligible；兼容旧检查点和直接调用的单元测试。"""
    if "eligible_candidates" in state:
        return state.get("eligible_candidates", [])
    return state.get("candidates", [])[:RECOMMEND_EVIDENCE_CANDIDATES]


# ===== 阶段一：输入安全检查与偏好问卷 =====

def load_preferences(state: AgentState) -> dict:
    """读取用户已持久化的长期偏好。"""
    preferences, step = timed_step(
        "get_user_preferences",
        get_user_preferences,
        state["user_id"],
    )
    return {"preferences": preferences or {}, "agent_steps": [step]}


def inspect_user_input(state: AgentState) -> dict:
    """检查本轮输入和近期历史；高风险时禁用工具规划与偏好写入。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    query_inspection = inspect_untrusted_text(
        state.get("query", ""),
        source="user_input",
        max_chars=1200,
    )
    history_text = "\n".join(
        str(message.get("content", ""))
        for message in state.get("history", [])[-8:]
        if isinstance(message, dict)
    )
    history_inspection = inspect_untrusted_text(
        history_text,
        source="conversation_history",
        max_chars=4000,
    )
    inspections = (query_inspection, history_inspection)
    highest = max(inspections, key=lambda item: item["score"])
    flags = sorted(
        {
            flag
            for inspection in inspections
            for flag in inspection["flags"]
        }
    )
    security = {
        "risk": highest["risk"],
        "flags": flags,
        "query_truncated": query_inspection["truncated"],
        "history_truncated": history_inspection["truncated"],
        "guardrail_action": (
            "disable_tools_and_memory_write"
            if highest["risk"] == "high"
            else "monitor"
            if highest["risk"] == "medium"
            else "allow"
        ),
    }
    return {
        "safe_query": query_inspection["sanitized_text"],
        "input_security": security,
        "agent_steps": [
            plain_step(
                "inspect_user_input",
                "degraded" if flags else "success",
                (
                    f"risk={security['risk']}; "
                    f"flags={','.join(flags) or 'none'}"
                ),
            )
        ],
    }


def collect_preferences(state: AgentState) -> dict:
    """从安全输入中提取偏好，去重合并后写入长期记忆。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    query = state.get("safe_query") or state["query"]
    high_risk = state.get("input_security", {}).get("risk") == "high"
    pending = _pending_slot(state.get("history", []))
    skipped = (
        set()
        if high_risk
        else _skipped_slots(state.get("history", []), pending, query)
    )
    updates: dict[str, list[Any]] = {}

    genres = [] if high_risk else [term for term in GENRE_TERMS if term in query]
    moods = [] if high_risk else [term for term in MOOD_TERMS if term in query]
    dislikes = [] if high_risk else _extract_dislikes(query)
    if genres:
        updates["preferred_genres"] = genres
    if moods:
        updates["preferred_moods"] = moods
    if dislikes:
        updates["dislikes"] = dislikes
    if pending and not high_risk:
        values = _extract_slot(pending, query)
        if values:
            updates[pending] = _unique(updates.get(pending, []) + values)

    preferences = _merge_preferences(state.get("preferences", {}), updates)
    status, detail = (
        ("guarded", "high-risk input cannot update persistent preferences")
        if high_risk
        else ("skipped", "no new preference values")
    )
    if updates:
        try:
            preferences = update_user_preferences(state["user_id"], updates)
            status, detail = "success", "updated " + ",".join(sorted(updates))
        except Exception as exc:
            status, detail = "error", str(exc)

    return {
        "preferences": preferences,
        "preference_updates": updates,
        "skipped_slots": sorted(skipped),
        "search_query": _search_query(query, preferences),
        "agent_steps": [plain_step("collect_preferences", status, detail)],
    }


def assess_preferences(state: AgentState) -> dict:
    """判断问卷是否完整，并决定继续提问还是进入候选检索。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    next_slot, progress = _progress(
        state.get("preferences", {}),
        set(state.get("skipped_slots", [])),
    )
    detail = f"next={next_slot}" if next_slot else "questionnaire complete"
    return {
        "next_preference_slot": next_slot,
        "preference_progress": progress,
        "agent_steps": [plain_step("assess_preferences", "success", detail)],
    }


def route_preferences(state: AgentState) -> str:
    """偏好未补齐时结束本轮并提问，否则继续构建候选。"""
    if state.get("force_recommendation"):
        return "candidates"
    return "ask_preference" if state.get("next_preference_slot") else "candidates"


def ask_preference(state: AgentState) -> dict:
    """返回单个澄清问题；本轮不生成推荐结果。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    slot = state["next_preference_slot"]
    step = plain_step("ask_preference_question", "success", f"requesting {slot}")
    steps = [*state.get("agent_steps", []), step]
    result = RecommendationResponseSchema(
        need_clarification=True,
        clarifying_question=QUESTIONS[slot],
        preference_stage=slot,
        preference_progress=state.get("preference_progress", {}),
        preference_updates=_preference_update_payload(state),
        agent_steps=[AgentStep(**item) for item in steps],
        fallback=False,
    )
    return {
        "result": result.model_dump(),
        "fallback": False,
        "agent_steps": [step],
    }


# ===== 阶段二：候选构建、RAG 检索与不可信证据清洗 =====

def build_candidates(state: AgentState) -> dict:
    """根据查询和偏好生成数量受限、带本地评分的候选池。"""
    candidates, step = timed_step(
        "search_anime_candidates",
        build_candidate_pool,
        state.get("safe_query") or state["query"],
        state["user_id"],
        RECOMMEND_CANDIDATE_LIMIT,
        excluded_anime_ids=state.get("excluded_anime_ids", []),
    )
    candidates = candidates or []
    step["detail"] = f"selected {len(candidates)} candidates"
    return {"candidates": candidates, "agent_steps": [step]}


def retrieve_evidence(state: AgentState) -> dict:
    """为候选逐一检索归属明确的证据，并记录覆盖率诊断。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    started = time.perf_counter()
    candidates = state.get("candidates", [])
    try:
        evidence_map, diagnostics = retrieve_candidate_evidence(
            state.get("search_query") or state["query"],
            candidates,
            state.get("preferences", {}),
        )
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return {
            "evidence_map": {
                int(candidate["id"]): []
                for candidate in candidates
            },
            "evidence_coverage": {
                "modes": ["retrieval_error"],
                "candidate_count": min(len(candidates), RECOMMEND_EVIDENCE_CANDIDATES),
                "covered_candidates": 0,
                "raw_evidence_count": 0,
                "evidence_insufficient": True,
                "eligible_candidate_ids": [],
                "retrieval_coverage": {
                    "covered_candidates": 0,
                    "candidate_count": min(len(candidates), RECOMMEND_EVIDENCE_CANDIDATES),
                },
                "field_coverage": {
                    "profile": False,
                    "comments": False,
                    "relations": False,
                    "platform": False,
                    "topic_or_sentiment": False,
                },
                "error": reason,
            },
            "eligible_candidates": [],
            "fallback_reason": reason,
            "agent_steps": [
                plain_step(
                    "retrieve_candidate_evidence",
                    "fallback",
                    reason,
                    started,
                )
            ],
        }
    complete = diagnostics["covered_candidates"] == diagnostics["candidate_count"]
    step = plain_step(
        "retrieve_candidate_evidence",
        "success" if complete else "degraded",
        (
            f"covered {diagnostics['covered_candidates']}/"
            f"{diagnostics['candidate_count']} via {','.join(diagnostics['modes'])}"
        ),
        started,
    )
    eligible_ids = diagnostics.get("eligible_candidate_ids")
    if eligible_ids is None:
        eligible_ids = [
            int(candidate["id"])
            for candidate in candidates
            if evidence_map.get(int(candidate["id"]))
        ][:RECOMMEND_EVIDENCE_CANDIDATES]
    return {
        "evidence_map": evidence_map,
        "evidence_coverage": diagnostics,
        "eligible_candidates": [
            candidate
            for candidate in candidates
            if int(candidate["id"]) in eligible_ids
        ],
        "agent_steps": [step],
    }


def route_evidence(state: AgentState) -> str:
    """检索发生系统错误时降级，否则进入证据安全检查。"""
    return "fallback" if state.get("fallback_reason") else "inspect_evidence"


def inspect_evidence(state: AgentState) -> dict:
    """过滤高风险检索文本，并重新计算清洗后的证据覆盖率。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    cleaned, security = sanitize_evidence_map(
        state.get("evidence_map", {})
    )
    coverage = dict(state.get("evidence_coverage", {}))
    eligible_candidates = [
        candidate
        for candidate in state.get("candidates", [])
        if cleaned.get(int(candidate["id"]))
    ][:RECOMMEND_EVIDENCE_CANDIDATES]
    eligible_ids = [int(candidate["id"]) for candidate in eligible_candidates]
    candidate_fields = {
        str(aid): evidence_field_coverage(cleaned.get(aid, []))
        for aid in eligible_ids
    }
    fields = ("profile", "comments", "relations", "platform", "topic_or_sentiment")
    field_counts = {
        field: sum(bool(values.get(field)) for values in candidate_fields.values())
        for field in fields
    }
    covered_candidates = len(eligible_candidates)
    candidate_count = coverage.get("candidate_count", 0)
    coverage.update(
        {
            "covered_candidates": covered_candidates,
            "eligible_candidate_count": covered_candidates,
            "eligible_candidate_ids": eligible_ids,
            "evidence_insufficient": covered_candidates < min(3, len(state.get("candidates", []))),
            "retrieval_coverage": {
                "covered_candidates": covered_candidates,
                "candidate_count": candidate_count,
            },
            "field_coverage": {
                field: bool(eligible_ids) and count == len(eligible_ids)
                for field, count in field_counts.items()
            },
            "field_coverage_counts": field_counts,
            "candidate_field_coverage": candidate_fields,
            "evidence_gaps": {
                aid: evidence_field_gaps(values)
                for aid, values in candidate_fields.items()
            },
            "security_flagged_count": security["flagged_count"],
            "security_filtered_count": security["filtered_count"],
        }
    )
    return {
        "evidence_map": cleaned,
        "evidence_coverage": coverage,
        "eligible_candidates": eligible_candidates,
        "evidence_security": security,
        "agent_steps": [
            plain_step(
                "inspect_untrusted_evidence",
                "degraded" if security["filtered_count"] else "success",
                (
                    f"filtered {security['filtered_count']} high-risk "
                    f"items; flagged {security['flagged_count']}"
                ),
            )
        ],
    }


# ===== 阶段三：提示词打包与有界只读工具循环 =====

def pack_context(state: AgentState) -> dict:
    """按预算打包候选证据，固定提示词版本并记录可追踪元数据。"""
    _, plain_step, render_prompt, _, _ = _recommend_helpers()
    packed, budget = pack_recommendation_context(
        _eligible_candidates(state),
        state.get("evidence_map", {}),
        state.get("search_query") or state["query"],
    )
    prompt_template = get_prompt("recommendation")
    trace = prompt_trace(
        "recommendation",
        LLM_MODEL,
        state.get("evidence_coverage", {}).get("raw_evidence_count", 0),
        "chroma" not in state.get("evidence_coverage", {}).get("modes", []),
        prompt=prompt_template,
    )
    trace["security"] = {
        "input": state.get("input_security", {}),
        "evidence": state.get("evidence_security", {}),
    }
    prompt, request_budget = render_prompt(
        state["user_id"],
        state.get("search_query") or state["query"],
        state.get("preferences", {}),
        packed,
        state.get("history", []),
        prompt_template=prompt_template,
    )
    budget.update(request_budget)
    return {
        "packed_candidates": packed,
        "context_budget": budget,
        "prompt_trace": trace,
        "prompt": prompt,
        "agent_steps": [
            plain_step(
                "pack_evidence_context",
                "success",
                f"{budget['before_chars']}→{budget['after_chars']} chars",
            )
        ],
    }


def agent_decide(state: AgentState) -> dict:
    """让模型决定是否调用只读工具；高风险输入和无模型配置会跳过。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    if state.get("input_security", {}).get("risk") == "high":
        return {
            "messages": [
                AIMessage(
                    content="Tool planning skipped by prompt security guardrail."
                )
            ],
            "agent_steps": [
                plain_step(
                    "agent_decide",
                    "guarded",
                    "high-risk input: read-only tool planning disabled",
                )
            ],
        }
    model = get_chat_model(
        0,
        timeout=RECOMMEND_LLM_TIMEOUT,
        max_tokens=RECOMMEND_LLM_MAX_TOKENS,
    )
    if model is None:
        reason = "LLM is not configured"
        return {
            "fallback_reason": reason,
            "agent_steps": [plain_step("agent_decide", "fallback", reason)],
        }

    candidates = [
        {
            "anime_id": candidate.get("id"),
            "name": candidate.get("name", ""),
            "final_score": candidate.get("final_score", 0),
            "topics": candidate.get("topics", [])[:3],
            "evidence_count": len(
                state.get("evidence_map", {}).get(int(candidate["id"]), [])
            ),
        }
        for candidate in _eligible_candidates(state)
    ]
    trace = state.get("prompt_trace", {})
    prompt_template = get_prompt(
        "recommendation",
        version=trace.get("template_version"),
    )
    system = SystemMessage(
        content=prompt_template.render_section("planner_system")
    )
    human = HumanMessage(
        content=prompt_template.render_section(
            "planner_user",
            query=state.get("search_query") or state["query"],
            preferences=json.dumps(
                state.get("preferences", {}),
                ensure_ascii=False,
            ),
            candidates=json.dumps(candidates, ensure_ascii=False),
            evidence_coverage=json.dumps(
                state.get("evidence_coverage", {}),
                ensure_ascii=False,
            ),
            tool_rounds_used=state.get("tool_rounds", 0),
            tool_rounds_max=RECOMMEND_TOOL_MAX_ROUNDS,
        )
    )
    started = time.perf_counter()
    try:
        response = model.bind_tools(RECOMMEND_TOOLS).invoke(
            [system, human, *state.get("messages", [])]
        )
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(getattr(response, "content", response)))
        return {
            "messages": [response],
            "fallback_reason": "",
            "agent_steps": [
                plain_step(
                    "agent_decide",
                    "success",
                    f"requested {len(response.tool_calls)} tool calls",
                    started,
                )
            ],
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return {
            "fallback_reason": reason,
            "agent_steps": [plain_step("agent_decide", "error", reason, started)],
        }


def route_agent_decision(state: AgentState) -> str:
    """按工具请求、轮数和剩余图步数路由，防止工具无限循环。"""
    if state.get("fallback_reason"):
        return "fallback"
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        if (
            state.get("tool_rounds", 0) >= RECOMMEND_TOOL_MAX_ROUNDS
            or state.get("remaining_steps", RECOMMEND_GRAPH_RECURSION_LIMIT) <= 5
        ):
            return "tool_limit"
        return "tools"
    return "generate"


def record_tool_round(state: AgentState) -> dict:
    """在 ToolNode 完成后递增工具轮数并追加执行轨迹。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    rounds = state.get("tool_rounds", 0) + 1
    return {
        "tool_rounds": rounds,
        "agent_steps": [
            plain_step(
                "execute_recommendation_tools",
                "success",
                f"completed tool round {rounds}",
            )
        ],
    }


def route_after_tool_round(state: AgentState) -> str:
    """完成最大工具轮次后直接生成，避免再发起一次无意义规划。"""
    return (
        "tool_limit"
        if state.get("tool_rounds", 0) >= RECOMMEND_TOOL_MAX_ROUNDS
        else "agent_decide"
    )


def tool_limit(state: AgentState) -> dict:
    """停止继续调用工具，并使用已收集的证据进入结构化生成。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    detail = (
        f"tool loop limit reached after {state.get('tool_rounds', 0)} rounds"
        "; continuing with collected evidence"
    )
    budget = dict(state.get("context_budget", {}))
    budget["tool_limit_reached"] = True
    return {
        "context_budget": budget,
        "agent_steps": [plain_step("tool_loop_guard", "degraded", detail)],
    }


# ===== 阶段四：结构化生成、校验、有限修复与结果组装 =====

def generate(state: AgentState) -> dict:
    """合并打包上下文和最近工具结果，生成结构化推荐。"""
    _, plain_step, _, structured, _ = _recommend_helpers()
    model = get_chat_model(
        0.35,
        timeout=RECOMMEND_LLM_TIMEOUT,
        max_tokens=RECOMMEND_LLM_MAX_TOKENS,
    )
    if model is None:
        reason = "LLM is not configured"
        return {
            "fallback_reason": reason,
            "agent_steps": [
                plain_step("generate_structured_recommendation", "fallback", reason)
            ],
        }

    started = time.perf_counter()
    try:
        tool_messages = [
            message
            for message in state.get("messages", [])
            if isinstance(message, ToolMessage)
        ][-8:]
        tool_context = "\n".join(
            f"{message.name or 'tool'}: {str(message.content)[:1200]}"
            for message in tool_messages
        )
        prompt = state["prompt"]
        if tool_context:
            prompt += "\n\nAdditional read-only tool evidence:\n" + tool_context
        trace = state.get("prompt_trace", {})
        prompt_template = get_prompt(
            "recommendation",
            version=trace.get("template_version"),
        )
        data = structured(model, prompt, prompt_template)
        budget = dict(state.get("context_budget", {}))
        budget["llm_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return {
            "llm_data": data,
            "context_budget": budget,
            "fallback_reason": "",
            "agent_steps": [
                plain_step(
                    "generate_structured_recommendation",
                    "success",
                    (
                        f"{LLM_MODEL}, ~{budget['estimated_input_tokens']} input tokens, "
                        f"max {RECOMMEND_LLM_MAX_TOKENS} output tokens"
                    ),
                    started,
                )
            ],
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        budget = dict(state.get("context_budget", {}))
        budget["llm_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return {
            "fallback_reason": reason,
            "context_budget": budget,
            "agent_steps": [
                plain_step("generate_structured_recommendation", "error", reason, started)
            ],
        }


def route_generation(state: AgentState) -> str:
    """生成异常时降级，成功时必须先经过业务校验。"""
    return "fallback" if state.get("fallback_reason") else "validate"


def validate(state: AgentState) -> dict:
    """校验推荐数量、候选边界、证据引用和问卷状态。"""
    _, plain_step, _, _, validate_result = _recommend_helpers()
    data, errors = validate_result(
        dict(state.get("llm_data", {})),
        _eligible_candidates(state),
        state.get("evidence_map", {}),
        required_count=3 if state.get("force_recommendation") else None,
    )
    if data.get("need_clarification"):
        errors.append("preference questionnaire is already complete")
    return {
        "llm_data": data,
        "validation_errors": errors,
        "agent_steps": [
            plain_step(
                "validate_recommendation",
                "degraded" if errors else "success",
                "; ".join(errors) or "valid",
            )
        ],
    }


def route_validation(state: AgentState) -> str:
    """无错误则成功；有错误且仍有次数则修复，否则降级。"""
    if not state.get("validation_errors"):
        return "success"
    if state.get("repair_attempts", 0) < RECOMMEND_LLM_REPAIR_RETRIES:
        return "repair"
    return "fallback"


def repair(state: AgentState) -> dict:
    """把校验错误和原始结果交给低温模型做一次有界结构修复。"""
    _, plain_step, _, structured, _ = _recommend_helpers()
    attempt = state.get("repair_attempts", 0) + 1
    model = get_chat_model(
        0,
        timeout=RECOMMEND_LLM_TIMEOUT,
        max_tokens=RECOMMEND_LLM_REPAIR_MAX_TOKENS,
    )
    if model is None:
        reason = "repair model is not configured"
        return {
            "repair_attempts": attempt,
            "fallback_reason": reason,
            "agent_steps": [
                plain_step("repair_structured_recommendation", "error", reason)
            ],
        }

    trace = state.get("prompt_trace", {})
    prompt_template = get_prompt(
        "recommendation",
        version=trace.get("template_version"),
    )
    prompt = prompt_template.render_section(
        "repair_user",
        errors=json.dumps(
            state.get("validation_errors", []),
            ensure_ascii=False,
        ),
        candidates=json.dumps(
            state.get("packed_candidates", []),
            ensure_ascii=False,
        ),
        original=json.dumps(
            state.get("llm_data", {}),
            ensure_ascii=False,
        ),
    )
    try:
        return {
            "llm_data": structured(model, prompt, prompt_template),
            "repair_attempts": attempt,
            "validation_errors": [],
            "agent_steps": [
                plain_step(
                    "repair_structured_recommendation",
                    "success",
                    f"repair attempt {attempt}",
                )
            ],
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return {
            "repair_attempts": attempt,
            "fallback_reason": reason,
            "agent_steps": [
                plain_step("repair_structured_recommendation", "error", reason)
            ],
        }


def route_repair(state: AgentState) -> str:
    """修复调用成功后重新校验，调用异常则直接降级。"""
    return "fallback" if state.get("fallback_reason") else "validate"


def finalize_success(state: AgentState) -> dict:
    """补齐候选元数据、证据、追踪和安全诊断，形成最终成功响应。"""
    _, plain_step, _, _, _ = _recommend_helpers()
    data = dict(state.get("llm_data", {}))
    updates, suggestion_security = sanitize_preference_suggestions(
        data.get("preference_updates") or {}
    )
    preferences = state.get("preferences", {})
    step = plain_step("finalize_recommendation", "success", "response assembled")
    trace = dict(state.get("prompt_trace", {}))
    trace_security = dict(trace.get("security", {}))
    trace_security["preference_suggestions"] = suggestion_security
    trace["security"] = trace_security

    all_evidence = [
        item
        for candidate in _eligible_candidates(state)
        for item in state.get("evidence_map", {}).get(int(candidate["id"]), [])
    ]
    steps = [*state.get("agent_steps", []), step]
    data.update(
        {
            "fallback": False,
            "tool_rounds": state.get("tool_rounds", 0),
            "retrieval_evidence": all_evidence,
            "evidence_refs": [item.get("doc_id", "") for item in all_evidence],
            "prompt_trace": trace,
            "agent_steps": steps,
            "retrieval_mode": ",".join(
                state.get("evidence_coverage", {}).get("modes", [])
            ),
            "evidence_coverage": state.get("evidence_coverage", {}),
            "context_budget": state.get("context_budget", {}),
            "validation_warnings": [],
            "preference_stage": "",
            "preference_progress": state.get("preference_progress", {}),
            "preference_updates": _preference_update_payload(
                state,
                applied=state.get("preference_updates", {}),
                suggested=updates,
                preferences=preferences,
            ),
        }
    )
    candidate_map = {
        int(candidate["id"]): candidate
        for candidate in _eligible_candidates(state)
    }
    for recommendation in data.get("recommendations", []):
        candidate = candidate_map[int(recommendation["anime_id"])]
        items = state.get("evidence_map", {}).get(
            int(recommendation["anime_id"]),
            [],
        )
        recommendation.update(
            {
                "name": candidate.get("name", ""),
                "platform": verified_platform_availability(items),
                "comment_count": candidate.get("comment_count", 0),
                "evidence": {
                    "sentiment": candidate.get("sentiment", {}),
                    "topics": candidate.get("topics", []),
                    "comments": candidate.get("comments", []),
                },
            }
        )
        recommendation["retrieval_evidence"] = items
        recommendation["evidence_refs"] = recommendation.get("evidence_refs") or [
            item.get("doc_id", "") for item in items
        ]
    return {
        "result": RecommendationResponseSchema(**data).model_dump(),
        "preferences": preferences,
        "fallback": False,
        "agent_steps": [step],
    }


def fallback(state: AgentState) -> dict:
    """使用已有候选和证据生成同结构本地结果，并保留失败原因。"""
    local_result, _, _, _, _ = _recommend_helpers()
    errors = state.get("validation_errors", [])
    reason = state.get("fallback_reason") or (
        "validation failed: " + "; ".join(errors)
    )
    steps = list(state.get("agent_steps", []))
    payload = local_result(
        state.get("search_query") or state["query"],
        _eligible_candidates(state),
        state.get("preferences", {}),
        state.get("evidence_map", {}),
        steps,
        state.get("prompt_trace", {}),
        reason,
        state.get("evidence_coverage", {}),
        state.get("context_budget", {}),
        required_count=3 if state.get("force_recommendation") else None,
    )
    payload["result"]["preference_stage"] = ""
    payload["result"]["preference_progress"] = state.get(
        "preference_progress",
        {},
    )
    payload["result"]["preference_updates"] = _preference_update_payload(
        state,
        applied=state.get("preference_updates", {}),
    )
    payload["result"]["tool_rounds"] = state.get("tool_rounds", 0)
    return {
        "result": payload["result"],
        "fallback": True,
        "agent_steps": [payload["agent_steps"][-1]],
    }


# ===== 图定义、Checkpoint 与公开运行入口 =====

def create_recommendation_graph(checkpointer=None):
    """注册 18 个节点及条件边，编译为可注入 Checkpointer 的图。"""
    builder = StateGraph(AgentState)
    builder.add_node("load_preferences", load_preferences)
    builder.add_node("inspect_user_input", inspect_user_input)
    builder.add_node("collect_preferences", collect_preferences)
    builder.add_node("assess_preferences", assess_preferences)
    builder.add_node("ask_preference", ask_preference)
    builder.add_node("candidates", build_candidates)
    builder.add_node("evidence", retrieve_evidence)
    builder.add_node("inspect_evidence", inspect_evidence)
    builder.add_node("pack_context", pack_context)
    builder.add_node("agent_decide", agent_decide)
    builder.add_node(
        "tools",
        ToolNode(RECOMMEND_TOOLS, handle_tool_errors=True),
    )
    builder.add_node("record_tool_round", record_tool_round)
    builder.add_node("tool_limit", tool_limit)
    builder.add_node("generate", generate)
    builder.add_node("validate", validate)
    builder.add_node("repair", repair)
    builder.add_node("success", finalize_success)
    builder.add_node("fallback", fallback)

    builder.add_edge(START, "load_preferences")
    builder.add_edge("load_preferences", "inspect_user_input")
    builder.add_edge("inspect_user_input", "collect_preferences")
    builder.add_edge("collect_preferences", "assess_preferences")
    builder.add_conditional_edges(
        "assess_preferences",
        route_preferences,
        {"ask_preference": "ask_preference", "candidates": "candidates"},
    )
    builder.add_edge("ask_preference", END)
    builder.add_edge("candidates", "evidence")
    builder.add_conditional_edges(
        "evidence",
        route_evidence,
        {"inspect_evidence": "inspect_evidence", "fallback": "fallback"},
    )
    builder.add_edge("inspect_evidence", "pack_context")
    builder.add_edge("pack_context", "agent_decide")
    builder.add_conditional_edges(
        "agent_decide",
        route_agent_decision,
        {
            "tools": "tools",
            "generate": "generate",
            "tool_limit": "tool_limit",
            "fallback": "fallback",
        },
    )
    builder.add_edge("tools", "record_tool_round")
    builder.add_conditional_edges(
        "record_tool_round",
        route_after_tool_round,
        {"agent_decide": "agent_decide", "tool_limit": "tool_limit"},
    )
    builder.add_edge("tool_limit", "generate")
    builder.add_conditional_edges(
        "generate",
        route_generation,
        {"validate": "validate", "fallback": "fallback"},
    )
    builder.add_conditional_edges(
        "validate",
        route_validation,
        {"success": "success", "repair": "repair", "fallback": "fallback"},
    )
    builder.add_conditional_edges(
        "repair",
        route_repair,
        {"validate": "validate", "fallback": "fallback"},
    )
    builder.add_edge("success", END)
    builder.add_edge("fallback", END)
    return builder.compile(checkpointer=checkpointer)


def _sqlite_recommendation_checkpointer() -> SqliteSaver:
    parent = os.path.dirname(RECOMMEND_CHECKPOINT_DB)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(
        RECOMMEND_CHECKPOINT_DB,
        check_same_thread=False,
    )
    return SqliteSaver(connection)


@lru_cache(maxsize=1)
def _get_recommendation_checkpointer():
    """Redis 为默认 Checkpointer；仅显式配置或开发降级时使用 SQLite。"""
    if RECOMMEND_CHECKPOINT_BACKEND == "sqlite":
        return _sqlite_recommendation_checkpointer()

    checkpointer = None
    try:
        checkpoint_prefix = "checkpoint"
        checkpoint_write_prefix = "checkpoint_write"
        if AGENT_REDIS_KEY_PREFIX:
            checkpoint_prefix = f"{AGENT_REDIS_KEY_PREFIX}:checkpoint"
            checkpoint_write_prefix = f"{AGENT_REDIS_KEY_PREFIX}:checkpoint_write"
        checkpointer = RedisSaver(
            redis_url=RECOMMEND_CHECKPOINT_REDIS_URL,
            connection_args={
                "socket_connect_timeout": CELERY_BROKER_SOCKET_TIMEOUT,
                "socket_timeout": CELERY_BROKER_SOCKET_TIMEOUT,
                "max_connections": RECOMMEND_REDIS_MAX_CONNECTIONS,
            },
            ttl={
                "default_ttl": RECOMMEND_CHECKPOINT_TTL_MINUTES,
                "refresh_on_read": True,
            },
            checkpoint_prefix=checkpoint_prefix,
            checkpoint_write_prefix=checkpoint_write_prefix,
        )
        checkpointer.setup()
        return checkpointer
    except Exception as exc:
        redis_client = getattr(checkpointer, "_redis", None)
        if redis_client is not None:
            redis_client.close()
            pool = getattr(redis_client, "connection_pool", None)
            if pool is not None:
                pool.disconnect()
        if not RECOMMEND_CHECKPOINT_SQLITE_FALLBACK:
            raise RuntimeError(
                "Redis recommendation Checkpointer initialization failed"
            ) from exc
        logger.warning(
            "Redis recommendation Checkpointer unavailable; using SQLite development fallback: %s",
            type(exc).__name__,
        )
        return _sqlite_recommendation_checkpointer()


def close_recommendation_checkpointer() -> None:
    """关闭当前进程缓存的 Redis/SQLite Checkpointer 连接。"""
    if not _get_recommendation_checkpointer.cache_info().currsize:
        return
    checkpointer = _get_recommendation_checkpointer()
    connection = getattr(checkpointer, "conn", None)
    if connection is not None:
        connection.close()
    redis_client = getattr(checkpointer, "_redis", None)
    if redis_client is not None:
        redis_client.close()
        pool = getattr(redis_client, "connection_pool", None)
        if pool is not None:
            pool.disconnect()
    build_recommendation_graph.cache_clear()
    _get_recommendation_checkpointer.cache_clear()


@lru_cache(maxsize=1)
def build_recommendation_graph():
    """构建并缓存默认带 Checkpoint 的推荐图。"""
    return create_recommendation_graph(_get_recommendation_checkpointer())


def run_recommendation_graph(
    user_id: int,
    query: str,
    history: list[dict] | None = None,
    *,
    task_id: int | None = None,
    excluded_anime_ids: list[int] | None = None,
    force_recommendation: bool = False,
    graph=None,
    auto_resume: bool = True,
) -> dict:
    """运行一次推荐图；异常时可用同一 thread_id 从最近检查点续跑。"""
    runnable = graph or build_recommendation_graph()
    thread_id = (
        f"recommendation-task:{task_id}"
        if task_id is not None
        else f"recommendation-run:{user_id}:{uuid4()}"
    )
    config = {
        "recursion_limit": RECOMMEND_GRAPH_RECURSION_LIMIT,
        "configurable": {"thread_id": thread_id},
    }
    initial_state = {
        "user_id": user_id,
        "query": query,
        "excluded_anime_ids": list(excluded_anime_ids or []),
        "force_recommendation": bool(force_recommendation),
        "history": history or [],
        "messages": [],
        "agent_steps": [],
        "repair_attempts": 0,
        "tool_rounds": 0,
        "fallback": False,
    }
    try:
        final_state = runnable.invoke(initial_state, config=config)
    except Exception:
        if not auto_resume:
            raise
        logger.exception(
            "Recommendation graph failed; resuming task %s from its latest checkpoint",
            task_id,
        )
        final_state = runnable.invoke(None, config=config)
    return {
        "result": final_state["result"],
        "agent_steps": final_state.get("agent_steps", []),
        "fallback": bool(final_state.get("fallback")),
    }
