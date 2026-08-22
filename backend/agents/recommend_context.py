# -*- coding: utf-8 -*-
"""为每个候选检索证据，并把推荐上下文压缩到可控字符预算内。"""
from __future__ import annotations
from difflib import SequenceMatcher
from backend.agents.evidence_excerpt import select_evidence_excerpt
from backend.config import (RECOMMEND_COMMENT_MAX_CHARS, RECOMMEND_CONTEXT_MAX_CHARS,
    RECOMMEND_EVIDENCE_CANDIDATES, RECOMMEND_EVIDENCE_PER_ANIME, RECOMMEND_HISTORY_LIMIT)
from backend.rag.retriever import search_evidence
from backend.rag.storage import get_anime_documents

_SOURCE_GROUPS = {
    "profile": {"anime_knowledge", "anime_profile"},
    "comments": {"comment"},
    "topic_or_sentiment": {"topic", "sentiment_summary"},
    "relations": {"anime_relation", "relation"},
    "platform": {"platform_availability"},
}
_RELATION_QUERY_TERMS = ("相似", "类似", "同类", "续作", "前作", "外传", "同系列", "关联作品")
_FIELD_GAP_LABELS = {
    "profile": "剧情简介证据不足",
    "comments": "高相关评论证据不足",
    "relations": "相似作品映射不足",
    "platform": "播放平台证据不足",
}

def compact_history(history):
    """只保留最近有效对话，并统一 Agent 角色名、限制单条长度。"""
    return [{"role": "assistant" if x.get("role") == "agent" else x.get("role"), "content": str(x.get("content", ""))[:400]}
            for x in (history or [])[-RECOMMEND_HISTORY_LIMIT:] if x.get("role") in {"user", "agent", "assistant"} and x.get("content")]

def _words(candidate):
    """从候选主题中提取少量扩展检索词。"""
    out = []
    for topic in candidate.get("topics", [])[:3]:
        out.extend(str(topic).split("/"))
    return [x.strip() for x in out if x.strip()][:15]

def _source_type(item):
    return str(item.get("source_type") or (item.get("metadata") or {}).get("source_type") or "").strip()

def _source_group(item):
    source_type = _source_type(item)
    return next((name for name, values in _SOURCE_GROUPS.items() if source_type in values), f"other:{source_type}")

def _is_verified_platform(item):
    metadata = item.get("metadata") or {}
    return (
        _source_type(item) == "platform_availability"
        and str(metadata.get("verification_status") or "").lower() == "verified"
        and bool(metadata.get("viewing_platform"))
    )

def _select_evidence_by_source(items, query, limit):
    """先覆盖关键证据类型；类型不足时再按 Rerank 顺序补满证据。"""
    selected, used_groups = [], set()
    relation_query = any(term in str(query or "") for term in _RELATION_QUERY_TERMS)
    priority = []
    if relation_query:
        priority.append("relations")
    priority.extend(("platform", "profile", "comments", "topic_or_sentiment"))
    for group in priority:
        candidates = [item for item in items if _source_group(item) == group]
        if group == "profile":
            candidates.sort(key=lambda item: _source_type(item) != "anime_knowledge")
        elif group == "platform":
            candidates.sort(key=lambda item: not _is_verified_platform(item))
        if candidates and len(selected) < limit:
            selected.append(candidates[0])
            used_groups.add(group)
    for item in items:
        group = _source_group(item)
        if len(selected) >= limit:
            break
        if group in used_groups:
            continue
        selected.append(item)
        used_groups.add(group)
    if len(selected) < limit:
        selected_ids = {id(item) for item in selected}
        for item in items:
            if len(selected) >= limit:
                break
            if id(item) in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(id(item))
    return selected

def evidence_field_coverage(items):
    """区分检索到文档与具体字段是否有可靠来源。"""
    source_types = {_source_type(item) for item in items}
    platform_verified = any(
        _source_type(item) == "platform_availability"
        and str((item.get("metadata") or {}).get("verification_status") or "").lower() == "verified"
        and bool((item.get("metadata") or {}).get("viewing_platform"))
        for item in items
    )
    return {
        "profile": "anime_knowledge" in source_types,
        "comments": "comment" in source_types,
        "relations": bool(source_types & {"anime_relation", "relation"}),
        "platform": platform_verified,
        "topic_or_sentiment": bool(source_types & {"topic", "sentiment_summary"}),
    }

