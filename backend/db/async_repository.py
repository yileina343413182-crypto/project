"""FastAPI 请求层专用的异步 CRUD。

函数接收请求作用域内的 ``AsyncSession``，不自行提交事务；提交、回滚与
关闭统一由 ``get_async_session`` 依赖处理。
"""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Anime, ChatHistory, Comment, Topic, User, WatchGuide


def _date_value(value):
    """把数据库日期转换为可 JSON 序列化的字符串。"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _json_value(value, default=None):
    """兼容 JSON 列对象与旧库中的 JSON 字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


# ===== 用户与聊天历史 =====

async def create_user(session: AsyncSession, username: str, password_hash: str) -> int:
    """新增用户并 flush，以便在提交前取得自增 ID。"""
    user = User(username=username, password_hash=password_hash)
    session.add(user)
    await session.flush()
    return user.id


async def get_user_by_username(session: AsyncSession, username: str) -> dict | None:
    user = await session.scalar(select(User).where(User.username == username))
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "password_hash": user.password_hash,
        "created_at": _date_value(user.created_at),
    }


async def get_user_by_id(session: AsyncSession, user_id: int) -> dict | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    return {"id": user.id, "username": user.username, "created_at": _date_value(user.created_at)}


async def save_chat_exchange(
    session: AsyncSession,
    user_id: int,
    user_content: str,
    ai_content: str,
    anime_card=None,
) -> int:
    session.add(ChatHistory(user_id=user_id, role="user", content=user_content))
    answer = ChatHistory(
        user_id=user_id,
        role="ai",
        content=ai_content,
        anime_card=anime_card,
    )
    session.add(answer)
    await session.flush()
    return answer.id


