"""Add authenticated image attachments for Recommendation Agent.

Revision ID: 20260819_06
Revises: 20260814_05
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20260819_06"
down_revision = "20260814_05"
branch_labels = None
depends_on = None


def _create_table() -> None:
    op.create_table(
        "agent_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer()),
        sa.Column("message_id", sa.Integer()),
        sa.Column(
            "content",
            sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql"),
            nullable=False,
        ),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name="fk_agent_attachments_session_id_agent_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_agent_attachments_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_attachments"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_agent_attachments_user_id_created_at",
        "agent_attachments",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_agent_attachments_session_id",
        "agent_attachments",
        ["session_id"],
    )
    op.create_index(
        "ux_agent_attachments_message_id",
        "agent_attachments",
        ["message_id"],
        unique=True,
    )


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        # base -> head 已由动态初始 revision 输出当前 Base；增量 SQL 才补建新表。
        if context.get_current_revision():
            _create_table()
        return
    if not sa.inspect(op.get_bind()).has_table("agent_attachments"):
        _create_table()


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop Agent attachment data automatically. "
        "Restore the pre-migration database or remove the verified table manually."
    )
