# -*- coding: utf-8 -*-
"""Shared SQLAlchemy CRUD used by synchronous Agent and offline paths."""

from __future__ import annotations

import json
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime

import jieba
from sqlalchemy import delete, func, or_, select

from backend.config import STOPWORDS_PATH
from backend.db.models import Anime, ChatHistory, Comment, Topic, User
from backend.db.session import get_sync_engine, session_scope


def _date_value(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _json_value(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


@contextmanager
def orm_session(db_path: str | None = None):
    """Open one synchronous Session for one thread/transaction."""
    with session_scope(db_path=db_path) as session:
        yield session


def init_user_tables(db_path=None):
    engine = get_sync_engine(db_path=db_path) if db_path else get_sync_engine()
    User.metadata.create_all(
        engine,
        tables=[User.__table__, ChatHistory.__table__],
        checkfirst=True,
    )


def create_user(username, password_hash):
    with orm_session() as session:
        user = User(username=username, password_hash=password_hash)
        session.add(user)
        session.flush()
        return user.id


def get_user_by_username(username):
    with orm_session() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            return None
        return {
            "id": user.id,
            "username": user.username,
            "password_hash": user.password_hash,
            "created_at": _date_value(user.created_at),
        }


def get_user_by_id(user_id):
    with orm_session() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        return {
            "id": user.id,
            "username": user.username,
            "created_at": _date_value(user.created_at),
        }


def save_chat_message(user_id, role, content, anime_card=None):
    with orm_session() as session:
        message = ChatHistory(
            user_id=user_id,
            role=role,
            content=content,
            anime_card=anime_card,
        )
        session.add(message)
        session.flush()
        return message.id


def save_chat_exchange(user_id, user_content, ai_content, anime_card=None):
    """Save both sides of one exchange atomically and return the AI row id."""
    with orm_session() as session:
        session.add(ChatHistory(user_id=user_id, role="user", content=user_content))
        answer = ChatHistory(
            user_id=user_id,
            role="ai",
            content=ai_content,
            anime_card=anime_card,
        )
        session.add(answer)
        session.flush()
        return answer.id


def get_chat_history(user_id, page=1, page_size=20):
    offset = (page - 1) * page_size
    with orm_session() as session:
        total = session.scalar(
            select(func.count()).select_from(ChatHistory).where(ChatHistory.user_id == user_id)
        ) or 0
        rows = session.scalars(
            select(ChatHistory)
            .where(ChatHistory.user_id == user_id)
            .order_by(ChatHistory.created_at.desc(), ChatHistory.id.desc())
            .limit(page_size)
            .offset(offset)
        ).all()
        items = [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "anime_card": _json_value(row.anime_card),
                "created_at": _date_value(row.created_at),
            }
            for row in rows
        ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def delete_chat_message(msg_id, user_id):
    with orm_session() as session:
        result = session.execute(
            delete(ChatHistory).where(
                ChatHistory.id == msg_id,
                ChatHistory.user_id == user_id,
            )
        )
        return result.rowcount


def _load_stopwords():
    stopwords = set()
    try:
        with open(STOPWORDS_PATH, "r", encoding="utf-8") as file:
            for line in file:
                word = line.strip()
                if word:
                    stopwords.add(word)
    except FileNotFoundError:
        pass
    return stopwords


def get_all_anime():
    statement = (
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
    with orm_session() as session:
        return [dict(row) for row in session.execute(statement).mappings().all()]


def get_comments(anime_id, sentiment=None, page=1, page_size=20):
    filters = [Comment.anime_id == anime_id]
    if sentiment:
        filters.append(Comment.sentiment_label == sentiment)
    offset = (page - 1) * page_size
    with orm_session() as session:
        total = session.scalar(
            select(func.count()).select_from(Comment).where(*filters)
        ) or 0
        rows = session.execute(
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
        ).mappings().all()
    items = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["seq"] = offset + index + 1
        item["publish_time"] = _date_value(item["publish_time"])
        items.append(item)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def get_sentiment_stats(anime_id):
    with orm_session() as session:
        rows = session.execute(
            select(Comment.sentiment_label, func.count().label("cnt"))
            .where(Comment.anime_id == anime_id, Comment.sentiment_label.is_not(None))
            .group_by(Comment.sentiment_label)
        ).all()
    stats = {"positive": 0, "negative": 0, "neutral": 0}
    for label, count in rows:
        if label in stats:
            stats[label] = count
    stats["total"] = sum(stats.values())
    return stats


def get_sentiment_scatter(anime_id, limit=600):
    with orm_session() as session:
        rows = session.execute(
            select(Comment.sentiment_label, Comment.sentiment_score)
            .where(Comment.anime_id == anime_id, Comment.sentiment_label.is_not(None))
            .order_by(Comment.id)
            .limit(limit)
        ).all()
    result = []
    for index, (label, raw_score) in enumerate(rows):
        score = raw_score or 0.5
        if label == "positive":
            value = round(score * 0.5, 4)
        elif label == "negative":
            value = round(-score * 0.5, 4)
        else:
            value = round((score - 0.5) * 0.3, 4)
        result.append({"index": index, "value": value, "label": label})
    return result


def get_sentiment_trend(anime_id):
    day = func.date(Comment.publish_time).label("date")
    with orm_session() as session:
        rows = session.execute(
            select(day, Comment.sentiment_label, func.count().label("cnt"))
            .where(
                Comment.anime_id == anime_id,
                Comment.publish_time.is_not(None),
                Comment.sentiment_label.is_not(None),
            )
            .group_by(day, Comment.sentiment_label)
            .order_by(day)
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


def get_topics(anime_id):
    with orm_session() as session:
        rows = session.execute(
            select(Topic.topic_id, Topic.keywords, Topic.weight)
            .where(Topic.anime_id == anime_id)
            .order_by(Topic.topic_id)
        ).all()
    return [
        {
            "topic_id": topic_id,
            "keywords": _json_value(keywords, []),
            "weight": weight,
        }
        for topic_id, keywords, weight in rows
    ]


def _wordcloud_from_contents(contents, top_n=100):
    stopwords = _load_stopwords()
    counter = Counter()
    for content in contents:
        if not content:
            continue
        for word in jieba.lcut(str(content)):
            word = word.strip()
            if word and len(word) > 1 and word not in stopwords and not word.isdigit():
                counter[word] += 1
    return [{"word": word, "count": count} for word, count in counter.most_common(top_n)]


def get_wordcloud_data(anime_id, top_n=100):
    with orm_session() as session:
        contents = session.scalars(
            select(Comment.content).where(Comment.anime_id == anime_id)
        ).all()
    return _wordcloud_from_contents(contents, top_n)


def get_aspect_sentiment(anime_id):
    aspects = {
        "作画": ["作画", "画面", "画质", "画风", "美术", "特效", "CG"],
        "剧情": ["剧情", "故事", "情节", "剧本", "结局", "设定", "逻辑", "伏笔"],
        "声优": ["声优", "配音", "CV", "声线", "日配", "中配"],
    }
    result = {}
    with orm_session() as session:
        for aspect, keywords in aspects.items():
            rows = session.execute(
                select(Comment.sentiment_label, func.count().label("cnt"))
                .where(
                    Comment.anime_id == anime_id,
                    Comment.sentiment_label.is_not(None),
                    or_(*(Comment.content.like(f"%{keyword}%") for keyword in keywords)),
                )
                .group_by(Comment.sentiment_label)
            ).all()
            stats = {"positive": 0, "neutral": 0, "negative": 0}
            for label, count in rows:
                if label in stats:
                    stats[label] = count
            stats["total"] = sum(stats.values())
            result[aspect] = stats
    return result
