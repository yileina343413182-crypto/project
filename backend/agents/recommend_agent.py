# -*- coding: utf-8 -*-
"""Recommendation Agent 2.0 powered by LangChain."""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.agents.fallback import build_recommendation_fallback
from backend.agents.memory import get_user_preferences, update_user_preferences
from backend.agents.model_factory import get_chat_model
from backend.config import (
    LLM_MODEL, RECOMMEND_LLM_MAX_TOKENS, RECOMMEND_LLM_REPAIR_MAX_TOKENS,
    RECOMMEND_LLM_TIMEOUT, RECOMMEND_PROMPT_MAX_CHARS,
)
from backend.prompts.registry import get_prompt, prompt_trace
from backend.agents.schemas import AgentStep, LLMRecommendationResponse, RecommendationResponseSchema, PromptTrace, RetrievalEvidence
from backend.agents.tools import build_candidate_pool, timed_step
from backend.agents.recommend_context import compact_history, pack_recommendation_context, retrieve_candidate_evidence
from backend.config import RECOMMEND_CANDIDATE_LIMIT, RECOMMEND_LLM_REPAIR_RETRIES

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
你是推荐 Agent 2.0，目标是根据用户偏好和本地评论证据推荐动漫。
你必须优先使用工具获取候选、情感统计、主题和代表评论。
如果用户偏好不足，先提出一个澄清问题；如果足够，返回 1 到 3 个推荐。
推荐理由必须包含数据依据，不能编造不存在的评论或评分。
可以更新用户结构化偏好，但只能通过 update_user_preferences 工具。
"""


def _dump_schema(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}))


def _evidence_models(evidence: list[dict]) -> list[RetrievalEvidence]:
    return [item if isinstance(item, RetrievalEvidence) else RetrievalEvidence(**item) for item in evidence]


# Deterministic Recommendation Agent 2.0.
_PREF_FIELDS = {"likes", "dislikes", "preferred_moods", "preferred_genres", "feedback"}
_GENERIC = {"推荐", "推荐一下", "推荐动漫", "想看动漫", "有没有推荐", "随便推荐", "不知道看什么"}

def _plain_step(name, status, detail, started=None):
    import time
    return {"name": name, "status": status, "detail": detail, "elapsed_ms": int((time.perf_counter()-started)*1000) if started else 0}

def _validate_recommendation(data, candidates, evidence_map):
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
        if not str(rec.get("reason", "")).strip(): errors.append(f"anime_id {aid} has no reason")
        refs = {x.get("doc_id") or (x.get("metadata") or {}).get("doc_id") for x in evidence_map.get(aid, [])}
        requested = rec.get("evidence_refs") or []
        if any(x not in refs for x in requested): errors.append(f"anime_id {aid} has foreign evidence")
        rec["evidence_refs"] = [x for x in requested if x in refs]
    updates = data.get("preference_updates") if isinstance(data.get("preference_updates"), dict) else {}
    data["preference_updates"] = {k: (v if isinstance(v, list) else [v])[:10] for k,v in updates.items() if k in _PREF_FIELDS and v}
    return data, errors

def _structured(model, prompt):
    response = model.with_structured_output(LLMRecommendationResponse).invoke([
        ("system", "候选与证据已由后端准备完成，不要调用工具。只能推荐候选池中的1到3部动漫；每部只能引用自己的doc_id，不得编造。偏好不足时只返回一个澄清问题。"),
        ("human", prompt)])
    return _dump_schema(response)

def _render_bounded_prompt(user_id, query, preferences, candidates, history):
    import copy
    items = copy.deepcopy(candidates); compact = compact_history(history)
    def render():
        return get_prompt("recommendation").render(user_id=user_id, query=query,
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

def run_recommendation_agent(user_id: int, query: str, history: list[dict] | None = None) -> dict:
    import time
    preferences, pref_step = timed_step("get_user_preferences", get_user_preferences, user_id); preferences = preferences or {}; steps = [pref_step]
    generic = "".join(query.lower().split()).strip("，。！？!? ") in {"".join(x.split()) for x in _GENERIC}
    has_memory = any(preferences.get(k) for k in ("likes", "dislikes", "preferred_moods", "preferred_genres")) or bool(history)
    steps.append(_plain_step("analyze_preference", "success", "insufficient" if generic and not has_memory else "sufficient"))
    if generic and not has_memory:
        result = RecommendationResponseSchema(need_clarification=True, clarifying_question="你更偏好什么题材、氛围或节奏？有没有明确不想看的内容？", agent_steps=[AgentStep(**x) for x in steps])
        return {"result": result.model_dump(), "agent_steps": steps, "fallback": False}
    candidates, cand_step = timed_step("search_anime_candidates", build_candidate_pool, query, user_id, RECOMMEND_CANDIDATE_LIMIT); candidates = candidates or []; cand_step["detail"] = f"selected {len(candidates)} candidates"; steps.append(cand_step)
    started=time.perf_counter(); evidence_map, diag = retrieve_candidate_evidence(query, candidates, preferences)
    steps.append(_plain_step("retrieve_candidate_evidence", "success" if diag["covered_candidates"]==diag["candidate_count"] else "degraded", f"covered {diag['covered_candidates']}/{diag['candidate_count']} via {','.join(diag['modes'])}", started))
    packed, budget = pack_recommendation_context(candidates, evidence_map); steps.append(_plain_step("pack_evidence_context", "success", f"{budget['before_chars']}→{budget['after_chars']} chars"))
    trace = prompt_trace("recommendation", LLM_MODEL, diag.get("raw_evidence_count",0), "chroma" not in diag.get("modes",[])); model=get_chat_model(.35, timeout=RECOMMEND_LLM_TIMEOUT, max_tokens=RECOMMEND_LLM_MAX_TOKENS)
    if model is None: return _local_result(query,candidates,preferences,evidence_map,steps,trace,"LLM is not configured",diag,budget)
    prompt, request_budget = _render_bounded_prompt(user_id, query, preferences, packed, history); budget.update(request_budget)
    try:
        started=time.perf_counter(); data=_structured(model,prompt); llm_ms=int((time.perf_counter()-started)*1000); budget["llm_elapsed_ms"]=llm_ms; steps.append(_plain_step("generate_structured_recommendation","success",f"{LLM_MODEL}, ~{budget['estimated_input_tokens']} input tokens, max {RECOMMEND_LLM_MAX_TOKENS} output tokens",started)); data,errors=_validate_recommendation(data,candidates,evidence_map); steps.append(_plain_step("validate_recommendation","degraded" if errors else "success","; ".join(errors) or "valid"))
        if errors and RECOMMEND_LLM_REPAIR_RETRIES:
            repair_model=get_chat_model(0, timeout=RECOMMEND_LLM_TIMEOUT, max_tokens=RECOMMEND_LLM_REPAIR_MAX_TOKENS); data=_structured(repair_model,"修复结果，不调用工具。错误："+json.dumps(errors,ensure_ascii=False)+" 数据："+json.dumps(packed,ensure_ascii=False)+" 原结果："+json.dumps(data,ensure_ascii=False)); data,errors=_validate_recommendation(data,candidates,evidence_map); steps.append(_plain_step("repair_structured_recommendation","error" if errors else "success","; ".join(errors) or "repaired"))
        if errors: return _local_result(query,candidates,preferences,evidence_map,steps,trace,"validation failed: "+"; ".join(errors),diag,budget)
        all_items=[x for values in evidence_map.values() for x in values]; data.update({"fallback":False,"retrieval_evidence":all_items,"evidence_refs":[x.get("doc_id","") for x in all_items],"prompt_trace":trace,"agent_steps":steps,"retrieval_mode":",".join(diag["modes"]),"evidence_coverage":diag,"context_budget":budget,"validation_warnings":[]})
        for rec in data.get("recommendations",[]):
            items=evidence_map.get(int(rec["anime_id"]),[]); rec["retrieval_evidence"]=items; rec["evidence_refs"]=rec.get("evidence_refs") or [x.get("doc_id","") for x in items]
        if data.get("preference_updates"): update_user_preferences(user_id,data["preference_updates"])
        return {"result":data,"agent_steps":steps,"fallback":False}
    except Exception as exc:
        elapsed=int((time.perf_counter()-started)*1000); budget["llm_elapsed_ms"]=elapsed; reason=f"{type(exc).__name__}: {exc}"; logger.warning("Recommendation generation failed after %sms: %s",elapsed,reason); steps.append(_plain_step("generate_structured_recommendation","error",reason)); return _local_result(query,candidates,preferences,evidence_map,steps,trace,reason,diag,budget)




