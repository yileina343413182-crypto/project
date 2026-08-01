"""Synchronous and asynchronous SQLAlchemy session boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator, Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import ASYNC_DATABASE_URL, DATABASE_URL

_SYNC_ENGINES: set[Engine] = set()
_ASYNC_ENGINES: set[AsyncEngine] = set()


def sqlite_sync_url(path: str) -> str:
    return f"sqlite+pysqlite:///{Path(path).resolve().as_posix()}"


def sqlite_async_url(path: str) -> str:
    return f"sqlite+aiosqlite:///{Path(path).resolve().as_posix()}"


def to_sync_url(url: str) -> str:
    if url.startswith("mysql+aiomysql:"):
        return url.replace("mysql+aiomysql:", "mysql+pymysql:", 1)
    if url.startswith("sqlite+aiosqlite:"):
        return url.replace("sqlite+aiosqlite:", "sqlite+pysqlite:", 1)
    return url


def to_async_url(url: str) -> str:
    if url.startswith("mysql+pymysql:"):
        return url.replace("mysql+pymysql:", "mysql+aiomysql:", 1)
    if url.startswith("sqlite+pysqlite:"):
        return url.replace("sqlite+pysqlite:", "sqlite+aiosqlite:", 1)
    return url


def _sync_engine_options(url: str) -> dict:
    if url.startswith("sqlite+"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_recycle": 1800}


def _async_engine_options(url: str) -> dict:
    if url.startswith("sqlite+"):
        return {}
    return {"pool_pre_ping": True, "pool_recycle": 1800}


@lru_cache(maxsize=16)
def _sync_engine(url: str) -> Engine:
    engine = create_engine(url, future=True, **_sync_engine_options(url))
    if url.startswith("sqlite+"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    _SYNC_ENGINES.add(engine)
    return engine


@lru_cache(maxsize=16)
def _async_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, future=True, **_async_engine_options(url))
    if url.startswith("sqlite+"):
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    _ASYNC_ENGINES.add(engine)
    return engine


def get_sync_engine(*, db_path: str | None = None, url: str | None = None) -> Engine:
    resolved = sqlite_sync_url(db_path) if db_path else to_sync_url(url or DATABASE_URL)
    return _sync_engine(resolved)


def get_async_engine(*, db_path: str | None = None, url: str | None = None) -> AsyncEngine:
    resolved = sqlite_async_url(db_path) if db_path else to_async_url(url or ASYNC_DATABASE_URL)
    return _async_engine(resolved)


def get_sessionmaker(*, db_path: str | None = None, url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(get_sync_engine(db_path=db_path, url=url), expire_on_commit=False, future=True)


def get_async_sessionmaker(
    *, db_path: str | None = None, url: str | None = None
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_async_engine(db_path=db_path, url=url),
        expire_on_commit=False,
        autoflush=False,
    )


@contextmanager
def session_scope(*, db_path: str | None = None, url: str | None = None) -> Iterator[Session]:
    factory = get_sessionmaker(db_path=db_path, url=url)
    with factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """One AsyncSession boundary per FastAPI request."""
    factory = get_async_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def dispose_sync_engines() -> None:
    for engine in tuple(_SYNC_ENGINES):
        engine.dispose()
    _SYNC_ENGINES.clear()
    _sync_engine.cache_clear()


async def dispose_async_engines() -> None:
    for engine in tuple(_ASYNC_ENGINES):
        await engine.dispose()
    _ASYNC_ENGINES.clear()
    _async_engine.cache_clear()
