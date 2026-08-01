"""Copy the 16 business tables from legacy SQLite into an empty MySQL schema.

The LangGraph checkpoint database is intentionally excluded. Run Alembic against
the MySQL target before this script. The copy is one transaction and refuses a
non-empty target, so a failed migration can be retried without partial commits.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import JSON, create_engine, func, inspect, select
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import DATABASE_URL, DB_PATH
from backend.db.models import BUSINESS_TABLES, Base

logger = logging.getLogger(__name__)


class MigrationPreflightError(RuntimeError):
    """Raised before any target rows are written."""


def _quoted_select(engine: Engine, table) -> str:
    quote = engine.dialect.identifier_preparer.quote
    columns = ", ".join(quote(column.name) for column in table.columns)
    primary_key = ", ".join(quote(column.name) for column in table.primary_key.columns)
    order_by = f" ORDER BY {primary_key}" if primary_key else ""
    return f"SELECT {columns} FROM {quote(table.name)}{order_by}"


def _normalize_row(table, row: dict) -> dict:
    normalized = dict(row)
    primary_key = tuple(row.get(column.name) for column in table.primary_key.columns)
    for column in table.columns:
        value = normalized.get(column.name)
        if not isinstance(column.type, JSON) or value is None or not isinstance(value, (str, bytes)):
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            normalized[column.name] = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise MigrationPreflightError(
                f"Invalid JSON in {table.name}.{column.name}, primary key={primary_key}"
            ) from exc
    return normalized


def _validate_schema(source: Engine, target: Engine) -> None:
    source_inspector = inspect(source)
    target_inspector = inspect(target)
    missing_target = [name for name in BUSINESS_TABLES if not target_inspector.has_table(name)]
    if missing_target:
        raise MigrationPreflightError(
            "Target schema is incomplete; run Alembic first. Missing: " + ", ".join(missing_target)
        )

    for table in Base.metadata.sorted_tables:
        if not source_inspector.has_table(table.name):
            continue
        actual = {column["name"] for column in source_inspector.get_columns(table.name)}
        expected = {column.name for column in table.columns}
        missing_columns = sorted(expected - actual)
        if missing_columns:
            raise MigrationPreflightError(
                f"Source table {table.name} is missing columns: {', '.join(missing_columns)}"
            )


def _ensure_empty_target(target: Engine) -> None:
    with target.connect() as connection:
        non_empty = []
        for table in Base.metadata.sorted_tables:
            count = connection.scalar(select(func.count()).select_from(table)) or 0
            if count:
                non_empty.append(f"{table.name}={count}")
    if non_empty:
        raise MigrationPreflightError(
            "Target business tables must be empty; found " + ", ".join(non_empty)
        )


def migrate_business_tables(source: Engine, target: Engine, chunk_size: int = 1000) -> dict[str, int]:
    """Copy all present business tables and return verified target row counts."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    _validate_schema(source, target)
    _ensure_empty_target(target)
    source_inspector = inspect(source)
    expected_counts: dict[str, int] = {}

    with source.connect() as source_connection, target.begin() as target_connection:
        if target.dialect.name == "mysql":
            # Preserve legitimate SQLite primary key 0 values instead of treating
            # them as MySQL AUTO_INCREMENT requests. This is connection-local.
            target_connection.exec_driver_sql(
                "SET SESSION sql_mode = IF("
                "FIND_IN_SET('NO_AUTO_VALUE_ON_ZERO', @@SESSION.sql_mode), "
                "@@SESSION.sql_mode, "
                "CONCAT_WS(',', NULLIF(@@SESSION.sql_mode, ''), 'NO_AUTO_VALUE_ON_ZERO'))"
            )
        for table in Base.metadata.sorted_tables:
            if not source_inspector.has_table(table.name):
                expected_counts[table.name] = 0
                continue
            result = source_connection.exec_driver_sql(_quoted_select(source, table))
            count = 0
            while True:
                rows = result.mappings().fetchmany(chunk_size)
                if not rows:
                    break
                values = [_normalize_row(table, dict(row)) for row in rows]
                target_connection.execute(table.insert(), values)
                count += len(values)
            expected_counts[table.name] = count
            logger.info("Copied %-28s %d", table.name, count)

    verified_counts: dict[str, int] = {}
    with target.connect() as connection:
        for table in Base.metadata.sorted_tables:
            actual = connection.scalar(select(func.count()).select_from(table)) or 0
            expected = expected_counts[table.name]
            if actual != expected:
                raise RuntimeError(
                    f"Post-copy count mismatch for {table.name}: expected {expected}, got {actual}"
                )
            verified_counts[table.name] = actual
    return verified_counts


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移 16 张业务表：SQLite -> 空 MySQL schema")
    parser.add_argument("--source", default=DB_PATH, help="源 SQLite 文件路径")
    parser.add_argument(
        "--target-url",
        default=DATABASE_URL,
        help="目标 SQLAlchemy URL；默认读取 DATABASE_URL/MYSQL_* 环境变量",
    )
    parser.add_argument("--chunk-size", type=int, default=1000, help="单批写入行数")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    source_path = Path(args.source).resolve()
    if not source_path.is_file():
        raise SystemExit(f"源 SQLite 文件不存在: {source_path}")

    source = create_engine(f"sqlite+pysqlite:///{source_path.as_posix()}", future=True)
    target = create_engine(args.target_url, future=True, pool_pre_ping=True)
    try:
        if target.dialect.name != "mysql":
            raise SystemExit("目标必须是 MySQL；请配置 DATABASE_URL 或 MYSQL_* 凭据")
        counts = migrate_business_tables(source, target, args.chunk_size)
        print(f"迁移并核验完成：{len(counts)} 张业务表，共 {sum(counts.values())} 行")
        print("LangGraph Checkpoint SQLite 未读取、未修改。")
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    main()
