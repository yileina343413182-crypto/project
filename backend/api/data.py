# -*- coding: utf-8 -*-
"""
数据查询 API

端点：
    GET /api/anime/list                  → 所有动漫列表
    GET /api/comments/<anime_id>         → 分页查询评论
"""

from fastapi import APIRouter, Query

from backend.api.common import ok
from backend.database import get_all_anime, get_comments

router = APIRouter()


@router.get("/api/anime/list")
def anime_list():
    """获取所有动漫列表"""
    data = get_all_anime()
    return ok(data)


@router.get("/api/comments/{anime_id}")
def comments(
    anime_id: int,
    sentiment: str | None = Query(default=None),
    page: int = Query(default=1),
    size: int = Query(default=20),
):
    """
    分页查询评论

    查询参数：
        sentiment: 情感标签过滤 (positive/negative/neutral)
        page: 页码，默认1
        size: 每页大小，默认20
    """
    # 参数校验
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20
    if sentiment and sentiment not in ("positive", "negative", "neutral"):
        sentiment = None

    data = get_comments(anime_id, sentiment=sentiment, page=page, page_size=size)
    return ok(data)
