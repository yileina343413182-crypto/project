# -*- coding: utf-8 -*-
"""推荐 Agent 2.0 的兼容入口与共享辅助函数。

真正的节点编排位于 ``recommend_graph``；本模块保留原公开入口，并提供
结构化校验、提示词预算控制和本地降级组装。
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.agents.fallback import build_recommendation_fallback
from backend.config import RECOMMEND_LLM_MAX_TOKENS, RECOMMEND_PROMPT_MAX_TOKENS
from backend.prompts.registry import get_prompt
from backend.agents.schemas import (
    AgentStep,
    LLMRecommendationResponse,
    PromptTrace,
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


def _normalize_match_tags(value):
    """清理模型偶发生成的包围符号并按显示语义去重。"""
    values = value if isinstance(value, list) else []
    normalized = []
    seen = set()
    for item in values:
        text = re.sub(r"[\u200b-\u200d\ufeff]", "", str(item or "")).strip()
        text = re.sub(r"^[\[\]【】()（）<>《》]+", "", text).strip()
        text = re.sub(r"^匹配\s*[:：]\s*", "匹配：", text)
        compact = re.sub(r"\s+", "", text).rstrip("＋+−-")
        if compact in {"口碑", "口碑依据"}:
            text, key = "口碑", "口碑"
        elif compact in {"评论证据", "评论检索证据"}:
            text, key = "评论证据", "评论证据"
        else:
            key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            normalized.append(text)
    return normalized[:10]

def _plain_step(name, status, detail, started=None):
    """构造统一的 Agent 执行步骤，便于前端展示和问题追踪。"""
    import time
    return {"name": name, "status": status, "detail": detail, "elapsed_ms": int((time.perf_counter()-started)*1000) if started else 0}

def _validate_recommendation(data, candidates, evidence_map, required_count=None):
    """限制推荐数量、候选范围和证据归属，返回清洗结果及错误列表。"""
    errors, warnings, seen = [], [], set(); allowed = {int(x["id"]): x for x in candidates}
    recs = data.get("recommendations") or []
    if data.get("need_clarification"):
        if recs: errors.append("clarification must not include recommendations")
        data["recommendations"] = []
        return data, errors
    if required_count is not None:
        if len(recs) != required_count:
            errors.append(f"recommendations must contain exactly {required_count} items")
    elif not 1 <= len(recs) <= 3:
        errors.append("recommendations must contain 1 to 3 items")
    for rec in recs:
        try: aid = int(rec.get("anime_id"))
        except (TypeError, ValueError): errors.append("invalid anime_id"); continue
        if aid not in allowed: errors.append(f"anime_id {aid} is outside candidate pool"); continue
        if aid in seen: errors.append(f"duplicate anime_id {aid}")
        seen.add(aid); rec["name"] = allowed[aid].get("name", "")
        reason = str(rec.get("reason") or "").strip()
        rec["reason"] = reason
        rec["match_tags"] = _normalize_match_tags(rec.get("match_tags"))
        if not reason:
            errors.append(f"anime_id {aid} reason must not be empty")
        refs = {x.get("doc_id") or (x.get("metadata") or {}).get("doc_id") for x in evidence_map.get(aid, [])}
        requested = rec.get("evidence_refs") or []
        if any(x not in refs for x in requested):
            warnings.append(f"anime_id {aid} foreign evidence refs removed")
        rec["evidence_refs"] = [x for x in requested if x in refs]
    updates = data.get("preference_updates") if isinstance(data.get("preference_updates"), dict) else {}
    data["preference_updates"] = {k: (v if isinstance(v, list) else [v])[:10] for k,v in updates.items() if k in _PREF_FIELDS and v}
    data["validation_warnings"] = warnings
    return data, errors

def _structured(model, prompt, prompt_template=None):
    """要求模型按 LLMRecommendationResponse 结构输出。"""
    actual_prompt = prompt_template or get_prompt("recommendation")
    response = model.with_structured_output(LLMRecommendationResponse).invoke([
        ("system", actual_prompt.render_system()),
        ("human", prompt)])
    return _dump_schema(response)


_TOKEN_PIECE = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)


def _estimate_prompt_tokens(value: str) -> int:
    """Conservatively estimate mixed Chinese/Latin prompt tokens without a model download."""
    total = 0
    for match in _TOKEN_PIECE.finditer(str(value or "")):
        piece = match.group(0)
        total += (len(piece) + 3) // 4 if re.fullmatch(r"[A-Za-z0-9_]+", piece) else 1
    return total


def _complete_text_segments(value: str) -> list[str]:
    """Split text into removable whole clauses instead of slicing through a sentence."""
    return [
        segment
        for segment in re.findall(r".*?(?:[。！？!?；;\n]+|$)", str(value or ""), re.DOTALL)
        if segment
    ]

def _render_bounded_prompt(
    user_id,
    query,
    preferences,
    candidates,
    history,
    prompt_template=None,
    memory_context=None,
):
    """按完整结构降级上下文，避免在 JSON、句子或模板边界中间切断。"""
    import copy
    actual_prompt = prompt_template or get_prompt("recommendation")
    items = copy.deepcopy(candidates); compact = compact_history(history)
    prefs = copy.deepcopy(preferences)
    memories = copy.deepcopy(memory_context or {})
    query_segments = _complete_text_segments(query)
    bounded_query = "".join(query_segments).strip()
    omitted_query = "[用户请求过长，已按完整句边界省略]"
    trimmed_sections = []

    def render():
        return actual_prompt.render(user_id=user_id, query=bounded_query,
            preferences=json.dumps(prefs, ensure_ascii=False, separators=(",", ":")),
            memory_context=json.dumps(memories, ensure_ascii=False, separators=(",", ":")),
            candidates=json.dumps(items, ensure_ascii=False, separators=(",", ":")),
            history=json.dumps(compact, ensure_ascii=False, separators=(",", ":")), evidence="inside candidates")

    schema = json.dumps(LLMRecommendationResponse.model_json_schema(), ensure_ascii=False)
    system_prompt = actual_prompt.render_system()
    fixed_tokens = _estimate_prompt_tokens(system_prompt) + _estimate_prompt_tokens(schema)
    prompt = render()
    prompt_tokens = _estimate_prompt_tokens(prompt)
    while fixed_tokens + prompt_tokens > RECOMMEND_PROMPT_MAX_TOKENS:
        changed = False
        for item in reversed(items):
            if len(item.get("evidence", [])) > 1:
                item["evidence"].pop(); changed = True; trimmed_sections.append("evidence"); break
        if not changed:
            for item in reversed(items):
                if item.get("topics"):
                    item["topics"].pop(); changed = True; trimmed_sections.append("topics"); break
        if not changed and memories.get("long_term_memories"):
            memories["long_term_memories"].pop(); changed = True; trimmed_sections.append("long_term_memories")
        if not changed and compact:
            compact.pop(0); changed = True; trimmed_sections.append("history")
        if not changed and memories.get("older_uncompressed_messages"):
            memories["older_uncompressed_messages"].pop(0); changed = True; trimmed_sections.append("older_messages")
        if not changed:
            for item in reversed(items):
                if item.get("evidence"):
                    item["evidence"].pop(); changed = True; trimmed_sections.append("evidence"); break
        if not changed and memories.get("summary"):
            memories["summary"] = ""; changed = True; trimmed_sections.append("summary")
        if not changed and memories.get("working_state"):
            memories["working_state"] = {}; changed = True; trimmed_sections.append("working_state")
        if not changed:
            for field in ("structured_knowledge", "data_sources", "match_tags", "field_coverage", "evidence_gaps"):
                target = next((item for item in reversed(items) if item.get(field)), None)
                if target is not None:
                    target.pop(field, None); changed = True; trimmed_sections.append(field); break
        if not changed and len(items) > 3:
            items.pop(); changed = True; trimmed_sections.append("candidate")
        if not changed:
            keys = [key for key in prefs if key != "dislikes"] + (["dislikes"] if "dislikes" in prefs else [])
            for key in keys:
                if isinstance(prefs.get(key), list) and prefs[key]:
                    prefs[key].pop(); changed = True; trimmed_sections.append("preferences"); break
        if not changed and len(query_segments) > 1:
            query_segments = query_segments[:max(1, len(query_segments) // 2)]
            bounded_query = "".join(query_segments).strip()
            changed = True; trimmed_sections.append("query")
        elif not changed and bounded_query and bounded_query != omitted_query:
            bounded_query = omitted_query
            changed = True; trimmed_sections.append("query")
        if not changed:
            break
        prompt = render()
        prompt_tokens = _estimate_prompt_tokens(prompt)
    estimated_input_tokens = fixed_tokens + prompt_tokens
    return prompt, {"prompt_chars": len(prompt), "prompt_tokens": prompt_tokens,
        "candidate_context_chars": len(json.dumps(items, ensure_ascii=False)), "history_chars": len(json.dumps(compact, ensure_ascii=False)),
        "memory_context_chars": len(json.dumps(memories, ensure_ascii=False)),
        "schema_chars": len(schema), "schema_tokens": _estimate_prompt_tokens(schema),
        "system_tokens": _estimate_prompt_tokens(system_prompt),
        "estimated_input_tokens": estimated_input_tokens,
        "max_input_tokens": RECOMMEND_PROMPT_MAX_TOKENS,
        "budget_exceeded": estimated_input_tokens > RECOMMEND_PROMPT_MAX_TOKENS,
        "trimmed_sections": list(dict.fromkeys(trimmed_sections)),
        "max_output_tokens": RECOMMEND_LLM_MAX_TOKENS}

def _local_result(query, candidates, preferences, evidence_map, steps, trace, reason, diagnostics, budget=None, required_count=None):
    """把本地推荐补齐为与正常 LLM 路径一致的响应与追踪字段。"""
    candidates = sorted(candidates, key=lambda x: (bool(evidence_map.get(int(x["id"]))), x.get("final_score", x.get("score", 0))), reverse=True)
    result = build_recommendation_fallback(
        query,
        candidates,
        preferences,
        evidence_map=evidence_map,
        required_count=required_count,
    )
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
    excluded_anime_ids: list[int] | None = None,
    force_recommendation: bool = False,
    memory_context: dict | None = None,
) -> dict:
    """兼容旧调用方的公开入口，实际委托给 LangGraph 工作流。"""
    from backend.agents.recommend_graph import run_recommendation_graph

    return run_recommendation_graph(
        user_id,
        query,
        history,
        task_id=task_id,
        excluded_anime_ids=excluded_anime_ids,
        force_recommendation=force_recommendation,
        memory_context=memory_context,
    )




