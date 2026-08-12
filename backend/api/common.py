# -*- coding: utf-8 -*-
"""定义 API 层统一响应结构，以及可安全返回给客户端的业务异常。"""

from fastapi.responses import JSONResponse


class ApiError(Exception):
    """需要按既有 API 契约返回给客户端的错误。"""

    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.msg = msg
        self.code = code


def ok(data=None, msg: str = "success") -> dict:
    """包装成功响应，保持前端约定的 code/msg/data 结构。"""
    return {"code": 200, "msg": msg, "data": data}


def error_response(msg: str, code: int = 400) -> JSONResponse:
    """同时设置 HTTP 状态码和响应体业务码。"""
    return JSONResponse(
        status_code=code,
        content={"code": code, "msg": msg, "data": None},
    )
