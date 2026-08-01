# -*- coding: utf-8 -*-
"""Deterministic LangGraph workflow for Recommendation Agent 2.0."""

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
    pack_recommendation_context,
    retrieve_candidate_evidence,
)
from backend.agents.schemas import AgentStep, RecommendationResponseSchema
from backend.agents.tools import RECOMMEND_TOOLS, build_candidate_pool, timed_step
from backend.config import (
    LLM_MODEL,
    RECOMMEND_CANDIDATE_LIMIT,
    RECOMMEND_CHECKPOINT_DB,
    RECOMMEND_GRAPH_RECURSION_LIMIT,
    RECOMMEND_LLM_MAX_TOKENS,
    RECOMMEND_LLM_REPAIR_MAX_TOKENS,
    RECOMMEND_LLM_REPAIR_RETRIES,
    RECOMMEND_LLM_TIMEOUT,
    RECOMMEND_TOOL_MAX_ROUNDS,
)
from backend.prompts.registry import get_prompt, prompt_trace

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """Serializable state shared by the recommendation graph nodes."""

    user_id: int
    query: str
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
    for message in reversed(history or []):
        if message.get("role") not in {"agent", "assistant"}:
            continue
        stage = _result_metadata(message).get("preference_stage")
        return stage if stage in QUESTIONNAIRE_SLOTS else ""
    return ""


def _skipped_slots(history: list[dict], pending: str, query: str) -> set[str]:
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
    parts = [query]
    for slot in QUESTIONNAIRE_SLOTS:
        parts.extend(str(value) for value in preferences.get(slot, [])[-5:])
    return " ".join(_unique(parts))


def load_preferences(state: AgentState) -> dict:
    preferences, step = timed_step(
        "get_user_preferences",
        get_user_preferences,
        state["user_id"],
    )
    return {"preferences": preferences or {}, "agent_steps": [step]}


def inspect_user_input(state: AgentState) -> dict:
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
    return "ask_preference" if state.get("next_preference_slot") else "candidates"


def ask_preference(state: AgentState) -> dict:
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


def build_candidates(state: AgentState) -> dict:
    candidates, step = timed_step(
        "search_anime_candidates",
        build_candidate_pool,
        state.get("search_query") or state["query"],
        state["user_id"],
        RECOMMEND_CANDIDATE_LIMIT,
    )
    candidates = candidates or []
    step["detail"] = f"selected {len(candidates)} candidates"
    return {"candidates": candidates, "agent_steps": [step]}


def retrieve_evidence(state: AgentState) -> dict:
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
                "candidate_count": len(candidates),
                "covered_candidates": 0,
                "raw_evidence_count": 0,
                "evidence_insufficient": True,
                "error": reason,
            },
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
    return {
        "evidence_map": evidence_map,
        "evidence_coverage": diagnostics,
        "agent_steps": [step],
    }


def route_evidence(state: AgentState) -> str:
    return "fallback" if state.get("fallback_reason") else "inspect_evidence"


def inspect_evidence(state: AgentState) -> dict:
    _, plain_step, _, _, _ = _recommend_helpers()
    cleaned, security = sanitize_evidence_map(
        state.get("evidence_map", {})
    )
    coverage = dict(state.get("evidence_coverage", {}))
    covered_candidates = sum(bool(items) for items in cleaned.values())
    coverage.update(
        {
            "covered_candidates": covered_candidates,
            "evidence_insufficient": (
                covered_candidates < coverage.get("candidate_count", 0)
            ),
            "security_flagged_count": security["flagged_count"],
            "security_filtered_count": security["filtered_count"],
        }
    )
    return {
        "evidence_map": cleaned,
        "evidence_coverage": coverage,
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


def pack_context(state: AgentState) -> dict:
    _, plain_step, render_prompt, _, _ = _recommend_helpers()
    packed, budget = pack_recommendation_context(
        state.get("candidates", []),
        state.get("evidence_map", {}),
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
        for candidate in state.get("candidates", [])
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


def tool_limit(state: AgentState) -> dict:
    _, plain_step, _, _, _ = _recommend_helpers()
    reason = (
        f"tool loop limit reached after {state.get('tool_rounds', 0)} rounds"
    )
    return {
        "fallback_reason": reason,
        "agent_steps": [plain_step("tool_loop_guard", "fallback", reason)],
    }


def generate(state: AgentState) -> dict:
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
    return "fallback" if state.get("fallback_reason") else "validate"


def validate(state: AgentState) -> dict:
    _, plain_step, _, _, validate_result = _recommend_helpers()
    data, errors = validate_result(
        dict(state.get("llm_data", {})),
        state.get("candidates", []),
        state.get("evidence_map", {}),
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
    if not state.get("validation_errors"):
        return "success"
    if state.get("repair_attempts", 0) < RECOMMEND_LLM_REPAIR_RETRIES:
        return "repair"
    return "fallback"


def repair(state: AgentState) -> dict:
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
    return "fallback" if state.get("fallback_reason") else "validate"


def finalize_success(state: AgentState) -> dict:
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
        for values in state.get("evidence_map", {}).values()
        for item in values
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
        for candidate in state.get("candidates", [])
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
                "platform": candidate.get("platform", ""),
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
    local_result, _, _, _, _ = _recommend_helpers()
    errors = state.get("validation_errors", [])
    reason = state.get("fallback_reason") or (
        "validation failed: " + "; ".join(errors)
    )
    steps = list(state.get("agent_steps", []))
    payload = local_result(
        state.get("search_query") or state["query"],
        state.get("candidates", []),
        state.get("preferences", {}),
        state.get("evidence_map", {}),
        steps,
        state.get("prompt_trace", {}),
        reason,
        state.get("evidence_coverage", {}),
        state.get("context_budget", {}),
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


def create_recommendation_graph(checkpointer=None):
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
    builder.add_edge("record_tool_round", "agent_decide")
    builder.add_edge("tool_limit", "fallback")
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


@lru_cache(maxsize=1)
def _get_recommendation_checkpointer() -> SqliteSaver:
    parent = os.path.dirname(RECOMMEND_CHECKPOINT_DB)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(
        RECOMMEND_CHECKPOINT_DB,
        check_same_thread=False,
    )
    return SqliteSaver(connection)


@lru_cache(maxsize=1)
def build_recommendation_graph():
    return create_recommendation_graph(_get_recommendation_checkpointer())


def run_recommendation_graph(
    user_id: int,
    query: str,
    history: list[dict] | None = None,
    *,
    task_id: int | None = None,
    graph=None,
    auto_resume: bool = True,
) -> dict:
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
