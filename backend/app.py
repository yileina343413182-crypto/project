# -*- coding: utf-8 -*-
"""
FastAPI 应用入口

注册所有 APIRouter，配置 CORS，统一错误处理。

启动方式：
    cd project
    python -m backend.app
"""

import os
import sys
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# 确保项目根目录在搜索路径中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.agent import router as agent_router
from backend.api.auth import router as auth_router
from backend.api.common import ApiError, error_response, ok
from backend.api.data import router as data_router
from backend.api.history import router as history_router
from backend.api.rag import router as rag_router
from backend.api.recommend import router as recommend_router
from backend.api.sentiment import router as sentiment_router
from backend.api.topic import router as topic_router
from backend.database import init_user_tables
from backend.agents.memory import init_agent_tables
from backend.agents.recommend_graph import close_recommendation_checkpointer
from backend.rag.storage import init_rag_tables
from backend.config import CORS_ORIGINS, DEBUG, HOST, PORT
from backend.db.session import dispose_async_engines, dispose_sync_engines

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """初始化业务表，并在 ASGI 进程退出时释放数据库连接池。

    模型与 Celery Worker 独立运行；这里只关闭 API 进程实际创建过的 Checkpointer。
    """
    init_user_tables()
    init_agent_tables()
    init_rag_tables()
    yield
    # 关闭阶段主动释放连接，避免开发期热重载遗留数据库连接。
    close_recommendation_checkpointer()
    await dispose_async_engines()
    dispose_sync_engines()


def create_app() -> FastAPI:
    """创建应用，并集中注册中间件、路由和统一异常格式。"""
    app = FastAPI(title="Anime Sentiment API", lifespan=_lifespan)

    # 通配来源不能与凭据模式同时开启，这是浏览器 CORS 规范的限制。
    origins = ["*"] if CORS_ORIGINS == "*" else CORS_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=CORS_ORIGINS != "*",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由在入口处统一挂载，便于快速看出后端暴露的功能模块。
    app.include_router(sentiment_router)
    app.include_router(topic_router)
    app.include_router(data_router)
    app.include_router(recommend_router)
    app.include_router(auth_router)
    app.include_router(history_router)
    app.include_router(agent_router)
    app.include_router(rag_router)

    # ===== 统一错误处理：所有接口保持 {code, msg, data} 响应契约 =====

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError):
        return error_response(exc.msg, exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        return error_response("请求参数错误", 400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException):
        messages = {404: "接口不存在", 405: "请求方法不允许"}
        return error_response(messages.get(exc.status_code, str(exc.detail)), exc.status_code)

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, exc: Exception):
        logger.exception("服务器内部错误: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "msg": "服务器内部错误", "data": None},
        )

    # ===== 健康检查 =====

    @app.get("/api/health")
    def health():
        return ok({"status": "running"}, msg="ok")

    logger.info("FastAPI应用创建完成，已注册 8 个 APIRouter")
    return app


if __name__ == "__main__":
    import uvicorn

    logger.info("启动FastAPI服务: http://%s:%d", HOST, PORT)
    uvicorn.run("backend.app:create_app", factory=True, host=HOST, port=PORT, reload=DEBUG, workers=1)