def evidence_field_gaps(coverage):
    return [label for field, label in _FIELD_GAP_LABELS.items() if not coverage.get(field)]

def verified_platform_availability(items):
    """只返回已验证的观看平台，绝不把评论采集来源当播放平台。"""
    platforms = []
    for item in items:
        metadata = item.get("metadata") or {}
        if (
            _source_type(item) == "platform_availability"
            and str(metadata.get("verification_status") or "").lower() == "verified"
        ):
            platforms.extend(str(metadata.get("viewing_platform") or "").split(";"))
    return "、".join(dict.fromkeys(value.strip() for value in platforms if value.strip()))

def retrieve_candidate_evidence(query, candidates, preferences):
    """检索前五个候选；不足三部有证据时才从备用候选补检索。"""
    result_map, modes, raw, attempted = {}, [], 0, []
    prefs = sum((preferences.get(k, [])[-5:] for k in ("likes", "preferred_moods", "preferred_genres")), [])

    def retrieve_one(candidate):
        nonlocal raw
        aid = int(candidate["id"])
        attempted.append(aid)
        expanded = " ".join(map(str, [query, candidate.get("name", ""), *_words(candidate), *prefs]))
        found = search_evidence(expanded, anime_id=aid, top_k=20)
        modes.append(found.get("mode", "unknown"))
        valid, seen = [], set()
        for item in found.get("evidence", []):
            meta = item.get("metadata") or {}
            doc_id = item.get("doc_id") or meta.get("doc_id")
            full_content = str(
                item.get("full_content") or item.get("content") or ""
            )
            try: owner = int(meta.get("anime_id"))
            except (TypeError, ValueError): continue
            if owner != aid or not doc_id or doc_id in seen: continue
            if any(SequenceMatcher(None, full_content, old.get("full_content", "")).ratio() > .88 for old in valid): continue
            seen.add(doc_id)
            valid.append({**item, "content": full_content, "full_content": full_content})
        if not any(_is_verified_platform(item) for item in valid):
            for item in get_anime_documents(aid, {"platform_availability"}, limit=1):
                metadata = item.get("metadata") or {}
                doc_id = item.get("doc_id") or metadata.get("doc_id")
                if (
                    doc_id
                    and doc_id not in seen
                    and str(metadata.get("verification_status") or "").lower() == "verified"
                    and bool(metadata.get("viewing_platform"))
                ):
                    seen.add(doc_id)
                    valid.append(item)
        selected = _select_evidence_by_source(valid, expanded, RECOMMEND_EVIDENCE_PER_ANIME)
        raw += len(selected)
        result_map[aid] = selected

    initial = candidates[:RECOMMEND_EVIDENCE_CANDIDATES]
    for candidate in initial:
        retrieve_one(candidate)
    minimum_eligible = min(3, len(candidates))
    covered = sum(bool(result_map.get(int(candidate["id"]))) for candidate in initial)
    for candidate in candidates[RECOMMEND_EVIDENCE_CANDIDATES:]:
        if covered >= minimum_eligible:
            break
        retrieve_one(candidate)
        covered += int(bool(result_map.get(int(candidate["id"]))))

    eligible_ids = [
        int(candidate["id"])
        for candidate in candidates
        if result_map.get(int(candidate["id"]))
    ][:RECOMMEND_EVIDENCE_CANDIDATES]
    candidate_fields = {
        str(aid): evidence_field_coverage(result_map.get(aid, []))
        for aid in eligible_ids
    }
    field_counts = {
        field: sum(bool(values.get(field)) for values in candidate_fields.values())
        for field in ("profile", "comments", "relations", "platform", "topic_or_sentiment")
    }
    field_coverage = {
        field: bool(eligible_ids) and count == len(eligible_ids)
        for field, count in field_counts.items()
    }
    return result_map, {
        "modes": sorted(set(modes)),
        "candidate_count": len(attempted),
        "covered_candidates": len(eligible_ids),
        "raw_evidence_count": raw,
        "evidence_insufficient": len(eligible_ids) < minimum_eligible,
        "initial_candidate_count": len(initial),
        "attempted_candidate_count": len(attempted),
        "eligible_candidate_count": len(eligible_ids),
        "eligible_candidate_ids": eligible_ids,
        "retrieval_coverage": {
            "covered_candidates": len(eligible_ids),
            "candidate_count": len(attempted),
        },
        "field_coverage": field_coverage,
        "field_coverage_counts": field_counts,
        "candidate_field_coverage": candidate_fields,
        "evidence_gaps": {
            aid: evidence_field_gaps(values)
            for aid, values in candidate_fields.items()
        },
    }

