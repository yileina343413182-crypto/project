# -*- coding: utf-8 -*-
"""基于 LangChain 的动漫舆情诊断 Agent。

主流程先收集本地统计、代表评论、Bangumi 信息与 RAG 证据，再经过不可信
文本过滤、上下文压缩、结构化生成和一次修复；任一步失败都可退回只依赖
本地数据的确定性报告。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from backend.agents.evidence_excerpt import select_evidence_excerpt
from backend.agents.fallback import build_opinion_fallback
from backend.agents.model_factory import get_chat_model
from backend.agents.prompt_security import (
    inspect_untrusted_text,
    sanitize_comment_groups,
    sanitize_evidence_map,
)
from backend.config import (
    LLM_MODEL,
    OPINION_LLM_MAX_TOKENS,
    OPINION_LLM_REPAIR_MAX_TOKENS,
    OPINION_LLM_TIMEOUT,
    OPINION_PROMPT_MAX_CHARS,
)
from backend.prompts.registry import get_prompt, prompt_trace
from backend.rag.retriever import evidence_doc_ids, search_evidence
from backend.agents.schemas import AgentStep, LLMOpinionReportSchema, PromptTrace, RetrievalEvidence
from backend.agents.tools import (
    fetch_anime_info,
    fetch_bangumi_info,
    fetch_representative_comments,
    get_aspect_sentiment,
    get_sentiment_stats,
    get_sentiment_trend,
    get_topics,
    get_wordcloud_data,
    timed_step,
)

logger = logging.getLogger(__name__)


# ===== 结构转换与上下文压缩 =====

def _dump_schema(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}))


def _evidence_models(evidence: list[dict]) -> list[RetrievalEvidence]:
    return [item if isinstance(item, RetrievalEvidence) else RetrievalEvidence(**item) for item in evidence]




def _truncate(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _compact_trend(trend: list[dict], limit: int = 10) -> list[dict]:
    if len(trend) <= limit:
        return trend

    scored = []
    for item in trend:
        positive = int(item.get("positive") or 0)
        negative = int(item.get("negative") or 0)
        neutral = int(item.get("neutral") or 0)
        total = positive + negative + neutral
        negative_rate = negative / total if total else 0
        score = total + (negative * 2) + (50 if negative_rate >= 0.35 else 0)
        scored.append((score, item))

    selected = [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:limit]]
    return sorted(selected, key=lambda item: item.get("date", ""))


def _compact_topics(topics: list[dict], limit: int = 5, keyword_limit: int = 5) -> list[dict]:
    compact = []
    for topic in topics[:limit]:
        keywords = topic.get("keywords") or []
        compact.append({
            "topic_id": topic.get("topic_id"),
            "weight": topic.get("weight"),
            "keywords": keywords[:keyword_limit],
        })
    return compact


def _compact_comments(comments: dict[str, list[dict]], per_label: int = 2, text_limit: int = 180) -> dict[str, list[dict]]:
    compact: dict[str, list[dict]] = {}
    for label, items in (comments or {}).items():
        compact[label] = []
        for item in (items or [])[:per_label]:
            compact[label].append({
                "content": _truncate(item.get("content", ""), text_limit),
                "sentiment_label": item.get("sentiment_label", label),
                "sentiment_score": item.get("sentiment_score", 0),
                "likes": item.get("likes", 0),
                "platform": item.get("platform", ""),
                "publish_time": item.get("publish_time", ""),
            })
    return compact


def _compact_evidence(
    evidence: list[dict],
    limit: int = 5,
    text_limit: int = 300,
    *,
    query: str = "",
    anime_name: str = "",
    topics: list | None = None,
    total_budget: int = 5000,
) -> list[dict]:
    compact = []
    selected = (evidence or [])[:limit]
    remaining = total_budget
    pending = len(selected)
    for item in selected:
        allowance = min(
            remaining,
            max(text_limit, remaining // max(1, pending)),
        )
        full_content = str(
            item.get("full_content") or item.get("content") or ""
        )
        excerpt = select_evidence_excerpt(
            full_content,
            query=query,
            anime_name=anime_name,
            topics=topics,
            target_chars=allowance,
            remaining_chars=remaining,
        )
        pending -= 1
        if not excerpt:
            continue
        compact.append({
            "doc_id": item.get("doc_id", ""),
            "source_type": item.get("source_type", ""),
            "evidence_excerpt": excerpt,
            "similarity": item.get("similarity", 0),
            "rank": item.get("rank", 0),
            "source_label": item.get("source_label", ""),
            "metadata": item.get("metadata", {}),
        })
        remaining -= len(excerpt)
    return compact


def build_compact_opinion_context(
    context: dict[str, Any],
    evidence: list[dict] | None = None,
    *,
    trend_limit: int = 10,
    topic_limit: int = 5,
    topic_keyword_limit: int = 5,
    word_limit: int = 20,
    comments_per_label: int = 2,
    comment_text_limit: int = 180,
    evidence_limit: int = 10,
    evidence_text_limit: int = 300,
    evidence_budget: int = 5000,
    bangumi_summary_limit: int = 300,
    query: str = "",
    anime_name: str = "",
) -> tuple[dict[str, Any], list[dict]]:
    """压缩趋势、主题、评论和检索证据，控制发送给模型的上下文体积。"""
    bangumi = dict(context.get("bangumi") or {})
    if "summary" in bangumi:
        bangumi["summary"] = _truncate(bangumi.get("summary", ""), bangumi_summary_limit)

    compact_context = {
        "stats": context.get("stats") or {},
        "trend": _compact_trend(context.get("trend") or [], trend_limit),
        "topics": _compact_topics(context.get("topics") or [], topic_limit, topic_keyword_limit),
        "wordcloud": (context.get("wordcloud") or [])[:word_limit],
        "comments": _compact_comments(context.get("comments") or {}, comments_per_label, comment_text_limit),
        "aspect": context.get("aspect") or {},
        "bangumi": bangumi,
    }
    return compact_context, _compact_evidence(
        evidence or [],
        evidence_limit,
        evidence_text_limit,
        query=query,
        anime_name=anime_name,
        topics=context.get("topics") or [],
        total_budget=evidence_budget,
    )

# ===== 模型输出解析与结构校验 =====

def _message_content(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _extract_json_object(text: str) -> dict:
    """从纯 JSON 或 Markdown 代码块中提取第一个 JSON 对象。"""
    clean = (text or "").strip()
    if clean.startswith("```"):
        chunks = clean.split("```")
        clean = chunks[1] if len(chunks) > 1 else clean
        if clean.lstrip().startswith("json"):
            clean = clean.lstrip()[4:]
    start = clean.find("{")
    end = clean.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response does not contain a JSON object")
    payload = json.loads(clean[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON response is not an object")
    return payload


def _normalize_llm_report_data(data: dict) -> dict:
    """把模型常见的空值和类型偏差修正为 Schema 可校验的形状。"""
    normalized = dict(data or {})
    for key in (
        "positive_points",
        "negative_points",
        "topic_insights",
        "risk_points",
        "operation_suggestions",
        "evidence_refs",
    ):
        value = normalized.get(key)
        if value is None:
            normalized[key] = []
        elif not isinstance(value, list):
            normalized[key] = [str(value)]
        else:
            normalized[key] = value[:4]

    comments = normalized.get("representative_comments") or {}
    if not isinstance(comments, dict):
        comments = {}
    compact_comments: dict[str, list[dict]] = {}
    for label, items in comments.items():
        compact_comments[str(label)] = []
        if not isinstance(items, list):
            items = [items]
        for item in items[:2]:
            if isinstance(item, dict):
                compact_comments[str(label)].append({
                    "content": str(item.get("content", "")),
                    "sentiment_label": str(item.get("sentiment_label") or label),
                    "sentiment_score": item.get("sentiment_score", 0),
                    "likes": item.get("likes", 0),
                    "platform": item.get("platform", ""),
                    "publish_time": item.get("publish_time", ""),
                })
            else:
                compact_comments[str(label)].append({
                    "content": str(item),
                    "sentiment_label": str(label),
                })
    normalized["representative_comments"] = compact_comments
    return normalized


def _validate_llm_report(data: dict) -> dict:
    """用当前 Pydantic 版本校验并标准化舆情报告。"""
    data = _normalize_llm_report_data(data)
    if hasattr(LLMOpinionReportSchema, "model_validate"):
        report = LLMOpinionReportSchema.model_validate(data)
    else:
        report = LLMOpinionReportSchema(**data)
    return _dump_schema(report)


def _invoke_structured_report(
    model: Any,
    prompt: str,
    prompt_template=None,
) -> dict:
    """优先使用结构化输出能力，失败时回退到文本 JSON 解析。"""
    actual_prompt = prompt_template or get_prompt("opinion_report")
    messages = [
        {"role": "system", "content": actual_prompt.render_system()},
        {"role": "user", "content": prompt},
    ]
    errors: list[str] = []

    if hasattr(model, "with_structured_output"):
        try:
            structured_model = model.with_structured_output(LLMOpinionReportSchema)
            report = _dump_schema(structured_model.invoke(messages))
            report["_generation_mode"] = "schema"
            return report
        except Exception as exc:
            errors.append(f"schema call failed: {type(exc).__name__}: {exc}")
            logger.warning("Structured report schema invocation failed; trying JSON parse: %s", exc)

    json_prompt = (
        f"{prompt}\n\n"
        + actual_prompt.render_section("json_retry_suffix")
    )
    try:
        raw = model.invoke([
            {"role": "system", "content": actual_prompt.render_system()},
            {"role": "user", "content": json_prompt},
        ])
        report = _validate_llm_report(_extract_json_object(_message_content(raw)))
        report["_generation_mode"] = "json_parse"
        return report
    except Exception as exc:
        errors.append(f"json parse failed: {type(exc).__name__}: {exc}")
        raise ValueError("; ".join(errors))


def _render_opinion_prompt(
    query: str,
    anime: dict,
    context: dict[str, Any],
    evidence: list[dict],
    prompt_template=None,
) -> str:
    """渲染舆情提示词，并在最终边界处执行硬字符上限。"""
    actual_prompt = prompt_template or get_prompt("opinion_report")
    prompt = actual_prompt.render(
        query=query or "",
        anime=json.dumps(anime, ensure_ascii=False),
        context=json.dumps(context, ensure_ascii=False),
        evidence=json.dumps(evidence, ensure_ascii=False),
    )
    if len(prompt) <= OPINION_PROMPT_MAX_CHARS:
        return prompt
    return prompt[:OPINION_PROMPT_MAX_CHARS].rstrip() + "\n\n[context truncated]"


def _invoke_opinion_attempt(
    model: Any,
    prompt: str,
    step_name: str,
    detail: str,
    prompt_template=None,
) -> tuple[dict, dict]:
    """完成一次模型调用、输出解析和 Schema 校验，并记录耗时。"""
    start = time.perf_counter()
    report_data = _invoke_structured_report(
        model,
        prompt,
        prompt_template,
    )
    generation_mode = report_data.pop("_generation_mode", "schema_or_json")
    step = {
        "name": step_name,
        "status": "success",
        "detail": f"{detail} via {generation_mode}",
        "elapsed_ms": int((time.perf_counter() - start) * 1000),
    }
    return report_data, step


# ===== 数据预取与主流程 =====

def _prefetch(anime_id: int | None = None, name: str | None = None) -> tuple[dict | None, dict[str, Any], list[dict]]:
    """一次收集报告需要的本地分析结果、外部元数据与执行步骤。"""
    steps: list[dict] = []
    anime, step = timed_step("get_anime_info", fetch_anime_info, anime_id, name)
    steps.append(step)
    if not anime:
        return None, {}, steps

    stats, step = timed_step("get_sentiment_stats", get_sentiment_stats, anime["id"])
    steps.append(step)
    trend, step = timed_step("get_sentiment_trend", get_sentiment_trend, anime["id"])
    steps.append(step)
    topics, step = timed_step("get_topics", get_topics, anime["id"])
    steps.append(step)
    words, step = timed_step("get_wordcloud", get_wordcloud_data, anime["id"], 40)
    steps.append(step)
    comments, step = timed_step("get_representative_comments", fetch_representative_comments, anime["id"])
    steps.append(step)
    aspect, step = timed_step("get_aspect_sentiment", get_aspect_sentiment, anime["id"])
    steps.append(step)
    bangumi, step = timed_step("get_bangumi_info", fetch_bangumi_info, anime["name"])
    steps.append(step)

    context = {
        "stats": stats or {},
        "trend": trend or [],
        "topics": topics or [],
        "wordcloud": words or [],
        "comments": comments or {},
        "aspect": aspect or {},
        "bangumi": bangumi or {},
    }
    return anime, context, steps


def _emit_progress(callback: Callable[..., Any] | None, message: str, progress: int) -> None:
    if callback is None:
        return
    try:
        callback("phase", message=message, progress=progress)
    except Exception:
        logger.debug("Opinion stream callback failed", exc_info=True)


def analyze_public_opinion(
    anime_id: int | None = None,
    name: str | None = None,
    query: str = "",
    event_callback: Callable[..., Any] | None = None,
) -> dict:
    """运行完整舆情诊断，返回报告、执行轨迹及是否触发降级。"""
    _emit_progress(event_callback, "正在准备动漫与评论数据", 10)
    anime, context, steps = _prefetch(anime_id=anime_id, name=name)
    if not anime:
        return {
            "anime": None,
            "report": None,
            "agent_steps": steps,
            "fallback": True,
            "error": "未找到匹配的动漫",
        }

    _emit_progress(event_callback, "正在清洗评论并构建分析上下文", 25)
    safe_comments, comment_security = sanitize_comment_groups(
        context.get("comments", {})
    )
    context = {**context, "comments": safe_comments}
    input_security = inspect_untrusted_text(
        query,
        source="user_input",
        max_chars=1200,
    )
    _emit_progress(event_callback, "正在检索舆情证据", 40)
    retrieval = search_evidence(
        input_security["sanitized_text"] or anime["name"],
        anime_id=anime["id"],
        top_k=10,
    )
    raw_evidence = retrieval.get("evidence", [])
    cleaned_evidence, evidence_security = sanitize_evidence_map(
        {int(anime["id"]): raw_evidence}
    )
    evidence = cleaned_evidence[int(anime["id"])]
    refs = evidence_doc_ids(evidence)
    prompt_template = get_prompt("opinion_report")
    trace = prompt_trace(
        "opinion_report",
        LLM_MODEL,
        retrieval.get("top_k", 10),
        retrieval.get("fallback", True),
        prompt=prompt_template,
    )
    trace["security"] = {
        "input": {
            key: value
            for key, value in input_security.items()
            if key != "sanitized_text"
        },
        "evidence": evidence_security,
        "tool_comments": comment_security,
    }
    steps.append({
        "name": "rag_retrieve" if not retrieval.get("fallback") else "rag_fallback_keyword",
        "status": "success" if evidence else "fallback",
        "detail": f"{retrieval.get('mode')} returned {len(evidence)} evidence items",
        "elapsed_ms": 0,
    })
    _emit_progress(event_callback, "正在生成结构化舆情报告", 60)
    model = get_chat_model(
        temperature=0.2,
        timeout=OPINION_LLM_TIMEOUT,
        max_tokens=OPINION_LLM_MAX_TOKENS,
    )
    # 没有模型配置时直接返回有证据的本地报告，不让接口整体失败。
    if model is None:
        report = build_opinion_fallback(anime, context["stats"], context["topics"], context["comments"], context["aspect"])
        report.evidence_refs = refs
        report.retrieval_evidence = _evidence_models(evidence)
        report.prompt_trace = PromptTrace(**trace)
        report.agent_steps = [AgentStep(**s) for s in steps] + report.agent_steps
        _emit_progress(event_callback, "正在整理本地降级报告", 90)
        return {"anime": anime, "report": report.model_dump(), "agent_steps": [s.model_dump() for s in report.agent_steps], "fallback": True}

    compact_context, compact_evidence = build_compact_opinion_context(
        context,
        evidence,
        query=input_security["sanitized_text"],
        anime_name=anime["name"],
    )
    try:
        prompt = _render_opinion_prompt(
            query or "",
            anime,
            compact_context,
            compact_evidence,
            prompt_template,
        )
        _emit_progress(event_callback, "正在校验报告结构与证据引用", 75)
        report_data, success_step = _invoke_opinion_attempt(
            model,
            prompt,
            "structured_opinion_report",
            "LLM generated schema report from compact prefetched context and RAG evidence",
            prompt_template,
        )
        steps.append(success_step)
        report_data["fallback"] = False
        report_data["evidence_refs"] = [
            ref
            for ref in report_data.get("evidence_refs", [])
            if ref in set(refs)
        ] or refs
        report_data["retrieval_evidence"] = evidence
        report_data["prompt_trace"] = trace
        report_data["agent_steps"] = steps + report_data.get("agent_steps", [])
        _emit_progress(event_callback, "舆情报告已通过校验", 95)
        return {"anime": anime, "report": report_data, "agent_steps": report_data["agent_steps"], "fallback": False}
    except Exception as first_exc:
        # 首次结构化结果失败后，只允许一次更小上下文的修复尝试。
        logger.warning("Opinion structured report failed; retrying with tighter compact context: %s", first_exc)
        steps.append({
            "name": "structured_opinion_report",
            "status": "fallback",
            "detail": str(first_exc),
            "elapsed_ms": 0,
        })

    _emit_progress(event_callback, "正在压缩上下文并修复报告", 82)
    retry_context, retry_evidence = build_compact_opinion_context(
        context,
        evidence,
        trend_limit=6,
        topic_limit=4,
        topic_keyword_limit=4,
        word_limit=15,
        comments_per_label=2,
        comment_text_limit=140,
        evidence_limit=3,
        evidence_text_limit=220,
        evidence_budget=1200,
        bangumi_summary_limit=160,
        query=input_security["sanitized_text"],
        anime_name=anime["name"],
    )
    retry_model = get_chat_model(
        temperature=0,
        timeout=OPINION_LLM_TIMEOUT,
        max_tokens=OPINION_LLM_REPAIR_MAX_TOKENS,
    ) or model
    try:
        retry_prompt = _render_opinion_prompt(
            query or "",
            anime,
            retry_context,
            retry_evidence,
            prompt_template,
        )
        report_data, retry_step = _invoke_opinion_attempt(
            retry_model,
            retry_prompt,
            "structured_opinion_report_compact_retry",
            "LLM generated schema report after tighter compact retry",
            prompt_template,
        )
        steps.append(retry_step)
        report_data["fallback"] = False
        report_data["evidence_refs"] = [
            ref
            for ref in report_data.get("evidence_refs", [])
            if ref in set(refs)
        ] or refs
        report_data["retrieval_evidence"] = evidence
        report_data["prompt_trace"] = trace
        report_data["agent_steps"] = steps + report_data.get("agent_steps", [])
        _emit_progress(event_callback, "修复后的报告已通过校验", 95)
        return {"anime": anime, "report": report_data, "agent_steps": report_data["agent_steps"], "fallback": False}
    except Exception as exc:
        logger.warning("Opinion compact retry failed, using fallback: %s", exc)
        report = build_opinion_fallback(anime, context["stats"], context["topics"], context["comments"], context["aspect"])
        error_step = AgentStep(name="structured_opinion_report_compact_retry", status="fallback", detail=str(exc))
        report.evidence_refs = refs
        report.retrieval_evidence = _evidence_models(evidence)
        report.prompt_trace = PromptTrace(**trace)
        report.agent_steps = [AgentStep(**s) for s in steps] + [error_step] + report.agent_steps
        _emit_progress(event_callback, "正在整理本地降级报告", 90)
        return {"anime": anime, "report": report.model_dump(), "agent_steps": [s.model_dump() for s in report.agent_steps], "fallback": True}






