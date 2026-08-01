# -*- coding: utf-8 -*-
"""
主题挖掘 API

端点：
    GET /api/topics/<anime_id>    → LDA主题列表及关键词
    GET /api/wordcloud/<anime_id> → 词云数据
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.api.common import ok
from backend.database import _wordcloud_from_contents
from backend.db.async_repository import get_topics, get_wordcloud_contents
from backend.db.session import get_async_session

router = APIRouter()


@router.get("/api/topics/{anime_id}")
async def topics(anime_id: int, session: AsyncSession = Depends(get_async_session)):
    """获取LDA主题列表"""
    data = await get_topics(session, anime_id)
    return ok(data)


@router.get("/api/wordcloud/{anime_id}")
async def wordcloud(anime_id: int, session: AsyncSession = Depends(get_async_session)):
    """获取词云数据"""
    contents = await get_wordcloud_contents(session, anime_id)
    data = await run_in_threadpool(_wordcloud_from_contents, contents, 100)
    return ok(data)
