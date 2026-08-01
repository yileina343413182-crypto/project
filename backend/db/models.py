"""Portable ORM mapping for the 16 business tables.

The LangGraph checkpoint database is intentionally outside this metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import DOUBLE, LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}
LONG_TEXT = Text().with_variant(LONGTEXT(), "mysql")
PRECISE_FLOAT = Float().with_variant(DOUBLE(asdecimal=False), "mysql")


class PortableDateTime(TypeDecorator):
    """Keep legacy SQLite date strings while using real MySQL DATETIME."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(32))
        return dialect.type_descriptor(DateTime())

    @staticmethod
    def _parse(value: str) -> datetime:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value, pattern)
                except ValueError:
                    continue
        raise ValueError(f"Unsupported datetime value: {value!r}")

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "sqlite":
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d %H:%M:%S")
            return str(value)
        if isinstance(value, str):
            return self._parse(value)
        return value


PORTABLE_DATETIME = PortableDateTime()


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class Anime(Base):
    __tablename__ = "anime"
    __table_args__ = MYSQL_TABLE_OPTIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str | None] = mapped_column(LONG_TEXT)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_anime_id_id", "anime_id", "id"),
        Index("ix_comments_anime_id_sentiment_label", "anime_id", "sentiment_label"),
        Index("ix_comments_anime_id_publish_time", "anime_id", "publish_time"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anime_id: Mapped[int] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    clean_content: Mapped[str | None] = mapped_column(LONG_TEXT)
    publish_time: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    likes: Mapped[int | None] = mapped_column(Integer, default=0)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    sentiment_label: Mapped[str | None] = mapped_column(String(32))
    sentiment_score: Mapped[float | None] = mapped_column(PRECISE_FLOAT)
    model_used: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        Index("ix_topics_anime_id_topic_id", "anime_id", "topic_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anime_id: Mapped[int] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    keywords: Mapped[Any] = mapped_column(JSON, nullable=False)
    weight: Mapped[float | None] = mapped_column(PRECISE_FLOAT)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class ChatHistory(Base):
    __tablename__ = "chat_history"
    __table_args__ = (
        Index("ix_chat_history_user_id_created_at_id", "user_id", "created_at", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    anime_card: Mapped[Any | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_agent_sessions_user_id_updated_at", "user_id", "updated_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        Index("ix_agent_messages_session_id_id", "session_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    message_metadata: Mapped[Any | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_agent_tasks_user_session_status", "user_id", "session_id", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    input_data: Mapped[Any | None] = mapped_column("input", JSON)
    result: Mapped[Any | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(LONG_TEXT)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step: Mapped[str] = mapped_column(String(128), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    finished_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    updated_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    likes: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    dislikes: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    preferred_moods: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    preferred_genres: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    feedback: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class RagIndexJob(Base):
    __tablename__ = "rag_index_jobs"
    __table_args__ = (Index("ix_rag_index_jobs_status_id", "status", "id"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    collection_name: Mapped[str] = mapped_column(String(191), nullable=False)
    anime_id: Mapped[int | None] = mapped_column(Integer)
    total_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step: Mapped[str] = mapped_column(String(128), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(LONG_TEXT)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    finished_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)
    updated_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class RagDocument(Base):
    __tablename__ = "rag_documents"
    __table_args__ = (
        UniqueConstraint("collection_name", "doc_id"),
        Index("ix_rag_documents_collection_anime", "collection_name", "anime_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_name: Mapped[str] = mapped_column(String(191), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    anime_id: Mapped[int | None] = mapped_column(Integer)
    anime_name: Mapped[str | None] = mapped_column(String(255))
    comment_id: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    document_metadata: Mapped[Any] = mapped_column("metadata", JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class RagActiveCollection(Base):
    __tablename__ = "rag_active_collections"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    collection_name: Mapped[str] = mapped_column(String(191), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class RagCollectionMetadata(Base):
    __tablename__ = "rag_collection_metadata"
    __table_args__ = MYSQL_TABLE_OPTIONS

    collection_name: Mapped[str] = mapped_column(String(191), primary_key=True)
    embedding_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class RagEvalCase(Base):
    __tablename__ = "rag_eval_cases"
    __table_args__ = MYSQL_TABLE_OPTIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    expected_anime_id: Mapped[int | None] = mapped_column(Integer)
    expected_source_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


class RagEvalRun(Base):
    __tablename__ = "rag_eval_runs"
    __table_args__ = MYSQL_TABLE_OPTIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    metrics: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(LONG_TEXT)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(PORTABLE_DATETIME)


class RagEvalItem(Base):
    __tablename__ = "rag_eval_items"
    __table_args__ = (Index("ix_rag_eval_items_run_id", "run_id"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("rag_eval_runs.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("rag_eval_cases.id", ondelete="SET NULL"))
    query: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metrics: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(LONG_TEXT)
    created_at: Mapped[datetime] = mapped_column(PORTABLE_DATETIME, nullable=False, server_default=func.now())


BUSINESS_TABLES = tuple(Base.metadata.tables)
