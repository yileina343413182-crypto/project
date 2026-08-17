"""Read-only, row-complete verification of SQLite to MySQL business data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import JSON as SqlJson
from sqlalchemy import Boolean, create_engine, inspect, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import DATABASE_URL, DB_PATH
from backend.db.models import Base
from scripts.migrate_sqlite_to_mysql import _normalize_row, _quoted_select


def _canonical_json(value):
    if isinstance(value, float):
        # MySQL native JSON may round a Python double by one ULP when it
        # canonicalizes numeric JSON. Fourteen significant digits are stable
        # across that representation boundary while remaining stricter than
        # the application's six-decimal score display.
        return {"json_float_14g": format(value, ".14g")}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_json(item) for key, item in value.items()}
    return value


def _canonical_value(column, value):
    if value is None:
        return None
    if isinstance(column.type, Boolean):
        return bool(value)
    if isinstance(column.type, SqlJson):
        return _canonical_json(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return {"float_hex": value.hex()}
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return value


def _digest_rows(table, rows) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        payload = [_canonical_value(column, row[column.name]) for column in table.columns]
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(encoded)
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def compare_business_tables(source, target) -> dict[str, dict[str, str | int]]:
    """Compare every normalized column value in every business row."""
    results = {}
    source_inspector = inspect(source)
    with source.connect() as source_connection, target.connect() as target_connection:
        for table in Base.metadata.sorted_tables:
            if source_inspector.has_table(table.name):
                available_columns = {
                    column["name"]
                    for column in source_inspector.get_columns(table.name)
                }
                source_result = source_connection.exec_driver_sql(
                    _quoted_select(source, table, available_columns)
                )
                source_rows = (
                    _normalize_row(table, dict(zip((column.name for column in table.columns), row)))
                    for row in source_result
                )
            else:
                source_rows = ()
            source_count, source_digest = _digest_rows(table, source_rows)

            order_by = list(table.primary_key.columns)
            statement = select(*table.columns)
            if order_by:
                statement = statement.order_by(*order_by)
            target_result = target_connection.execute(statement)
            target_rows = (
                _normalize_row(
                    table,
                    dict(zip((column.name for column in table.columns), row)),
                )
                for row in target_result
            )
            target_count, target_digest = _digest_rows(table, target_rows)
            if (source_count, source_digest) != (target_count, target_digest):
                raise RuntimeError(
                    f"Data mismatch for {table.name}: "
                    f"SQLite=({source_count}, {source_digest}), "
                    f"MySQL=({target_count}, {target_digest})"
                )
            results[table.name] = {"rows": source_count, "sha256": source_digest}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="逐行逐列核验 SQLite 与 MySQL 的 17 张业务表")
    parser.add_argument("--source", default=DB_PATH, help="源 SQLite 文件路径")
    parser.add_argument("--target-url", default=DATABASE_URL, help="目标 MySQL SQLAlchemy URL")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.is_file():
        raise SystemExit(f"源 SQLite 文件不存在: {source_path}")
    source = create_engine(f"sqlite+pysqlite:///{source_path.as_posix()}", future=True)
    target = create_engine(args.target_url, future=True, pool_pre_ping=True)
    try:
        if target.dialect.name != "mysql":
            raise SystemExit("目标必须是 MySQL")
        results = compare_business_tables(source, target)
        for table_name, result in results.items():
            print(f"{table_name:<28} {result['rows']:>7}  {result['sha256']}")
        print(f"全量一致性核验通过：{len(results)} 张业务表，共 {sum(r['rows'] for r in results.values())} 行")
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    main()
