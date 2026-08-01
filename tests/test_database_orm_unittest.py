"""Isolated checks for the shared SQLAlchemy schema and dual session boundary."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.schema import CreateTable

from backend.db.models import BUSINESS_TABLES, Base, Comment, Topic, User
from backend.db.session import get_async_engine, get_sync_engine


class DatabaseOrmTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(prefix="orm_schema_", suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.engine = get_sync_engine(db_path=str(self.path))
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.path.unlink(missing_ok=True)

    def test_schema_contains_exactly_16_business_tables(self):
        tables = set(inspect(self.engine).get_table_names())
        self.assertEqual(tables, set(BUSINESS_TABLES))
        self.assertEqual(len(tables), 16)
        self.assertNotIn("checkpoints", tables)
        self.assertNotIn("writes", tables)

    def test_sync_and_async_sessions_share_the_same_mapping(self):
        with self.engine.begin() as connection:
            connection.execute(
                User.__table__.insert().values(username="orm_test", password_hash="test")
            )

        async def read_user():
            async_engine = get_async_engine(db_path=str(self.path))
            try:
                async with async_engine.connect() as connection:
                    result = await connection.execute(
                        select(User.username).where(User.username == "orm_test")
                    )
                    return result.scalar_one()
            finally:
                await async_engine.dispose()

        self.assertEqual(asyncio.run(read_user()), "orm_test")

    def test_mysql_uses_double_for_sqlite_real_compatibility(self):
        dialect = mysql_dialect()
        comment_ddl = str(CreateTable(Comment.__table__).compile(dialect=dialect))
        topic_ddl = str(CreateTable(Topic.__table__).compile(dialect=dialect))
        self.assertIn("sentiment_score DOUBLE", comment_ddl)
        self.assertIn("weight DOUBLE", topic_ddl)


if __name__ == "__main__":
    unittest.main()
