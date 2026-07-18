# -*- coding: utf-8 -*-
"""
数据库操作封装

提供所有CRUD操作函数，供API层调用。
"""

import os
import json
import sqlite3
import logging
from collections import Counter

import jieba

from backend.config import DB_PATH, STOPWORDS_PATH

logger = logging.getLogger(__name__)


def get_db(db_path=None):
    """获取数据库连接"""
    if db_path is None:
        db_path = DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_user_tables(db_path=None):
    """初始化用户相关表（首次启动时自动建表）"""
    conn = get_db(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            anime_card TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


# ===================== 用户账号 =====================

def create_user(username, password_hash):
    """创建新用户，返回用户id"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash)
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return user_id


def get_user_by_username(username):
    """按用户名查询用户，返回 dict 或 None"""
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
        (username,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_user_by_id(user_id):
    """按id查询用户，返回 dict 或 None"""
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


# ===================== 聊天历史 =====================

def save_chat_message(user_id, role, content, anime_card=None):
    """保存一条聊天消息，返回消息id"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_history (user_id, role, content, anime_card) VALUES (?, ?, ?, ?)",
        (user_id, role, content, json.dumps(anime_card, ensure_ascii=False) if anime_card else None)
    )
    conn.commit()
    msg_id = cur.lastrowid
    conn.close()
    return msg_id


def get_chat_history(user_id, page=1, page_size=20):
    """分页获取用户聊天历史（按时间倒序），返回 {items, total, page, page_size}"""
    conn = get_db()
    cur = conn.cursor()

    total = cur.execute(
        "SELECT COUNT(*) FROM chat_history WHERE user_id = ?", (user_id,)
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = cur.execute(
        """SELECT id, role, content, anime_card, created_at
           FROM chat_history
           WHERE user_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT ? OFFSET ?""",
        (user_id, page_size, offset)
    ).fetchall()

    conn.close()

    items = []
    for r in rows:
        card = None
        if r["anime_card"]:
            try:
                card = json.loads(r["anime_card"])
            except Exception:
                pass
        items.append({
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "anime_card": card,
            "created_at": r["created_at"],
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size}


def delete_chat_message(msg_id, user_id):
    """删除聊天消息（只能删除自己的），返回受影响行数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM chat_history WHERE id = ? AND user_id = ?",
        (msg_id, user_id)
    )
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected


def _load_stopwords():
    """加载停用词"""
    stopwords = set()
    try:
        with open(STOPWORDS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if w:
                    stopwords.add(w)
    except FileNotFoundError:
        pass
    return stopwords


# ===================== 动漫列表 =====================

def get_all_anime():
    """
    获取所有动漫列表

    Returns:
        list[dict]: [{id, name, platform, comment_count}, ...]
    """
    conn = get_db()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT a.id, a.name, a.platform,
               COUNT(c.id) as comment_count
        FROM anime a
        LEFT JOIN comments c ON a.id = c.anime_id
        GROUP BY a.id
        ORDER BY a.id
    """).fetchall()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "name": r["name"],
            "platform": r["platform"],
            "comment_count": r["comment_count"]
        })

    conn.close()
    return result


# ===================== 评论查询 =====================

def get_comments(anime_id, sentiment=None, page=1, page_size=20):
    """
    分页查询评论

    Args:
        anime_id: 动漫ID
        sentiment: 情感标签过滤 (positive/negative/neutral/None)
        page: 页码（从1开始）
        page_size: 每页大小

    Returns:
        dict: {total, page, page_size, items: [{id, content, sentiment_label, likes, publish_time}, ...]}
    """
    conn = get_db()
    cur = conn.cursor()

    # 构建查询
    where = "WHERE anime_id = ?"
    params = [anime_id]

    if sentiment:
        where += " AND sentiment_label = ?"
        params.append(sentiment)

    # 总数
    total = cur.execute(
        "SELECT COUNT(*) FROM comments " + where, params
    ).fetchone()[0]

    # 分页
    offset = (page - 1) * page_size
    rows = cur.execute(
        "SELECT id, content, sentiment_label, sentiment_score, likes, publish_time, platform "
        "FROM comments " + where + " ORDER BY id ASC LIMIT ? OFFSET ?",
        params + [page_size, offset]
    ).fetchall()

    items = []
    for i, r in enumerate(rows):
        items.append({
            "seq": offset + i + 1,
            "id": r["id"],
            "content": r["content"],
            "sentiment_label": r["sentiment_label"],
            "sentiment_score": r["sentiment_score"],
            "likes": r["likes"],
            "publish_time": r["publish_time"],
            "platform": r["platform"]
        })

    conn.close()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items
    }


# ===================== 情感统计 =====================

def get_sentiment_stats(anime_id):
    """
    获取情感统计（饼图数据）

    Returns:
        dict: {positive: 数量, negative: 数量, neutral: 数量, total: 总数}
    """
    conn = get_db()
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT sentiment_label, COUNT(*) as cnt "
        "FROM comments WHERE anime_id = ? AND sentiment_label IS NOT NULL "
        "GROUP BY sentiment_label",
        (anime_id,)
    ).fetchall()

    stats = {"positive": 0, "negative": 0, "neutral": 0}
    for r in rows:
        label = r["sentiment_label"]
        if label in stats:
            stats[label] = r["cnt"]

    stats["total"] = sum(stats.values())
    conn.close()
    return stats


