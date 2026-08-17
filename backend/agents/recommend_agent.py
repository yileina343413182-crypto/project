# -*- coding: utf-8 -*-
"""推荐 Agent 2.0 的兼容入口与共享辅助函数。

真正的节点编排位于 ``recommend_graph``；本模块保留原公开入口，并提供
结构化校验、提示词预算控制和本地降级组装。
"""

from __future__ import annotations

import json
from typing import Any

from backend.agents.fallback import build_recommendation_fallback
from backend.config import RECOMMEND_LLM_MAX_TOKENS, RECOMMEND_PROMPT_MAX_CHARS
from backend.prompts.registry import get_prompt
from backend.agents.schemas import (
    AgentStep,
    LLMRecommendationResponse,
    PromptTrace,
    RECOMMEND_REASON_MAX_CHARS,
    RECOMMEND_REASON_MIN_CHARS,
    RetrievalEvidence,
)
from backend.agents.recommend_context import compact_history


def _dump_schema(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}))


def _evidence_models(evidence: list[dict]) -> list[RetrievalEvidence]:
    return [item if isinstance(item, RetrievalEvidence) else RetrievalEvidence(**item) for item in evidence]


# 只有这些字段允许从模型输出进入偏好建议，最终持久化前还会经过安全过滤。
_PREF_FIELDS = {"likes", "dislikes", "preferred_moods", "preferred_genres", "feedback"}

def _plain_step(name, status, detail, started=None):
    """构造统一的 Agent 执行步骤，便于前端展示和问题追踪。"""
    import time
    return {"name": name, "status": status, "detail": detail, "elapsed_ms": int((time.perf_counter()-started)*1000) if started else 0}

def _validate_recommendation(data, candidates, evidence_map):
    """限制推荐数量、候选范围和证据归属，返回清洗结果及错误列表。"""
    errors, seen = [], set(); allowed = {int(x["id"]): x for x in candidates}
    recs = data.get("recommendations") or []
    if data.get("need_clarification"):
        if recs: errors.append("clarification must not include recommendations")
        data["recommendations"] = []
        return data, errors
    if not 1 <= len(recs) <= 3: errors.append("recommendations must contain 1 to 3 items")
    for rec in recs:
        try: aid = int(rec.get("anime_id"))
        except (TypeError, ValueError): errors.append("invalid anime_id"); continue
        if aid not in allowed: errors.append(f"anime_id {aid} is outside candidate pool"); continue
        if aid in seen: errors.append(f"duplicate anime_id {aid}")
        seen.add(aid); rec["name"] = allowed[aid].get("name", "")
        reason = str(rec.get("reason") or "").strip()
        rec["reason"] = reason
        reason_chars = len(reason)
        if not RECOMMEND_REASON_MIN_CHARS <= reason_chars <= RECOMMEND_REASON_MAX_CHARS:
            errors.append(
                f"anime_id {aid} reason must contain "
                f"{RECOMMEND_REASON_MIN_CHARS} to {RECOMMEND_REASON_MAX_CHARS} "
                f"characters after trimming; got {reason_chars}"
            )
        refs = {x.get("doc_id") or (x.get("metadata") or {}).get("doc_id") for x in evidence_map.get(aid, [])}
        requested = rec.get("evidence_refs") or []
        if any(x not in refs for x in requested): errors.append(f"anime_id {aid} has foreign evidence")
        rec["evidence_refs"] = [x for x in requested if x in refs]
    updates = data.get("preference_updates") if isinstance(data.get("preference_updates"), dict) else {}
    data["preference_updates"] = {k: (v if isinstance(v, list) else [v])[:10] for k,v in updates.items() if k in _PREF_FIELDS and v}
    return data, errors

def _structured(model, prompt, prompt_template=None):
    """要求模型按 LLMRecommendationResponse 结构输出。"""
    actual_prompt = prompt_template or get_prompt("recommendation")
    response = model.with_structured_output(LLMRecommendationResponse).invoke([
        ("system", actual_prompt.render_system()),
        ("human", prompt)])
    return _dump_schema(response)

def _render_bounded_prompt(
    user_id,
    query,
    preferences,
    candidates,
    history,
    prompt_template=None,
):
    """超预算时依次丢弃多余证据、主题和最早历史，再渲染提示词。"""
    import copy
    actual_prompt = prompt_template or get_prompt("recommendation")
    items = copy.deepcopy(candidates); compact = compact_history(history)
    def render():
        return actual_prompt.render(user_id=user_id, query=query,
            preferences=json.dumps(preferences, ensure_ascii=False, separators=(",", ":")),
            candidates=json.dumps(items, ensure_ascii=False, separators=(",", ":")),
            history=json.dumps(compact, ensure_ascii=False, separators=(",", ":")), evidence="inside candidates")
    prompt = render()
    while len(prompt) > RECOMMEND_PROMPT_MAX_CHARS:
        changed = False
        for item in reversed(items):
            if len(item.get("evidence", [])) > 1:
                item["evidence"].pop(); changed = True; break
        if not changed:
            for item in reversed(items):
                if item.get("topics"):
                    item["topics"].pop(); changed = True; break
        if not changed and compact:
            compact.pop(0); changed = True
        if not changed: break
        prompt = render()
    schema_chars = len(json.dumps(LLMRecommendationResponse.model_json_schema(), ensure_ascii=False))
    return prompt[:RECOMMEND_PROMPT_MAX_CHARS], {"prompt_chars": min(len(prompt), RECOMMEND_PROMPT_MAX_CHARS),
        "candidate_context_chars": len(json.dumps(items, ensure_ascii=False)), "history_chars": len(json.dumps(compact, ensure_ascii=False)),
        "schema_chars": schema_chars, "estimated_input_tokens": int((len(prompt)+schema_chars)/1.5), "max_output_tokens": RECOMMEND_LLM_MAX_TOKENS}

def _local_result(query, candidates, preferences, evidence_map, steps, trace, reason, diagnostics, budget=None):
    """把本地推荐补齐为与正常 LLM 路径一致的响应与追踪字段。"""
    candidates = sorted(candidates, key=lambda x: (bool(evidence_map.get(int(x["id"]))), x.get("final_score", x.get("score", 0))), reverse=True)
    result = build_recommendation_fallback(query, candidates, preferences)
    all_items = [x for values in evidence_map.values() for x in values]
    result.retrieval_evidence = _evidence_models(all_items); result.evidence_refs = [x.get("doc_id", "") for x in all_items]
    result.prompt_trace = PromptTrace(**trace); result.fallback_reason = reason
    result.retrieval_mode = ",".join(diagnostics.get("modes", [])); result.evidence_coverage = diagnostics; result.context_budget = budget or {}
    for rec in result.recommendations:
        items = evidence_map.get(int(rec.anime_id), []); rec.retrieval_evidence = _evidence_models(items); rec.evidence_refs = [x.get("doc_id", "") for x in items]
    steps.append(_plain_step("fallback_recommendation", "fallback", reason)); result.agent_steps = [AgentStep(**x) for x in steps]
    return {"result": result.model_dump(), "agent_steps": steps, "fallback": True}

def run_recommendation_agent(
    user_id: int,
    query: str,
    history: list[dict] | None = None,
    *,
    task_id: int | None = None,
) -> dict:
    """兼容旧调用方的公开入口，实际委托给 LangGraph 工作流。"""
    from backend.agents.recommend_graph import run_recommendation_graph

    return run_recommendation_graph(user_id, query, history, task_id=task_id)




