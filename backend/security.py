# -*- coding: utf-8 -*-
"""生成并校验 JWT，同时保持与旧 Flask-JWT-Extended 载荷兼容。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.api.common import ApiError
from backend.config import JWT_ACCESS_TOKEN_EXPIRES, JWT_SECRET_KEY

_bearer = HTTPBearer(auto_error=False)


def create_access_token(identity: str | int) -> str:
    """生成包含旧系统必需字段的 HS256 access token。"""
    now = datetime.now(timezone.utc)
    payload = {
        "fresh": False,
        "iat": now,
        "jti": str(uuid4()),
        "type": "access",
        "sub": str(identity),
        "nbf": now,
        "csrf": str(uuid4()),
        "exp": now + timedelta(seconds=JWT_ACCESS_TOKEN_EXPIRES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> int:
    """作为 FastAPI 依赖解析 Bearer Token，并返回整数用户 ID。"""
    if credentials is None:
        raise ApiError("缺少 Authorization Header", 401)
    if credentials.scheme.lower() != "bearer":
        raise ApiError("Authorization Header 必须使用 Bearer Token", 401)

    # decode 同时完成签名、过期时间和算法白名单校验。
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
        if payload.get("type", "access") != "access":
            raise ApiError("Token 类型无效", 401)
        return int(payload["sub"])
    except ApiError:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise ApiError("Token 已过期", 401) from exc
    except (jwt.InvalidTokenError, TypeError, ValueError) as exc:
        raise ApiError("Token 无效", 401) from exc
