# -*- coding: utf-8 -*-
"""FastAPI 适配层的统一响应与业务异常。"""

from fastapi.responses import JSONResponse


class ApiError(Exception):
    """需要按既有 API 契约返回给客户端的错误。"""

    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.msg = msg
        self.code = code


def ok(data=None, msg: str = "success") -> dict:
    return {"code": 200, "msg": msg, "data": data}


def error_response(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"code": code, "msg": msg, "data": None},
    )
