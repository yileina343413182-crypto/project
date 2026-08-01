# -*- coding: utf-8 -*-
"""
聊天历史 API（JWT 保护）

POST   /api/history/chat        — 保存聊天消息（问+答一次保存）
GET    /api/history/chat        — 分页获取当前用户历史
DELETE /api/history/chat/<id>   — 删除指定历史条目
"""

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.common import error_response, ok
from backend.db.async_repository import save_chat_exchange, get_chat_history, delete_chat_message
from backend.db.session import get_async_session
from backend.security import get_current_user_id

router = APIRouter(prefix="/api/history")


@router.post("/chat")
async def save_history(
    body: dict | None = Body(default=None),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_session),
):
    """
    保存一次完整的问答记录（用户问 + AI答，合并为一条记录）
    Body: { user_content, ai_content, anime_card(可选) }
    """
    body = body or {}

    user_content = (body.get("user_content") or "").strip()
    ai_content = (body.get("ai_content") or "").strip()
    anime_card = body.get("anime_card")  # dict or None

    if not user_content or not ai_content:
        return error_response("user_content 和 ai_content 不能为空")

    # 一次事务保存问答两条消息，避免只写入一半。
    msg_id = await save_chat_exchange(
        session,
        user_id,
        user_content,
        ai_content,
        anime_card,
    )

    return ok({"msg_id": msg_id}, msg="保存成功")


@router.get("/chat")
async def list_history(
    page: str = Query(default="1"),
    page_size: str = Query(default="20"),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_session),
):
    """获取当前用户的聊天历史（分页）"""
    try:
        page = max(1, int(page))
        page_size = min(50, max(1, int(page_size)))
    except (ValueError, TypeError):
        page, page_size = 1, 20

    result = await get_chat_history(session, user_id, page=page, page_size=page_size)
    return ok(result)


@router.delete("/chat/{msg_id}")
async def delete_history(
    msg_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_session),
):
    """删除指定历史条目（只能删自己的）"""
    affected = await delete_chat_message(session, msg_id, user_id)
    if affected == 0:
        return error_response("记录不存在或无权删除", 404)
    return ok(msg="删除成功")
