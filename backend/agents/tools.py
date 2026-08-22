# -*- coding: utf-8 -*-
"""Agent 中心可调用的只读工具，以及候选构建等本地辅助函数。

工具返回普通字典/列表，既可被 LangGraph ``ToolNode`` 调用，也可由确定性
流程直接复用。涉及推荐候选的工具会校验 ID 是否属于当前状态候选池。
"""

from __future__ import annotations

import time
import math
import re
from difflib import SequenceMatcher
from typing import Annotated, Any

from langgraph.prebuilt import InjectedState
from sqlalchemy import func, select

from backend.database import (
    get_all_anime,
    get_aspect_sentiment,
    get_comments,
    get_sentiment_stats,
    get_sentiment_trend,
    get_topics,
    get_wordcloud_data,
)
from backend.database import orm_session
from backend.db.models import Comment, RagDocument, Topic
from backend.services.bangumi import search_anime
from backend.agents.memory import (
    get_non_recommendable_anime_ids,
    get_user_preferences as load_preferences,
    update_user_preferences as save_preferences,
)
from backend.agents.prompt_security import sanitize_search_result
from backend.agents.recommend_context import verified_platform_availability
from backend.rag.retriever import search_evidence
from backend.rag.storage import get_active_collection, query_terms

try:
    from langchain_core.tools import tool
