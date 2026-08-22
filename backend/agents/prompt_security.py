# -*- coding: utf-8 -*-
"""用确定性规则守住用户输入、检索证据与 LLM 之间的信任边界。

这些函数不判断内容观点是否正确，只识别提示词注入、角色伪装、控制字符等
风险，并在内容进入模型上下文或长期偏好前清洗、截断或过滤。
"""

from __future__ import annotations

import re
from typing import Any

from backend.agents.evidence_excerpt import select_evidence_excerpt


_CONTROL_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]"
)
# 每个信号由“名称、正则、风险权重”组成；累计分数映射为风险等级。
_SIGNALS = (
    (
        "instruction_override",
        re.compile(
            r"(?:忽略|无视|绕过|覆盖|忘掉).{0,24}"
            r"(?:之前|以上|系统|开发者|规则|指令)"
            r"|ignore.{0,24}(?:previous|prior|system|instructions?)",
            re.IGNORECASE,
        ),
        3,
    ),
    (
        "role_spoofing",
        re.compile(
            r"(?:^|\n)\s*(?:system|assistant|developer|tool|系统|开发者)"
            r"\s*[:：]",
            re.IGNORECASE,
        ),
        2,
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"(?:显示|泄露|输出|告诉).{0,24}"
            r"(?:system\s*prompt|系统提示|隐藏指令|开发者消息)"
            r"|(?:reveal|show|print|leak).{0,24}"
            r"(?:system\s*prompt|hidden\s*instructions?|developer\s*message)",
            re.IGNORECASE,
        ),
        2,
    ),
    (
        "tool_manipulation",
        re.compile(
            r"(?:调用|执行|使用).{0,24}(?:工具|函数|tool)"
            r"|tool[_\s-]?call"
            r"|update_user_preferences",
            re.IGNORECASE,
        ),
        2,
    ),
    (
        "data_exfiltration",
        re.compile(
            r"(?:发送|上传|外传|窃取).{0,32}(?:数据|密钥|token|密码|记录)"
            r"|(?:send|upload|exfiltrate|steal).{0,32}"
            r"(?:data|secret|token|password|records?)",
            re.IGNORECASE,
        ),
        3,
    ),
    (
        "encoded_payload",
        re.compile(r"(?:[A-Za-z0-9+/]{120,}={0,2})"),
        1,
    ),
)


def inspect_untrusted_text(
    value: Any,
    *,
    source: str,
    max_chars: int = 4000,
) -> dict[str, Any]:
    """清理并评分一段不可信文本，返回风险、命中信号和截断信息。"""
    text = _CONTROL_CHARS.sub("", str(value or ""))
    truncated = len(text) > max_chars
    text = text[:max_chars]
    flags: list[str] = []
    score = 0
    for name, pattern, weight in _SIGNALS:
        if pattern.search(text):
            flags.append(name)
            score += weight
    risk = "high" if score >= 3 else "medium" if score else "low"
    return {
        "source": source,
        "risk": risk,
        "flags": flags,
        "score": score,
        "truncated": truncated,
        "sanitized_text": text,
        "trust_level": "untrusted",
    }


def sanitize_evidence_map(
    evidence_map: dict[int, list[dict]],
) -> tuple[dict[int, list[dict]], dict[str, Any]]:
    """逐条检查候选证据：高风险项丢弃，中风险项保留但标记。"""
    cleaned: dict[int, list[dict]] = {}
    flagged_count = 0
    filtered_count = 0
    flags: set[str] = set()
    for anime_id, items in (evidence_map or {}).items():
        safe_items = []
        for item in items or []:
            full_content = str(
                item.get("full_content") or item.get("content") or ""
            )
            inspection = inspect_untrusted_text(
                full_content,
                source="rag_evidence",
                max_chars=max(1, len(full_content)),
            )
            flags.update(inspection["flags"])
            if inspection["flags"]:
                flagged_count += 1
            if inspection["risk"] == "high":
                filtered_count += 1
                continue
            metadata = dict(item.get("metadata") or {})
            metadata["security"] = {
                "trust_level": "untrusted",
                "risk": inspection["risk"],
                "flags": inspection["flags"],
            }
            safe_items.append(
                {
                    **item,
                    "content": inspection["sanitized_text"],
                    "full_content": inspection["sanitized_text"],
                    "metadata": metadata,
                }
            )
        cleaned[int(anime_id)] = safe_items
    return cleaned, {
        "flagged_count": flagged_count,
        "filtered_count": filtered_count,
        "flags": sorted(flags),
    }


def sanitize_search_result(
    result: dict,
    *,
    query: str = "",
    anime_name: str = "",
    topics: list | None = None,
) -> dict:
    """清洗单次 RAG 搜索结果并把安全诊断附加到返回值。"""
    payload = dict(result or {})
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return payload
    cleaned, diagnostics = sanitize_evidence_map({0: evidence})
    prompt_evidence = []
    remaining = 1200
    pending = len(cleaned[0])
    for item in cleaned[0]:
        allowance = min(remaining, max(160, remaining // max(1, pending)))
        excerpt = select_evidence_excerpt(
            item.get("full_content") or item.get("content") or "",
            query=query,
            anime_name=anime_name,
            topics=topics,
            target_chars=allowance,
            remaining_chars=allowance,
        )
        pending -= 1
        if not excerpt:
            continue
        prompt_evidence.append({
            key: value
            for key, value in item.items()
            if key not in {"content", "full_content"}
        } | {"evidence_excerpt": excerpt})
        remaining -= len(excerpt)
    payload["evidence"] = prompt_evidence
    payload["security"] = diagnostics
    return payload


def sanitize_preference_suggestions(
    updates: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """过滤 LLM 建议写入长期记忆的偏好，避免注入内容持久化。"""
    cleaned: dict[str, list[str]] = {}
    filtered_count = 0
    flags: set[str] = set()
    for key, raw_values in (updates or {}).items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        safe_values = []
        for value in values[:10]:
            inspection = inspect_untrusted_text(
                value,
                source="model_preference_suggestion",
                max_chars=80,
            )
            flags.update(inspection["flags"])
            if inspection["risk"] != "low":
                filtered_count += 1
                continue
            text = inspection["sanitized_text"].strip()
            if text and text not in safe_values:
                safe_values.append(text)
        if safe_values:
            cleaned[str(key)] = safe_values
    return cleaned, {
        "filtered_count": filtered_count,
        "flags": sorted(flags),
    }


def sanitize_comment_groups(
    comments: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, Any]]:
    """按情感分组清洗代表评论，并汇总被标记/过滤数量。"""
    cleaned: dict[str, list[dict]] = {}
    flagged_count = 0
    filtered_count = 0
    flags: set[str] = set()
    for label, items in (comments or {}).items():
        safe_items = []
        for item in items or []:
            inspection = inspect_untrusted_text(
                item.get("content", ""),
                source="tool_comment",
                max_chars=600,
            )
            flags.update(inspection["flags"])
            if inspection["flags"]:
                flagged_count += 1
            if inspection["risk"] == "high":
                filtered_count += 1
                continue
            safe_items.append(
                {
                    **item,
                    "content": inspection["sanitized_text"],
                }
            )
        cleaned[str(label)] = safe_items
    return cleaned, {
        "flagged_count": flagged_count,
        "filtered_count": filtered_count,
        "flags": sorted(flags),
    }