def pack_recommendation_context(candidates, evidence_map, query=""):
    """轮询分配完整句子级证据摘录，并保留每个候选的公平预算。"""
    selected = candidates[:RECOMMEND_EVIDENCE_CANDIDATES]
    before = sum(
        len(str(x.get("full_content") or x.get("content") or ""))
        for candidate in selected
        for x in evidence_map.get(int(candidate["id"]), [])
    )
    remaining = RECOMMEND_CONTEXT_MAX_CHARS
    packed, records = [], []
    count = excerpted = 0
    for candidate in selected:
        aid = int(candidate["id"])
        stats, topics = candidate.get("sentiment") or {}, candidate.get("topics", [])[:3]
        total = int(stats.get("total") or 0)
        evidence_items = evidence_map.get(aid, [])[:RECOMMEND_EVIDENCE_PER_ANIME]
        field_coverage = evidence_field_coverage(evidence_items)
        packed_item = {"anime_id": aid, "name": candidate.get("name"),
            "platform": verified_platform_availability(evidence_items), "data_sources": candidate.get("platform"),
            "structured_knowledge": candidate.get("structured_knowledge", {}),
            "comment_count": candidate.get("comment_count", 0), "scores": {k: candidate.get(k, 0) for k in ("match_score", "sentiment_score", "popularity_score", "preference_penalty", "final_score")},
            "match_tags": candidate.get("match_tags", []), "topics": topics,
            "sentiment": {"total": total, "positive": stats.get("positive", 0), "neutral": stats.get("neutral", 0), "negative": stats.get("negative", 0), "positive_rate": round(stats.get("positive", 0)/total, 4) if total else 0},
            "evidence": [], "evidence_insufficient": not bool(evidence_items),
            "field_coverage": field_coverage, "evidence_gaps": evidence_field_gaps(field_coverage)}
        packed.append(packed_item)
        records.append((candidate, packed_item, evidence_items))

    pending = sum(len(items) for _candidate, _packed, items in records)
    for evidence_index in range(RECOMMEND_EVIDENCE_PER_ANIME):
        for candidate, packed_item, items in records:
            if evidence_index >= len(items):
                continue
            item = items[evidence_index]
            allowance = min(
                remaining,
                max(
                    RECOMMEND_COMMENT_MAX_CHARS,
                    remaining // max(1, pending),
                ),
            )
            full_content = str(
                item.get("full_content") or item.get("content") or ""
            )
            excerpt = select_evidence_excerpt(
                full_content,
                query=query,
                anime_name=str(candidate.get("name") or ""),
                topics=candidate.get("topics", []),
                target_chars=allowance,
                remaining_chars=remaining,
            )
            pending -= 1
            if not excerpt:
                continue
            packed_item["evidence"].append({
                "doc_id": item.get("doc_id") or (item.get("metadata") or {}).get("doc_id"),
                "source_type": item.get("source_type"),
                "evidence_excerpt": excerpt,
                "similarity": item.get("similarity", 0),
            })
            packed_item["evidence_insufficient"] = False
            remaining -= len(excerpt)
            count += 1
            excerpted += int(len(excerpt) < len(full_content.strip()))

    after = RECOMMEND_CONTEXT_MAX_CHARS - remaining
    dropped = max(0, sum(len(items) for _candidate, _packed, items in records) - count)
    return packed, {"max_chars": RECOMMEND_CONTEXT_MAX_CHARS, "before_chars": before, "after_chars": after,
        "estimated_tokens": int(after / 1.5) if after else 0, "raw_evidence_count": sum(map(len, evidence_map.values())),
        "final_evidence_count": count, "truncated_or_dropped": excerpted + dropped,
        "excerpted_count": excerpted, "dropped_count": dropped}
