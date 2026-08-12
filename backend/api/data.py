# -*- coding: utf-8 -*-
"""
基础数据查询 API

端点：
    GET /api/anime/list                  → 所有动漫列表
    GET /api/comments/<anime_id>         → 分页查询评论
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.common import ok
from backend.db.async_repository import get_all_anime, get_comments
from backend.db.session import get_async_session

router = APIRouter()


@router.get("/api/anime/list")
async def anime_list(session: AsyncSession = Depends(get_async_session)):
    """获取所有动漫及其评论数量。"""
    data = await get_all_anime(session)
    return ok(data)


@router.get("/api/comments/{anime_id}")
async def comments(
    anime_id: int,
    sentiment: str | None = Query(default=None),
    page: int = Query(default=1),
    size: int = Query(default=20),
    session: AsyncSession = Depends(get_async_session),
):
    """
    分页获取指定动漫评论，可按情感标签过滤。

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

    data = await get_comments(session, anime_id, sentiment=sentiment, page=page, page_size=size)
    return ok(data)
