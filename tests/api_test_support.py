# -*- coding: utf-8 -*-
"""API 测试的隔离数据库与 FastAPI TestClient 生命周期。"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_DB = _PROJECT_ROOT / "data" / "anime_sentiment.db"

_db_file = tempfile.NamedTemporaryFile(prefix="anime_api_test_", suffix=".db", delete=False)
_db_file.close()
if _SOURCE_DB.exists():
    shutil.copyfile(_SOURCE_DB, _db_file.name)
else:
    from crawler.cleaner import init_database

    _session = init_database(_db_file.name)
    _session.close()

_checkpoint_file = tempfile.NamedTemporaryFile(
    prefix="anime_checkpoint_test_",
    suffix=".db",
    delete=False,
)
_checkpoint_file.close()

_sync_url = f"sqlite+pysqlite:///{Path(_db_file.name).resolve().as_posix()}"
_async_url = f"sqlite+aiosqlite:///{Path(_db_file.name).resolve().as_posix()}"
os.environ["DATABASE_PATH"] = _db_file.name
os.environ["DATABASE_URL"] = _sync_url
os.environ["ASYNC_DATABASE_URL"] = _async_url
os.environ["RECOMMEND_CHECKPOINT_DB"] = _checkpoint_file.name
for _name in (
    "LLM_API_KEY",
    "EMBEDDING_API_KEY",
    "RERANK_API_KEY",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "OPENAI_API_KEY",
    "ZHIPU_API_KEY",
):
    os.environ[_name] = ""

# Tests may be collected after another module has already imported backend.config.
# Refresh the cached configuration and session-module aliases so import order can
# never redirect an isolated test to the project databases.
import backend.config as _config
import backend.db.session as _db_session

_config.DB_PATH = _db_file.name
_config.DATABASE_URL = _sync_url
_config.ASYNC_DATABASE_URL = _async_url
_config.DATABASE_IS_MYSQL = False
_config.RECOMMEND_CHECKPOINT_DB = _checkpoint_file.name
_config.RERANK_API_KEY = ""
_db_session.DATABASE_URL = _sync_url
_db_session.ASYNC_DATABASE_URL = _async_url
if "backend.agents.recommend_graph" in sys.modules:
    _recommend_graph = sys.modules["backend.agents.recommend_graph"]
    _recommend_graph.RECOMMEND_CHECKPOINT_DB = _checkpoint_file.name
    _recommend_graph.build_recommendation_graph.cache_clear()
    _recommend_graph._get_recommendation_checkpointer.cache_clear()


def _cleanup_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _close_checkpoint_connection() -> None:
    try:
        from backend.agents import recommend_graph

        if recommend_graph._get_recommendation_checkpointer.cache_info().currsize:
            checkpointer = recommend_graph._get_recommendation_checkpointer()
            connection = getattr(checkpointer, "conn", None)
            if connection is not None:
                connection.close()
        recommend_graph.build_recommendation_graph.cache_clear()
        recommend_graph._get_recommendation_checkpointer.cache_clear()
    except (AttributeError, ImportError):
        pass


atexit.register(_cleanup_file, _checkpoint_file.name)
atexit.register(_cleanup_file, _db_file.name)
atexit.register(_close_checkpoint_connection)


def open_test_client():
    """返回 (context_manager, client)，由测试类在结束时显式关闭。"""
    from fastapi.testclient import TestClient

    from backend.app import create_app

    context = TestClient(create_app())
    return context, context.__enter__()
