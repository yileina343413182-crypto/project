"""Isolated checks for the shared SQLAlchemy schema and dual session boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from backend.agents.memory import init_agent_tables
from backend.db.async_repository import delete_watch_guide, get_watch_guide, list_watch_guides
from backend.db.models import (
    AgentMessage,
    AgentSession,
    AgentTask,
    BUSINESS_TABLES,
    Base,
    Comment,
    Topic,
    User,
    WatchGuide,
)
from backend.db.session import get_async_engine, get_async_sessionmaker, get_sync_engine
from scripts.migrate_sqlite_to_mysql import migrate_business_tables
from scripts.verify_mysql_migration import compare_business_tables


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

    def test_schema_contains_exactly_17_business_tables(self):
        tables = set(inspect(self.engine).get_table_names())
        self.assertEqual(tables, set(BUSINESS_TABLES))
        self.assertEqual(len(tables), 17)
        self.assertIn("watch_guides", tables)
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
        guide_ddl = str(CreateTable(WatchGuide.__table__).compile(dialect=dialect))
        self.assertIn("sentiment_score DOUBLE", comment_ddl)
        self.assertIn("weight DOUBLE", topic_ddl)
        self.assertIn("guide_content LONGTEXT", guide_ddl)
        self.assertIn("UNIQUE (user_id, anime_key)", guide_ddl)
        self.assertIn("ON DELETE SET NULL", guide_ddl)

    def test_agent_task_schema_contains_m1_idempotency_and_turn_indexes(self):
        inspector = inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("agent_tasks")}
        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("agent_tasks")
        }
        self.assertIn("client_request_id", columns)
        self.assertIn("turn_seq", columns)
        self.assertTrue(indexes["ux_agent_tasks_user_agent_request"]["unique"])
        self.assertTrue(indexes["ux_agent_tasks_session_turn"]["unique"])

    def test_agent_delivery_schema_contains_m2_lease_and_message_idempotency(self):
        inspector = inspect(self.engine)
        task_columns = {
            column["name"] for column in inspector.get_columns(AgentTask.__tablename__)
        }
        task_indexes = {
            index["name"] for index in inspector.get_indexes(AgentTask.__tablename__)
        }
        message_columns = {
            column["name"] for column in inspector.get_columns(AgentMessage.__tablename__)
        }
        message_indexes = {
            index["name"]: index
            for index in inspector.get_indexes(AgentMessage.__tablename__)
        }
        self.assertTrue(
            {
                "celery_task_id",
                "worker_id",
                "lease_until",
                "heartbeat_at",
                "attempt_count",
            }.issubset(task_columns)
        )
        self.assertIn("ix_agent_tasks_status_lease_until", task_indexes)
        self.assertIn("ix_agent_tasks_celery_task_id", task_indexes)
        self.assertIn("source_task_id", message_columns)
        self.assertTrue(message_indexes["ux_agent_messages_source_task_id"]["unique"])

    def test_init_agent_tables_adds_m1_columns_to_legacy_sqlite(self):
        handle = tempfile.NamedTemporaryFile(prefix="orm_agent_legacy_", suffix=".db", delete=False)
        handle.close()
        legacy_path = Path(handle.name)
        legacy_engine = get_sync_engine(db_path=str(legacy_path))
        try:
            Base.metadata.create_all(
                legacy_engine,
                tables=[User.__table__, AgentSession.__table__],
            )
            with legacy_engine.begin() as connection:
                connection.exec_driver_sql(
                    """
                    CREATE TABLE agent_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id INTEGER NOT NULL,
                        agent_type VARCHAR(32) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        input JSON,
                        result JSON,
                        error TEXT,
                        progress INTEGER NOT NULL,
                        current_step VARCHAR(128) NOT NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        started_at DATETIME,
                        finished_at DATETIME,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
                    )
                    """
                )

            init_agent_tables(db_path=str(legacy_path))
            inspector = inspect(legacy_engine)
            columns = {column["name"] for column in inspector.get_columns("agent_tasks")}
            indexes = {index["name"] for index in inspector.get_indexes("agent_tasks")}
            self.assertIn("client_request_id", columns)
            self.assertIn("turn_seq", columns)
            self.assertIn("ux_agent_tasks_user_agent_request", indexes)
            self.assertIn("ux_agent_tasks_session_turn", indexes)
        finally:
            legacy_engine.dispose()
            legacy_path.unlink(missing_ok=True)

    def test_agent_task_m1_revision_is_additive_and_idempotent(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "20260814_04_add_agent_task_concurrency_keys.py"
        )
        spec = importlib.util.spec_from_file_location("agent_task_m1_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    """
                    CREATE TABLE agent_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        session_id INTEGER NOT NULL,
                        agent_type VARCHAR(32) NOT NULL,
                        status VARCHAR(32) NOT NULL
                    )
                    """
                )
                migration.op = Operations(MigrationContext.configure(connection))
                migration.upgrade()
                migration.upgrade()

                inspector = inspect(connection)
                columns = {
                    column["name"]
                    for column in inspector.get_columns("agent_tasks")
                }
                indexes = {
                    index["name"]: index
                    for index in inspector.get_indexes("agent_tasks")
                }
                self.assertIn("client_request_id", columns)
                self.assertIn("turn_seq", columns)
                self.assertTrue(indexes["ux_agent_tasks_user_agent_request"]["unique"])
                self.assertTrue(indexes["ux_agent_tasks_session_turn"]["unique"])
        finally:
            engine.dispose()

    def test_agent_task_m2_revision_is_additive_and_idempotent(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "20260814_05_add_celery_agent_delivery.py"
        )
        spec = importlib.util.spec_from_file_location("agent_task_m2_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE agent_messages (id INTEGER PRIMARY KEY AUTOINCREMENT)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE agent_tasks ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, status VARCHAR(32) NOT NULL)"
                )
                migration.op = Operations(MigrationContext.configure(connection))
                migration.upgrade()
                migration.upgrade()

                inspector = inspect(connection)
                message_columns = {
                    column["name"] for column in inspector.get_columns("agent_messages")
                }
                task_columns = {
                    column["name"] for column in inspector.get_columns("agent_tasks")
                }
                message_indexes = {
                    index["name"]: index
                    for index in inspector.get_indexes("agent_messages")
                }
                task_indexes = {
                    index["name"] for index in inspector.get_indexes("agent_tasks")
                }
                self.assertIn("source_task_id", message_columns)
                self.assertTrue(message_indexes["ux_agent_messages_source_task_id"]["unique"])
                self.assertTrue(
                    {
                        "celery_task_id",
                        "worker_id",
                        "lease_until",
                        "heartbeat_at",
                        "attempt_count",
                    }.issubset(task_columns)
                )
                self.assertIn("ix_agent_tasks_status_lease_until", task_indexes)
                self.assertIn("ix_agent_tasks_celery_task_id", task_indexes)
        finally:
            engine.dispose()

    def test_watch_guides_are_isolated_by_user_in_async_repository(self):
        with self.engine.begin() as connection:
            user_one = connection.execute(
                User.__table__.insert().values(username="guide_owner", password_hash="test")
            ).inserted_primary_key[0]
            user_two = connection.execute(
                User.__table__.insert().values(username="guide_other", password_hash="test")
            ).inserted_primary_key[0]
            session_one = connection.execute(
                AgentSession.__table__.insert().values(
                    user_id=user_one,
                    agent_type="recommendation",
                    title="owner session",
                )
            ).inserted_primary_key[0]
            guide_one = connection.execute(
                WatchGuide.__table__.insert().values(
                    user_id=user_one,
                    source_session_id=session_one,
                    anime_name="四月是你的谎言",
                    anime_key="owner-anime-key",
                    guide_content="owner guide",
                )
            ).inserted_primary_key[0]
            guide_two = connection.execute(
                WatchGuide.__table__.insert().values(
                    user_id=user_two,
                    anime_name="CLANNAD",
                    anime_key="other-anime-key",
                    guide_content="other guide",
                )
            ).inserted_primary_key[0]

        async def exercise_repository():
            async_engine = get_async_engine(db_path=str(self.path))
            factory = get_async_sessionmaker(db_path=str(self.path))
            try:
                async with factory() as session:
                    listing = await list_watch_guides(session, user_one)
                    detail = await get_watch_guide(session, guide_one, user_one)
                    hidden = await get_watch_guide(session, guide_two, user_one)
                    forbidden_delete = await delete_watch_guide(session, guide_two, user_one)
                    own_delete = await delete_watch_guide(session, guide_one, user_one)
                    await session.commit()
                async with factory() as session:
                    other_still_exists = await get_watch_guide(session, guide_two, user_two)
                return listing, detail, hidden, forbidden_delete, own_delete, other_still_exists
            finally:
                await async_engine.dispose()

        listing, detail, hidden, forbidden_delete, own_delete, other_detail = asyncio.run(
            exercise_repository()
        )
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["id"], guide_one)
        self.assertNotIn("guide_content", listing["items"][0])
        self.assertEqual(detail["guide_content"], "owner guide")
        self.assertIsNone(hidden)
        self.assertEqual(forbidden_delete, 0)
        self.assertEqual(own_delete, 1)
        self.assertEqual(other_detail["id"], guide_two)

    def test_watch_guide_constraints_preserve_guide_until_user_deletion(self):
        with self.engine.begin() as connection:
            user_id = connection.execute(
                User.__table__.insert().values(username="guide_lifecycle", password_hash="test")
            ).inserted_primary_key[0]
            session_id = connection.execute(
                AgentSession.__table__.insert().values(
                    user_id=user_id,
                    agent_type="recommendation",
                    title="guide session",
                )
            ).inserted_primary_key[0]
            guide_id = connection.execute(
                WatchGuide.__table__.insert().values(
                    user_id=user_id,
                    source_session_id=session_id,
                    anime_name="紫罗兰永恒花园",
                    anime_key="same-anime-key",
                    guide_content="guide",
                )
            ).inserted_primary_key[0]

        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(
                    WatchGuide.__table__.insert().values(
                        user_id=user_id,
                        anime_name="重复标题",
                        anime_key="same-anime-key",
                        guide_content="duplicate",
                    )
                )

        with self.engine.begin() as connection:
            connection.execute(delete(AgentSession).where(AgentSession.id == session_id))
            source_session_id = connection.scalar(
                select(WatchGuide.source_session_id).where(WatchGuide.id == guide_id)
            )
            self.assertIsNone(source_session_id)
            connection.execute(delete(User).where(User.id == user_id))
            remaining = connection.scalar(
                select(func.count()).select_from(WatchGuide).where(WatchGuide.id == guide_id)
            )
            self.assertEqual(remaining, 0)

    def test_legacy_sqlite_without_watch_guides_can_be_migrated_and_verified(self):
        handle = tempfile.NamedTemporaryFile(prefix="orm_legacy_", suffix=".db", delete=False)
        handle.close()
        legacy_path = Path(handle.name)
        legacy_engine = get_sync_engine(db_path=str(legacy_path))
        legacy_tables = [
            table for table in Base.metadata.sorted_tables if table.name != WatchGuide.__tablename__
        ]
        Base.metadata.create_all(legacy_engine, tables=legacy_tables)
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql("DROP INDEX ux_agent_tasks_user_agent_request")
            connection.exec_driver_sql("DROP INDEX ux_agent_tasks_session_turn")
            connection.exec_driver_sql("ALTER TABLE agent_tasks DROP COLUMN client_request_id")
            connection.exec_driver_sql("ALTER TABLE agent_tasks DROP COLUMN turn_seq")
        try:
            counts = migrate_business_tables(legacy_engine, self.engine)
            verified = compare_business_tables(legacy_engine, self.engine)
            self.assertEqual(counts[WatchGuide.__tablename__], 0)
            self.assertEqual(verified[WatchGuide.__tablename__]["rows"], 0)
        finally:
            legacy_engine.dispose()
            legacy_path.unlink(missing_ok=True)

    def test_watch_guide_revision_adds_only_the_missing_table(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "20260813_03_add_watch_guides.py"
        )
        spec = importlib.util.spec_from_file_location("watch_guide_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        try:
            with engine.begin() as connection:
                Base.metadata.create_all(
                    connection,
                    tables=[User.__table__, AgentSession.__table__],
                )
                migration.op = SimpleNamespace(
                    get_bind=lambda: connection,
                    get_context=lambda: SimpleNamespace(as_sql=False),
                )
                migration.upgrade()
                self.assertTrue(inspect(connection).has_table(WatchGuide.__tablename__))
                migration.upgrade()
        finally:
            engine.dispose()

    def test_watch_guide_revision_supports_offline_sql_context(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "20260813_03_add_watch_guides.py"
        )
        spec = importlib.util.spec_from_file_location("watch_guide_offline_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        context = SimpleNamespace(
            as_sql=True,
            get_current_revision=lambda: None,
        )
        migration.op = SimpleNamespace(
            get_bind=lambda: object(),
            get_context=lambda: context,
        )
        with patch.object(WatchGuide.__table__, "create") as create_mock:
            migration.upgrade()
        create_mock.assert_not_called()

        context.get_current_revision = lambda: "20260801_02"
        with patch.object(WatchGuide.__table__, "create") as create_mock:
            migration.upgrade()
        create_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
