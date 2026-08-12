# -*- coding: utf-8 -*-
"""为每个候选检索证据，并把推荐上下文压缩到可控字符预算内。"""
from __future__ import annotations
import re
from difflib import SequenceMatcher
from backend.config import (RECOMMEND_COMMENT_MAX_CHARS, RECOMMEND_CONTEXT_MAX_CHARS,
    RECOMMEND_EVIDENCE_CANDIDATES, RECOMMEND_EVIDENCE_PER_ANIME, RECOMMEND_HISTORY_LIMIT)
from backend.rag.retriever import search_evidence

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

def retrieve_candidate_evidence(query, candidates, preferences):
    """逐候选检索、校验归属、去重，避免证据串到其他动漫。"""
    result_map, modes, raw = {}, [], 0
    prefs = sum((preferences.get(k, [])[-5:] for k in ("likes", "preferred_moods", "preferred_genres")), [])
    for candidate in candidates[:RECOMMEND_EVIDENCE_CANDIDATES]:
        aid = int(candidate["id"])
        expanded = " ".join(map(str, [query, candidate.get("name", ""), *_words(candidate), *prefs]))
        found = search_evidence(expanded, anime_id=aid, top_k=RECOMMEND_EVIDENCE_PER_ANIME * 2)
        modes.append(found.get("mode", "unknown"))
        valid, seen = [], set()
        for item in found.get("evidence", []):
            meta = item.get("metadata") or {}
            doc_id = item.get("doc_id") or meta.get("doc_id")
            try: owner = int(meta.get("anime_id"))
            except (TypeError, ValueError): continue
            if owner != aid or not doc_id or doc_id in seen: continue
            if any(SequenceMatcher(None, item.get("content", ""), old.get("content", "")).ratio() > .88 for old in valid): continue
            seen.add(doc_id); valid.append(item)
            if len(valid) >= RECOMMEND_EVIDENCE_PER_ANIME: break
        raw += len(valid); result_map[aid] = valid
    covered = sum(bool(x) for x in result_map.values())
    return result_map, {"modes": sorted(set(modes)), "candidate_count": min(len(candidates), RECOMMEND_EVIDENCE_CANDIDATES),
        "covered_candidates": covered, "raw_evidence_count": raw, "evidence_insufficient": covered == 0}

def pack_recommendation_context(candidates, evidence_map):
    """按全局字符预算裁剪证据，返回紧凑候选和预算诊断。"""
    before = sum(len(str(x.get("content", ""))) for values in evidence_map.values() for x in values)
    remaining, packed, count, truncated = RECOMMEND_CONTEXT_MAX_CHARS, [], 0, 0
    for candidate in candidates[:RECOMMEND_EVIDENCE_CANDIDATES]:
        aid, docs = int(candidate["id"]), []
        for item in evidence_map.get(aid, []):
            if remaining <= 0: break
            text = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", str(item.get("content", "")))).strip()
            limit = min(RECOMMEND_COMMENT_MAX_CHARS, remaining)
            if len(text) > limit: text, truncated = text[:limit].rstrip() + "…", truncated + 1
            remaining -= len(text); count += 1
            docs.append({"doc_id": item.get("doc_id") or (item.get("metadata") or {}).get("doc_id"),
                         "source_type": item.get("source_type"), "content": text, "similarity": item.get("similarity", 0)})
        stats, topics = candidate.get("sentiment") or {}, candidate.get("topics", [])[:3]
        total = int(stats.get("total") or 0)
        packed.append({"anime_id": aid, "name": candidate.get("name"), "platform": candidate.get("platform"),
            "comment_count": candidate.get("comment_count", 0), "scores": {k: candidate.get(k, 0) for k in ("match_score", "sentiment_score", "popularity_score", "preference_penalty", "final_score")},
            "match_tags": candidate.get("match_tags", []), "topics": topics,
            "sentiment": {"total": total, "positive": stats.get("positive", 0), "neutral": stats.get("neutral", 0), "negative": stats.get("negative", 0), "positive_rate": round(stats.get("positive", 0)/total, 4) if total else 0},
            "evidence": docs, "evidence_insufficient": not docs})
    after = RECOMMEND_CONTEXT_MAX_CHARS - remaining
    return packed, {"max_chars": RECOMMEND_CONTEXT_MAX_CHARS, "before_chars": before, "after_chars": after,
        "estimated_tokens": int(after / 1.5) if after else 0, "raw_evidence_count": sum(map(len, evidence_map.values())),
        "final_evidence_count": count, "truncated_or_dropped": truncated + max(0, sum(map(len, evidence_map.values())) - count)}
