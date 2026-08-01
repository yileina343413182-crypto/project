# -*- coding: utf-8 -*-
"""LangChain tools and local tool helpers for Agent Center."""

from __future__ import annotations

import time
import math
from difflib import SequenceMatcher
from typing import Annotated, Any

from langgraph.prebuilt import InjectedState

from backend.database import (
    get_all_anime,
    get_aspect_sentiment,
    get_comments,
    get_db,
    get_sentiment_stats,
    get_sentiment_trend,
    get_topics,
    get_wordcloud_data,
)
from backend.services.bangumi import search_anime
from backend.agents.memory import get_user_preferences as load_preferences, update_user_preferences as save_preferences
from backend.agents.prompt_security import sanitize_search_result
from backend.rag.retriever import search_evidence

try:
    from langchain_core.tools import tool
except Exception:  # pragma: no cover
    def tool(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco


def timed_step(name: str, fn, *args, **kwargs) -> tuple[Any, dict]:
    start = time.perf_counter()
    try:
        data = fn(*args, **kwargs)
        return data, {
            "name": name,
            "status": "success",
            "detail": "工具调用成功",
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
        }
    except Exception as exc:
        return None, {
            "name": name,
            "status": "error",
            "detail": str(exc),
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
        }


def fetch_anime_info(anime_id: int | None = None, name: str | None = None) -> dict | None:
    items = get_all_anime()
    if anime_id is not None:
        for item in items:
            if int(item["id"]) == int(anime_id):
                return item
    keyword = (name or "").strip()
    if keyword:
        exact = [item for item in items if item["name"] == keyword]
        if exact:
            return exact[0]
        best = None
        best_score = 0.0
        for item in items:
            score = SequenceMatcher(None, keyword.lower(), item["name"].lower()).ratio()
            if keyword.lower() in item["name"].lower():
                score += 0.3
            if score > best_score:
                best_score = score
                best = item
        if best and best_score >= 0.3:
            return best
    return None


def fetch_representative_comments(anime_id: int, limit_per_label: int = 3) -> dict[str, list[dict]]:
    conn = get_db()
    result: dict[str, list[dict]] = {}
    for label in ("positive", "neutral", "negative"):
        rows = conn.execute(
            """SELECT content, sentiment_label, sentiment_score, likes, platform, publish_time
               FROM comments
               WHERE anime_id = ? AND sentiment_label = ? AND content != ''
               ORDER BY likes DESC, LENGTH(content) DESC, id ASC
               LIMIT ?""",
            (anime_id, label, limit_per_label),
        ).fetchall()
        result[label] = [dict(row) for row in rows]
    conn.close()
    return result


def fetch_bangumi_info(name: str) -> dict:
    data = search_anime(name)
    return data or {"bgm_id": None, "name": name, "summary": "", "rating": 0, "image": ""}

def _get_sentiment_stats_map() -> dict[int, dict[str, int]]:
    conn = get_db()
    rows = conn.execute(
        """SELECT anime_id, sentiment_label, COUNT(*) as cnt
           FROM comments
           WHERE sentiment_label IS NOT NULL
           GROUP BY anime_id, sentiment_label"""
    ).fetchall()
    conn.close()

    stats_map: dict[int, dict[str, int]] = {}
    for row in rows:
        anime_id = int(row["anime_id"])
        label = row["sentiment_label"]
        stats = stats_map.setdefault(anime_id, {"positive": 0, "negative": 0, "neutral": 0, "total": 0})
        if label in stats:
            stats[label] = int(row["cnt"])
    for stats in stats_map.values():
        stats["total"] = stats["positive"] + stats["negative"] + stats["neutral"]
    return stats_map


def build_candidate_pool(query: str, user_id: int | None = None, limit: int = 8) -> list[dict]:
    keyword = query.strip().lower()
    preferences = load_preferences(user_id) if user_id else {}
    dislikes = " ".join(preferences.get("dislikes", [])) if preferences else ""
    items = get_all_anime()
    stats_map = _get_sentiment_stats_map()
    ranked = []

    for item in items:
        anime_id = int(item["id"])
        name = item.get("name", "")
        stats = stats_map.get(anime_id, {"positive": 0, "negative": 0, "neutral": 0, "total": 0})
        total = stats.get("total") or 0
        positive_rate = (stats.get("positive", 0) / total) if total else 0
        similarity = SequenceMatcher(None, keyword, name.lower()).ratio() if keyword else 0
        if keyword and keyword in name.lower():
            similarity += 0.5
        penalty = 0.8 if dislikes and any(word and word.lower() in name.lower() for word in dislikes.split()) else 0
        match_score = min(1.5, similarity)
        sentiment_score = positive_rate if total else 0.15
        popularity_score = min(0.45, math.log1p(max(0, item.get("comment_count", 0))) / 20)
        score = match_score + sentiment_score + popularity_score - penalty
        ranked.append({
            **item,
            "score": round(score, 4),
            "match_score": round(match_score, 4),
            "sentiment_score": round(sentiment_score, 4),
            "popularity_score": round(popularity_score, 4),
            "preference_penalty": round(penalty, 4),
            "final_score": round(score, 4),
            "sentiment": stats,
            "topics": [],
            "comments": [],
            "match_tags": ["口碑", "评论证据"] if positive_rate >= 0.4 else ["候选", "可探索"],
        })

    ranked.sort(key=lambda x: (x["score"], x.get("comment_count", 0)), reverse=True)
    top_candidates = ranked[:limit]
    for item in top_candidates:
        topics = []
        for topic_item in get_topics(item["id"])[:3]:
            words = topic_item.get("keywords") or []
            topic_words = [w.get("word", "") for w in words[:4] if isinstance(w, dict)]
            if topic_words:
                topics.append("/".join(topic_words))
        item["topics"] = topics
        item["comments"] = fetch_representative_comments(item["id"], limit_per_label=1).get("positive", [])
    return top_candidates


@tool("get_anime_info")
def get_anime_info_tool(anime_id: int | None = None, name: str | None = None) -> dict:
    """Get anime metadata from the local SQLite database by id or fuzzy name."""
    return fetch_anime_info(anime_id, name) or {}


@tool("get_sentiment_stats")
def get_sentiment_stats_tool(anime_id: int) -> dict:
    """Get positive, neutral, negative sentiment counts for an anime."""
    return get_sentiment_stats(anime_id)


@tool("get_sentiment_trend")
def get_sentiment_trend_tool(anime_id: int) -> list[dict]:
    """Get date-based sentiment trend data for an anime."""
    return get_sentiment_trend(anime_id)


@tool("get_topics")
def get_topics_tool(anime_id: int) -> list[dict]:
    """Get LDA topic keywords for an anime."""
    return get_topics(anime_id)


@tool("get_wordcloud")
def get_wordcloud_tool(anime_id: int) -> list[dict]:
    """Get top word frequency data for an anime."""
    return get_wordcloud_data(anime_id, top_n=40)


@tool("get_representative_comments")
def get_representative_comments_tool(anime_id: int) -> dict:
    """Get representative positive, neutral, and negative comments."""
    return fetch_representative_comments(anime_id)


@tool("get_aspect_sentiment")
def get_aspect_sentiment_tool(anime_id: int) -> dict:
    """Get aspect-level sentiment statistics."""
    return get_aspect_sentiment(anime_id)


@tool("get_bangumi_info")
def get_bangumi_info_tool(name: str) -> dict:
    """Search Bangumi metadata for an anime name."""
    return fetch_bangumi_info(name)


@tool("search_anime_candidates")
def search_anime_candidates_tool(query: str, user_id: int | None = None) -> list[dict]:
    """Search and rank local anime candidates by query and preference."""
    return build_candidate_pool(query, user_id=user_id)


@tool("rank_by_sentiment")
def rank_by_sentiment_tool(candidate_ids: list[int]) -> list[dict]:
    """Rank candidate anime ids by positive sentiment rate and comment count."""
    ranked = []
    anime_map = {item["id"]: item for item in get_all_anime()}
    for anime_id in candidate_ids:
        item = anime_map.get(anime_id)
        if not item:
            continue
        stats = get_sentiment_stats(anime_id)
        total = stats.get("total") or 0
        item = {**item, "sentiment": stats, "positive_rate": stats.get("positive", 0) / total if total else 0}
        ranked.append(item)
    ranked.sort(key=lambda x: (x["positive_rate"], x.get("comment_count", 0)), reverse=True)
    return ranked


@tool("get_recommendation_evidence")
def get_recommendation_evidence_tool(anime_id: int) -> dict:
    """Get sentiment, topics, and representative comments for one recommendation."""
    return {
        "sentiment": get_sentiment_stats(anime_id),
        "topics": get_topics(anime_id)[:3],
        "comments": fetch_representative_comments(anime_id, limit_per_label=2),
    }


@tool("get_user_preferences")
def get_user_preferences_tool(user_id: int) -> dict:
    """Read structured user recommendation preferences."""
    return load_preferences(user_id)


@tool("update_user_preferences")
def update_user_preferences_tool(user_id: int, updates: dict) -> dict:
    """Update structured user recommendation preferences."""
    return save_preferences(user_id, updates)


def _recommend_candidate_from_state(state: dict, anime_id: int) -> dict:
    candidates = {
        int(candidate["id"]): candidate
        for candidate in state.get("candidates", [])
    }
    if int(anime_id) not in candidates:
        raise ValueError(f"anime_id {anime_id} is outside the candidate pool")
    return candidates[int(anime_id)]


@tool("inspect_recommendation_candidate")
def inspect_recommendation_candidate_tool(
    anime_id: int,
    state: Annotated[dict, InjectedState],
) -> dict:
    """Inspect one allowed recommendation candidate and its prepared evidence."""
    candidate = _recommend_candidate_from_state(state, anime_id)
    return {
        "anime_id": int(anime_id),
        "name": candidate.get("name", ""),
        "platform": candidate.get("platform", ""),
        "comment_count": candidate.get("comment_count", 0),
        "scores": {
            key: candidate.get(key, 0)
            for key in (
                "match_score",
                "sentiment_score",
                "popularity_score",
                "preference_penalty",
                "final_score",
            )
        },
        "sentiment": candidate.get("sentiment", {}),
        "topics": candidate.get("topics", []),
        "evidence": state.get("evidence_map", {}).get(int(anime_id), []),
    }


@tool("search_candidate_comments")
def search_candidate_comments_tool(
    anime_id: int,
    query: str,
    state: Annotated[dict, InjectedState],
) -> dict:
    """Search local comment evidence for one candidate from the current pool."""
    _recommend_candidate_from_state(state, anime_id)
    return sanitize_search_result(
        search_evidence(query, anime_id=int(anime_id), top_k=3)
    )


@tool("compare_candidate_sentiment")
def compare_candidate_sentiment_tool(
    anime_ids: list[int],
    state: Annotated[dict, InjectedState],
) -> list[dict]:
    """Compare sentiment and ranking scores for candidates from the current pool."""
    result = []
    for anime_id in anime_ids[:5]:
        candidate = _recommend_candidate_from_state(state, anime_id)
        result.append(
            {
                "anime_id": int(anime_id),
                "name": candidate.get("name", ""),
                "final_score": candidate.get("final_score", 0),
                "sentiment": candidate.get("sentiment", {}),
            }
        )
    return result


OPINION_TOOLS = [
    get_anime_info_tool,
    get_sentiment_stats_tool,
    get_sentiment_trend_tool,
    get_topics_tool,
    get_wordcloud_tool,
    get_representative_comments_tool,
    get_aspect_sentiment_tool,
    get_bangumi_info_tool,
]

RECOMMEND_TOOLS = [
    inspect_recommendation_candidate_tool,
    search_candidate_comments_tool,
    compare_candidate_sentiment_tool,
]
