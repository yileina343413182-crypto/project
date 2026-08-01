# -*- coding: utf-8 -*-
"""
用户认证 API

POST /api/auth/register  — 注册
POST /api/auth/login     — 登录
GET  /api/auth/me        — 获取当前用户信息（JWT保护）
"""

import re
import bcrypt
from fastapi import APIRouter, Body, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.api.common import error_response, ok
from backend.db.async_repository import create_user, get_user_by_username, get_user_by_id
from backend.db.session import get_async_session
from backend.security import create_access_token, get_current_user_id

router = APIRouter(prefix="/api/auth")

# 用户名规则：3-20位，字母/数字/下划线
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}$")


@router.post("/register")
async def register(
    body: dict | None = Body(default=None),
    session: AsyncSession = Depends(get_async_session),
):
    """注册新用户"""
    body = body or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    # 基本校验
    if not username or not password:
        return error_response("用户名和密码不能为空")
    if not _USERNAME_RE.match(username):
        return error_response("用户名需为3-20位（字母、数字、下划线或中文）")
    if len(password) < 6:
        return error_response("密码至少6位")
    if len(password) > 72:
        return error_response("密码不能超过72位")

    # 检查用户名是否已存在
    if await get_user_by_username(session, username):
        return error_response("用户名已存在，请换一个")

    # bcrypt 哈希密码
    pwd_hash = await run_in_threadpool(
        bcrypt.hashpw,
        password.encode("utf-8"),
        bcrypt.gensalt(),
    )
    pwd_hash = pwd_hash.decode("utf-8")

    try:
        user_id = await create_user(session, username, pwd_hash)
    except IntegrityError:
        await session.rollback()
        return error_response("用户名已存在，请换一个")
    token = create_access_token(identity=str(user_id))

    return ok({"token": token, "username": username, "user_id": user_id}, msg="注册成功")


@router.post("/login")
async def login(
    body: dict | None = Body(default=None),
    session: AsyncSession = Depends(get_async_session),
):
    """用户登录"""
    body = body or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return error_response("用户名和密码不能为空")

    user = await get_user_by_username(session, username)
    if not user:
        return error_response("用户名或密码错误", 401)

    # 验证密码
    valid = await run_in_threadpool(
        bcrypt.checkpw,
        password.encode("utf-8"),
        user["password_hash"].encode("utf-8"),
    )
    if not valid:
        return error_response("用户名或密码错误", 401)

    token = create_access_token(identity=str(user["id"]))

    return ok({
        "token": token,
        "username": user["username"],
        "user_id": user["id"]
    }, msg="登录成功")


@router.get("/me")
async def me(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_session),
):
    """获取当前登录用户信息（验证token有效性）"""
    user = await get_user_by_id(session, user_id)
    if not user:
        return error_response("用户不存在", 404)
    return ok({"user_id": user["id"], "username": user["username"], "created_at": user["created_at"]})