except Exception:  # pragma: no cover
    def tool(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco


def timed_step(name: str, fn, *args, **kwargs) -> tuple[Any, dict]:
    """执行普通函数，并把成功/异常及耗时转换为统一步骤记录。"""
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


# ===== 不依赖 LangChain 的本地查询辅助函数 =====

def fetch_anime_info(anime_id: int | None = None, name: str | None = None) -> dict | None:
    """优先按 ID，再按精确名称/模糊相似度查找动漫。"""
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
    """为三类情感分别抽取高置信度代表评论。"""
    result: dict[str, list[dict]] = {}
    with orm_session() as session:
        for label in ("positive", "neutral", "negative"):
            rows = session.execute(
                select(
                    Comment.content,
                    Comment.sentiment_label,
                    Comment.sentiment_score,
                    Comment.likes,
                    Comment.platform,
                    Comment.publish_time,
                )
                .where(
                    Comment.anime_id == anime_id,
                    Comment.sentiment_label == label,
                    Comment.content != "",
                )
                .order_by(Comment.likes.desc(), func.length(Comment.content).desc(), Comment.id)
                .limit(limit_per_label)
            ).mappings().all()
            result[label] = []
            for row in rows:
                item = dict(row)
                item["publish_time"] = str(item["publish_time"] or "")
                result[label].append(item)
    return result


def fetch_bangumi_info(name: str) -> dict:
    data = search_anime(name)
    return data or {"bgm_id": None, "name": name, "summary": "", "rating": 0, "image": ""}

def _get_sentiment_stats_map() -> dict[int, dict[str, int]]:
    """一次聚合全部动漫情感计数，避免候选循环中重复查询。"""
    with orm_session() as session:
        rows = session.execute(
            select(Comment.anime_id, Comment.sentiment_label, func.count().label("cnt"))
            .where(Comment.sentiment_label.is_not(None))
            .group_by(Comment.anime_id, Comment.sentiment_label)
        ).mappings().all()

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


_CANDIDATE_LOW_INFO_TERMS = {
    "推荐", "动漫", "动画", "番剧", "一部", "一些", "想看", "有没有",
    "什么", "比较", "最好", "希望", "喜欢", "不要", "避开", "关于",
    "主题", "旗下", "制作", "制作公司", "出品",
}

_COMPANY_ALIASES = {
    "京阿尼": "京都アニメーション",
    "京都动画": "京都アニメーション",
    "京都動畫": "京都アニメーション",
    "kyoani": "京都アニメーション",
}
_COMPANY_CONSTRAINT_OPT_OUTS = (
    "不限制作公司", "不限定制作公司", "制作公司不限", "不一定要该制作公司",
    "其他公司也可以", "不必是该公司",
)


def _normalize_match_text(value: object) -> str:
    """统一标题和关键词的匹配形式，忽略空白及常见分隔符。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").lower())


def _intent_terms(value: object) -> list[str]:
    """提取用于候选匹配的有效词项，并过滤推荐请求中的低信息词。"""
    result = []
    for term in query_terms(str(value or "")):
        normalized = _normalize_match_text(term)
        if (
            len(normalized) > 1
            and normalized not in _CANDIDATE_LOW_INFO_TERMS
            and normalized not in result
        ):
            result.append(normalized)
    return result


def _topic_terms(value: object) -> list[str]:
    """兼容 Topic.keywords 的字典列表、字符串列表和异常旧数据。"""
    if not isinstance(value, list):
        value = [value]
    result = []
    for item in value:
        word = item.get("word", "") if isinstance(item, dict) else item
        normalized = _normalize_match_text(word)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _get_topic_terms_map() -> dict[int, list[str]]:
    """一次读取全部主题词，避免为候选语义评分产生 N+1 查询。"""
    with orm_session() as session:
        rows = session.execute(select(Topic.anime_id, Topic.keywords)).all()

    terms_map: dict[int, list[str]] = {}
    for anime_id, keywords in rows:
        terms = terms_map.setdefault(int(anime_id), [])
        for term in _topic_terms(keywords):
            if term not in terms:
                terms.append(term)
    return terms_map


def _split_knowledge_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[、,，;/；]", value) if part.strip()]


def _parse_structured_knowledge(content: str) -> dict[str, Any]:
    """从已索引的知识文档提取候选排序需要的稳定结构化字段。"""
    result: dict[str, Any] = {
        "summary": "",
        "genres": [],
        "moods": [],
        "production_companies": [],
        "year": "",
        "work_type": "",
    }
    for line in str(content or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if "无剧透简介：" in text and not result["summary"]:
            result["summary"] = text.split("无剧透简介：", 1)[1].strip()
        elif text.startswith("题材标签："):
            result["genres"] = _split_knowledge_values(text.split("：", 1)[1])
        elif text.startswith("氛围标签："):
            result["moods"] = _split_knowledge_values(text.split("：", 1)[1])
        elif text.startswith("制作公司："):
            result["production_companies"] = _split_knowledge_values(text.split("：", 1)[1])
        elif text.startswith("年代："):
            result["year"] = text.split("：", 1)[1].strip()
        elif text.startswith("作品类型："):
            result["work_type"] = text.split("：", 1)[1].strip()
    return result


def _get_structured_knowledge_map() -> dict[int, dict[str, Any]]:
    """一次读取活动集合中的作品知识，避免候选循环产生 N+1 查询。"""
    collection_name = get_active_collection()
    if not collection_name:
        return {}
    with orm_session() as session:
        rows = session.execute(
            select(RagDocument.anime_id, RagDocument.content).where(
                RagDocument.collection_name == collection_name,
                RagDocument.source_type == "anime_knowledge",
                RagDocument.anime_id.is_not(None),
            )
        ).all()
    return {
        int(anime_id): _parse_structured_knowledge(content)
        for anime_id, content in rows
    }


def _company_search_fields(companies: list[str]) -> list[str]:
    fields = [_normalize_match_text(company) for company in companies]
    normalized_companies = set(fields)
    for alias, canonical in _COMPANY_ALIASES.items():
        if _normalize_match_text(canonical) in normalized_companies:
            fields.append(_normalize_match_text(alias))
    return list(dict.fromkeys(field for field in fields if field))


def _requested_production_companies(
    query: str,
    knowledge_map: dict[int, dict[str, Any]],
) -> set[str]:
    """识别用户明确写出的制作公司；命中后作为候选硬约束。"""
    normalized_query = _normalize_match_text(query)
    if any(_normalize_match_text(value) in normalized_query for value in _COMPANY_CONSTRAINT_OPT_OUTS):
        return set()
    requested = {
        _normalize_match_text(canonical)
        for alias, canonical in _COMPANY_ALIASES.items()
        if _normalize_match_text(alias) in normalized_query
    }
    for knowledge in knowledge_map.values():
        for company in knowledge.get("production_companies", []):
            normalized_company = _normalize_match_text(company)
            if normalized_company and normalized_company in normalized_query:
                requested.add(normalized_company)
    return requested


def _matches_term(term: str, searchable_fields: list[str]) -> bool:
    return any(term in field for field in searchable_fields if field)


def build_candidate_pool(
    query: str,
    user_id: int | None = None,
    limit: int = 8,
    excluded_anime_ids: list[int] | set[int] | None = None,
) -> list[dict]:
    """综合本轮意图、结构化知识、低权重长期偏好、口碑与热度生成候选。"""
    keyword = query.strip()
    preferences = load_preferences(user_id) if user_id else {}
    preferred_values = [
        str(value)
        for key in ("preferred_genres", "preferred_moods", "likes")
        for value in preferences.get(key, [])
        if str(value or "").strip()
    ]
    dislike_values = [
        str(value)
        for value in preferences.get("dislikes", [])
        if str(value or "").strip()
    ]
    preference_terms = _intent_terms(" ".join(preferred_values))
    dislike_terms = _intent_terms(" ".join(dislike_values))
    intent_terms = [term for term in _intent_terms(keyword) if term not in dislike_terms]
    normalized_query = _normalize_match_text(keyword)
    items = get_all_anime()
    excluded_ids = get_non_recommendable_anime_ids(user_id) if user_id else set()
    for value in excluded_anime_ids or []:
        try:
            excluded_ids.add(int(value))
        except (TypeError, ValueError):
            continue
    stats_map = _get_sentiment_stats_map()
    topic_terms_map = _get_topic_terms_map()
    knowledge_map = _get_structured_knowledge_map()
    required_companies = _requested_production_companies(keyword, knowledge_map)
    ranked = []

    for item in items:
        anime_id = int(item["id"])
        if anime_id in excluded_ids:
            continue
        name = item.get("name", "")
        normalized_name = _normalize_match_text(name)
        knowledge = knowledge_map.get(anime_id, {})
        company_fields = _company_search_fields(knowledge.get("production_companies", []))
        if required_companies and not required_companies.intersection(company_fields):
            continue
        structured_fields = [
            _normalize_match_text(knowledge.get("summary", "")),
            *(_normalize_match_text(value) for value in knowledge.get("genres", [])),
            *(_normalize_match_text(value) for value in knowledge.get("moods", [])),
            _normalize_match_text(knowledge.get("work_type", "")),
            _normalize_match_text(knowledge.get("year", "")),
            *company_fields,
        ]
        searchable_fields = [
            normalized_name,
            *topic_terms_map.get(anime_id, []),
            *(field for field in structured_fields if field),
        ]
        stats = stats_map.get(anime_id, {"positive": 0, "negative": 0, "neutral": 0, "total": 0})
        total = stats.get("total") or 0
        positive_rate = (stats.get("positive", 0) / total) if total else 0
        if normalized_query and normalized_query == normalized_name:
            title_match_score = 1.6
        elif normalized_name and normalized_name in normalized_query:
            title_match_score = 1.3
        elif normalized_query and normalized_query in normalized_name:
            title_match_score = 1.1
        else:
            title_hits = [term for term in intent_terms if term in normalized_name]
            title_match_score = min(0.8, len(title_hits) * 0.25)

        matched_intent_terms = [
            term for term in intent_terms
            if _matches_term(term, searchable_fields)
        ]
        matched_preference_terms = [
            term for term in preference_terms
            if _matches_term(term, searchable_fields)
        ]
        matched_dislike_terms = [
            term for term in dislike_terms
            if _matches_term(term, searchable_fields)
        ]
        intent_match_score = min(1.4, len(matched_intent_terms) * 0.28)
        preference_bonus = min(0.32, len(matched_preference_terms) * 0.08)
        penalty = min(1.2, len(matched_dislike_terms) * 0.45)
        match_score = title_match_score + intent_match_score + preference_bonus
        sentiment_score = positive_rate * 0.35 if total else 0.05
        popularity_score = min(
            0.15,
            math.log1p(max(0, item.get("comment_count", 0))) / 30,
        )
        score = match_score + sentiment_score + popularity_score - penalty
        matched_terms = list(dict.fromkeys(
            [*matched_intent_terms, *matched_preference_terms]
        ))
        match_tags = [f"匹配：{term}" for term in matched_terms[:3]]
        if positive_rate >= 0.4:
            match_tags.append("口碑")
        if not match_tags:
            match_tags = ["候选", "可探索"]
        ranked.append({
            **item,
            "score": round(score, 4),
            "match_score": round(match_score, 4),
            "title_match_score": round(title_match_score, 4),
            "intent_match_score": round(intent_match_score, 4),
            "preference_bonus": round(preference_bonus, 4),
            "sentiment_score": round(sentiment_score, 4),
            "popularity_score": round(popularity_score, 4),
            "preference_penalty": round(penalty, 4),
            "final_score": round(score, 4),
            "sentiment": stats,
            "topics": [],
            "comments": [],
            "match_tags": match_tags,
            "structured_knowledge": knowledge,
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


# ===== 暴露给 LangGraph ToolNode 的工具 =====

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
    """从注入状态中取 eligible 候选，并拒绝未完成证据检索的 ID。"""
    candidate_items = (
        state.get("eligible_candidates", [])
        if "eligible_candidates" in state
        else state.get("candidates", [])
    )
    candidates = {
        int(candidate["id"]): candidate
        for candidate in candidate_items
    }
    if int(anime_id) not in candidates:
        raise ValueError(f"anime_id {anime_id} is outside the eligible candidate pool")
    return candidates[int(anime_id)]


@tool("inspect_recommendation_candidate")
def inspect_recommendation_candidate_tool(
    anime_id: int,
    state: Annotated[dict, InjectedState],
) -> dict:
    """读取一个候选的已打包统计与证据，不执行写操作。"""
    candidate = _recommend_candidate_from_state(state, anime_id)
    evidence = state.get("evidence_map", {}).get(int(anime_id), [])
    return {
        "anime_id": int(anime_id),
        "name": candidate.get("name", ""),
        "platform": verified_platform_availability(evidence),
        "data_sources": candidate.get("platform", ""),
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
        "evidence": evidence,
    }


@tool("search_candidate_comments")
def search_candidate_comments_tool(
    anime_id: int,
    query: str,
    state: Annotated[dict, InjectedState],
) -> dict:
    """只在当前候选池指定动漫的范围内检索评论证据。"""
    candidate = _recommend_candidate_from_state(state, anime_id)
    return sanitize_search_result(
        search_evidence(query, anime_id=int(anime_id), top_k=3),
        query=query,
        anime_name=str(candidate.get("name") or ""),
        topics=candidate.get("topics", []),
    )


@tool("compare_candidate_sentiment")
def compare_candidate_sentiment_tool(
    anime_ids: list[int],
    state: Annotated[dict, InjectedState],
) -> list[dict]:
    """比较最多五个候选的情感与排序分数，并保持候选池边界。"""
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