async def get_chat_history(
    session: AsyncSession,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """按时间倒序分页返回当前用户的聊天记录。"""
    offset = (page - 1) * page_size
    total = await session.scalar(
        select(func.count()).select_from(ChatHistory).where(ChatHistory.user_id == user_id)
    ) or 0
    rows = (
        await session.scalars(
            select(ChatHistory)
            .where(ChatHistory.user_id == user_id)
            .order_by(ChatHistory.created_at.desc(), ChatHistory.id.desc())
            .limit(page_size)
            .offset(offset)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "anime_card": _json_value(row.anime_card),
                "created_at": _date_value(row.created_at),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def delete_chat_message(session: AsyncSession, msg_id: int, user_id: int) -> int:
    result = await session.execute(
        delete(ChatHistory).where(ChatHistory.id == msg_id, ChatHistory.user_id == user_id)
    )
    return result.rowcount


# ===== 待看番剧指南 =====

def _watch_guide_dict(record: WatchGuide, *, include_content: bool = False) -> dict:
    result = {
        "id": record.id,
        "anime_name": record.anime_name,
        "created_at": _date_value(record.created_at),
        "source_session_id": record.source_session_id,
    }
    if include_content:
        result["guide_content"] = record.guide_content
    return result


async def list_watch_guides(
    session: AsyncSession,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """按创建时间倒序分页返回当前用户的待看指南摘要。"""
    page = max(1, page)
    page_size = min(50, max(1, page_size))
    offset = (page - 1) * page_size
    total = await session.scalar(
        select(func.count()).select_from(WatchGuide).where(WatchGuide.user_id == user_id)
    ) or 0
    rows = (
        await session.scalars(
            select(WatchGuide)
            .where(WatchGuide.user_id == user_id)
            .order_by(WatchGuide.created_at.desc(), WatchGuide.id.desc())
            .limit(page_size)
            .offset(offset)
        )
    ).all()
    return {
        "items": [_watch_guide_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_watch_guide(
    session: AsyncSession,
    guide_id: int,
    user_id: int,
) -> dict | None:
    """读取属于当前用户的单份指南；越权与不存在均返回 ``None``。"""
    record = await session.scalar(
        select(WatchGuide).where(
            WatchGuide.id == guide_id,
            WatchGuide.user_id == user_id,
        )
    )
    return _watch_guide_dict(record, include_content=True) if record is not None else None


async def delete_watch_guide(session: AsyncSession, guide_id: int, user_id: int) -> int:
    """只删除属于当前用户的指定指南，并返回受影响行数。"""
    result = await session.execute(
        delete(WatchGuide).where(
            WatchGuide.id == guide_id,
            WatchGuide.user_id == user_id,
        )
    )
    return result.rowcount


# ===== 动漫、评论与分析结果查询 =====

async def get_all_anime(session: AsyncSession) -> list[dict]:
    """查询动漫列表，并附带各动漫的评论总数。"""
    rows = (
        await session.execute(
            select(
                Anime.id,
                Anime.name,
                Anime.platform,
                func.count(Comment.id).label("comment_count"),
            )
            .outerjoin(Comment, Anime.id == Comment.anime_id)
            .group_by(Anime.id, Anime.name, Anime.platform)
            .order_by(Anime.id)
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def get_comments(
    session: AsyncSession,
    anime_id: int,
    sentiment=None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """按动漫和可选情感标签分页查询评论。"""
    filters = [Comment.anime_id == anime_id]
    if sentiment:
        filters.append(Comment.sentiment_label == sentiment)
    offset = (page - 1) * page_size
    total = await session.scalar(
        select(func.count()).select_from(Comment).where(*filters)
    ) or 0
    rows = (
        await session.execute(
            select(
                Comment.id,
                Comment.content,
                Comment.sentiment_label,
                Comment.sentiment_score,
                Comment.likes,
                Comment.publish_time,
                Comment.platform,
            )
            .where(*filters)
            .order_by(Comment.id)
            .limit(page_size)
            .offset(offset)
        )
    ).mappings().all()
    items = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["seq"] = offset + index + 1
        item["publish_time"] = _date_value(item["publish_time"])
        items.append(item)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


async def get_sentiment_stats(session: AsyncSession, anime_id: int) -> dict:
    """汇总正向、中性、负向评论数量及总数。"""
    rows = (
        await session.execute(
            select(Comment.sentiment_label, func.count().label("cnt"))
            .where(Comment.anime_id == anime_id, Comment.sentiment_label.is_not(None))
            .group_by(Comment.sentiment_label)
        )
    ).all()
    stats = {"positive": 0, "negative": 0, "neutral": 0}
    for label, count in rows:
        if label in stats:
            stats[label] = count
    stats["total"] = sum(stats.values())
    return stats


async def get_sentiment_scatter(session: AsyncSession, anime_id: int, limit: int = 600) -> list[dict]:
    """把模型置信度映射到前端散点图使用的正负坐标。"""
    rows = (
        await session.execute(
            select(Comment.sentiment_label, Comment.sentiment_score)
            .where(Comment.anime_id == anime_id, Comment.sentiment_label.is_not(None))
            .order_by(Comment.id)
            .limit(limit)
        )
    ).all()
    result = []
    for index, (label, raw_score) in enumerate(rows):
        score = raw_score or 0.5
        value = (
            round(score * 0.5, 4)
            if label == "positive"
            else round(-score * 0.5, 4)
            if label == "negative"
            else round((score - 0.5) * 0.3, 4)
        )
        result.append({"index": index, "value": value, "label": label})
    return result


async def get_sentiment_trend(session: AsyncSession, anime_id: int) -> list[dict]:
    """按发布日期聚合每天的三类情感数量。"""
    day = func.date(Comment.publish_time).label("date")
    rows = (
        await session.execute(
            select(day, Comment.sentiment_label, func.count().label("cnt"))
            .where(
                Comment.anime_id == anime_id,
                Comment.publish_time.is_not(None),
                Comment.sentiment_label.is_not(None),
            )
            .group_by(day, Comment.sentiment_label)
            .order_by(day)
        )
    ).all()
    trend_map = {}
    for row_day, label, count in rows:
        row_day = _date_value(row_day)
        if not row_day:
            continue
        trend_map.setdefault(
            row_day,
            {"date": row_day, "positive": 0, "negative": 0, "neutral": 0},
        )
        if label in ("positive", "negative", "neutral"):
            trend_map[row_day][label] = count
    return sorted(trend_map.values(), key=lambda item: item["date"])


async def get_topics(session: AsyncSession, anime_id: int) -> list[dict]:
    """返回指定动漫的 LDA 主题及已解析关键词。"""
    rows = (
        await session.execute(
            select(Topic.topic_id, Topic.keywords, Topic.weight)
            .where(Topic.anime_id == anime_id)
            .order_by(Topic.topic_id)
        )
    ).all()
    return [
        {"topic_id": topic_id, "keywords": _json_value(keywords, []), "weight": weight}
        for topic_id, keywords, weight in rows
    ]


async def get_wordcloud_contents(session: AsyncSession, anime_id: int) -> list[str]:
    """只读取词云计算所需的评论正文，分词在同步线程池中完成。"""
    return list(
        (
            await session.scalars(select(Comment.content).where(Comment.anime_id == anime_id))
        ).all()
    )


async def get_aspect_sentiment(session: AsyncSession, anime_id: int) -> dict:
    """按方面关键词分别聚合三类情感数量。"""
    aspects = {
        "作画": ["作画", "画面", "画质", "画风", "美术", "特效", "CG"],
        "剧情": ["剧情", "故事", "情节", "剧本", "结局", "设定", "逻辑", "伏笔"],
        "声优": ["声优", "配音", "CV", "声线", "日配", "中配"],
    }
    result = {}
    for aspect, keywords in aspects.items():
        rows = (
            await session.execute(
                select(Comment.sentiment_label, func.count().label("cnt"))
                .where(
                    Comment.anime_id == anime_id,
                    Comment.sentiment_label.is_not(None),
                    or_(*(Comment.content.like(f"%{keyword}%") for keyword in keywords)),
                )
                .group_by(Comment.sentiment_label)
            )
        ).all()
        stats = {"positive": 0, "neutral": 0, "negative": 0}
        for label, count in rows:
            if label in stats:
                stats[label] = count
        stats["total"] = sum(stats.values())
        result[aspect] = stats
    return result
