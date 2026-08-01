# -*- coding: utf-8 -*-
"""
主题挖掘 API

端点：
    GET /api/topics/<anime_id>    → LDA主题列表及关键词
    GET /api/wordcloud/<anime_id> → 词云数据
"""

from fastapi import APIRouter

from backend.api.common import ok
from backend.database import get_topics, get_wordcloud_data

router = APIRouter()


@router.get("/api/topics/{anime_id}")
def topics(anime_id: int):
    """获取LDA主题列表"""
    data = get_topics(anime_id)
    return ok(data)


@router.get("/api/wordcloud/{anime_id}")
def wordcloud(anime_id: int):
    """获取词云数据"""
    data = get_wordcloud_data(anime_id)
    return ok(data)