def get_sentiment_scatter(anime_id, limit=600):
    """
    按评论序号返回逐条情感值，用于折线散点图。

    情感值映射:
        positive → +sentiment_score * 0.5  (0 ~ +0.5)
        negative → -sentiment_score * 0.5  (−0.5 ~ 0)
        neutral  → 0.0

    Returns:
        list[dict]: [{index, value, label}, ...]  共最多 limit 条
    """
    conn = get_db()
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT sentiment_label, sentiment_score "
        "FROM comments "
        "WHERE anime_id = ? AND sentiment_label IS NOT NULL "
        "ORDER BY id "
        "LIMIT ?",
        (anime_id, limit)
    ).fetchall()

    result = []
    for i, r in enumerate(rows):
        label = r["sentiment_label"]
        score = r["sentiment_score"] or 0.5
        if label == "positive":
            value = round(score * 0.5, 4)
        elif label == "negative":
            value = round(-score * 0.5, 4)
        else:
            # 中性：置信度越高越靠近 0，置信度低时在 ±0.15 范围内小幅波动
            # (score - 0.5) * 0.3 将 [0,1] 映射到 [-0.15, +0.15]
            value = round((score - 0.5) * 0.3, 4)
        result.append({"index": i, "value": value, "label": label})

    conn.close()
    return result


def get_sentiment_trend(anime_id):
    """
    按天聚合返回情感趋势数据（折线图数据）

    Returns:
        list[dict]: [{date, positive, negative, neutral}, ...]
    """
    conn = get_db()
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT DATE(publish_time) as date, sentiment_label, COUNT(*) as cnt "
        "FROM comments "
        "WHERE anime_id = ? AND publish_time IS NOT NULL AND sentiment_label IS NOT NULL "
        "GROUP BY DATE(publish_time), sentiment_label "
        "ORDER BY date",
        (anime_id,)
    ).fetchall()

    # 按日期聚合
    trend_map = {}
    for r in rows:
        date = r["date"]
        if not date:
            continue
        if date not in trend_map:
            trend_map[date] = {"date": date, "positive": 0, "negative": 0, "neutral": 0}
        label = r["sentiment_label"]
        if label in ("positive", "negative", "neutral"):
            trend_map[date][label] = r["cnt"]

    conn.close()
    return sorted(trend_map.values(), key=lambda x: x["date"])


# ===================== 主题 =====================

def get_topics(anime_id):
    """
    获取主题和关键词

    Returns:
        list[dict]: [{topic_id, keywords: [{word, weight}, ...], weight}, ...]
    """
    conn = get_db()
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT topic_id, keywords, weight FROM topics "
        "WHERE anime_id = ? ORDER BY topic_id",
        (anime_id,)
    ).fetchall()

    result = []
    for r in rows:
        keywords = json.loads(r["keywords"])
        result.append({
            "topic_id": r["topic_id"],
            "keywords": keywords,
            "weight": r["weight"]
        })

    conn.close()
    return result


# ===================== 词云数据 =====================

def get_wordcloud_data(anime_id, top_n=100):
    """
    获取词频数据（词云用）

    Returns:
        list[dict]: [{word, count}, ...]
    """
    conn = get_db()
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT content FROM comments WHERE anime_id = ?",
        (anime_id,)
    ).fetchall()

    conn.close()

    if not rows:
        return []

    stopwords = _load_stopwords()
    counter = Counter()

    for r in rows:
        text = r["content"]
        if not text:
            continue
        words = jieba.lcut(str(text))
        for w in words:
            w = w.strip()
            if w and len(w) > 1 and w not in stopwords and not w.isdigit():
                counter[w] += 1

    return [{"word": w, "count": c} for w, c in counter.most_common(top_n)]


# ===================== 三维情感分析（推荐模块用）=====================

def get_aspect_sentiment(anime_id):
    """
    对评论按「作画」「剧情」「声优」三个维度进行情感统计。
    通过关键词过滤找出相关评论，再统计情感分布。

    Returns:
        dict: {
            "作画": {"positive": int, "neutral": int, "negative": int, "total": int},
            "剧情": {"positive": int, "neutral": int, "negative": int, "total": int},
            "声优": {"positive": int, "neutral": int, "negative": int, "total": int}
        }
    """
    ASPECTS = {
        "作画": ["作画", "画面", "画质", "画风", "美术", "特效", "CG"],
        "剧情": ["剧情", "故事", "情节", "剧本", "结局", "设定", "逻辑", "伏笔"],
        "声优": ["声优", "配音", "CV", "声线", "日配", "中配"],
    }

    conn = get_db()
    cur = conn.cursor()

    result = {}
    for aspect, keywords in ASPECTS.items():
        like_clauses = " OR ".join(["content LIKE ?" for _ in keywords])
        like_params = [f"%{kw}%" for kw in keywords]

        rows = cur.execute(
            f"SELECT sentiment_label, COUNT(*) as cnt "
            f"FROM comments "
            f"WHERE anime_id = ? AND sentiment_label IS NOT NULL "
            f"AND ({like_clauses}) "
            f"GROUP BY sentiment_label",
            [anime_id] + like_params
        ).fetchall()

        stats = {"positive": 0, "neutral": 0, "negative": 0}
        for r in rows:
            label = r["sentiment_label"]
            if label in stats:
                stats[label] = r["cnt"]
        stats["total"] = sum(stats.values())
        result[aspect] = stats

    conn.close()
    return result
