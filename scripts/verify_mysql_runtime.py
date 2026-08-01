"""Transactional sync/async MySQL runtime probe with no persistent test rows."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import DATABASE_IS_MYSQL, RECOMMEND_CHECKPOINT_DB
from backend.db.models import Anime, RagCollectionMetadata
from backend.db.session import (
    dispose_async_engines,
    dispose_sync_engines,
    get_async_engine,
    get_sync_engine,
)


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_statement(collection_name: str, document_count: int):
    statement = mysql_insert(RagCollectionMetadata).values(
        collection_name=collection_name,
        embedding_provider="runtime-probe",
        embedding_model="runtime-probe",
        embedding_dimension=1,
        document_count=document_count,
    )
    return statement.on_duplicate_key_update(
        document_count=statement.inserted.document_count
    )


def _run_sync_probe(collection_name: str) -> int:
    engine = get_sync_engine()
    if engine.dialect.name != "mysql":
        raise RuntimeError("同步数据库连接不是 MySQL")

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            anime_count = connection.scalar(select(func.count()).select_from(Anime))
            connection.execute(_probe_statement(collection_name, 1))
            connection.execute(_probe_statement(collection_name, 2))
            observed = connection.scalar(
                select(RagCollectionMetadata.document_count).where(
                    RagCollectionMetadata.collection_name == collection_name
                )
            )
            if observed != 2:
                raise RuntimeError(f"同步事务写后读失败: {observed!r}")
        finally:
            transaction.rollback()

    with engine.connect() as connection:
        persisted = connection.scalar(
            select(func.count())
            .select_from(RagCollectionMetadata)
            .where(RagCollectionMetadata.collection_name == collection_name)
        )
    if persisted:
        raise RuntimeError("同步事务回滚后仍存在探测数据")
    return int(anime_count or 0)


async def _run_async_probe(collection_name: str) -> int:
    engine = get_async_engine()
    if engine.dialect.name != "mysql":
        raise RuntimeError("异步数据库连接不是 MySQL")

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            anime_count = await connection.scalar(select(func.count()).select_from(Anime))
            await connection.execute(_probe_statement(collection_name, 1))
            await connection.execute(_probe_statement(collection_name, 2))
            observed = await connection.scalar(
                select(RagCollectionMetadata.document_count).where(
                    RagCollectionMetadata.collection_name == collection_name
                )
            )
            if observed != 2:
                raise RuntimeError(f"异步事务写后读失败: {observed!r}")
        finally:
            await transaction.rollback()

    async with engine.connect() as connection:
        persisted = await connection.scalar(
            select(func.count())
            .select_from(RagCollectionMetadata)
            .where(RagCollectionMetadata.collection_name == collection_name)
        )
    if persisted:
        raise RuntimeError("异步事务回滚后仍存在探测数据")
    return int(anime_count or 0)


async def main() -> None:
    if not DATABASE_IS_MYSQL:
        raise SystemExit("当前业务数据库不是 MySQL")

    checkpoint_path = Path(RECOMMEND_CHECKPOINT_DB)
    checkpoint_before = _file_sha256(checkpoint_path)
    sync_name = f"__codex_sync_probe_{uuid4().hex}"
    async_name = f"__codex_async_probe_{uuid4().hex}"
    try:
        sync_count = _run_sync_probe(sync_name)
        async_count = await _run_async_probe(async_name)
    finally:
        dispose_sync_engines()
        await dispose_async_engines()

    checkpoint_after = _file_sha256(checkpoint_path)
    if checkpoint_before != checkpoint_after:
        raise RuntimeError("运行探测期间 LangGraph Checkpoint 文件发生变化")
    if sync_count != async_count:
        raise RuntimeError(
            f"同步与异步读取的 anime 行数不一致: {sync_count} != {async_count}"
        )
    print(f"MySQL 运行探测通过：同步/异步均读取 {sync_count} 条 anime")
    print("同步和异步 upsert 均在事务内验证并成功回滚")
    print("LangGraph Checkpoint SQLite 未发生变化")


if __name__ == "__main__":
    asyncio.run(main())
