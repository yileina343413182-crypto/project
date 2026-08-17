"""Add M1 Agent task idempotency and turn-order keys.

Revision ID: 20260814_04
Revises: 20260813_03
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260814_04"
down_revision = "20260813_03"
branch_labels = None
depends_on = None


_REQUEST_INDEX = "ux_agent_tasks_user_agent_request"
_TURN_INDEX = "ux_agent_tasks_session_turn"


def _column_names(inspector) -> set[str]:
    return {column["name"] for column in inspector.get_columns("agent_tasks")}


def _index_names(inspector) -> set[str]:
    return {
        index.get("name")
        for index in inspector.get_indexes("agent_tasks")
        if index.get("name")
    }


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        # 初始 revision 会从当前 Base 生成完整表；仅增量离线脚本需要 ALTER。
        if not context.get_current_revision():
            return
        op.add_column("agent_tasks", sa.Column("client_request_id", sa.String(64)))
        op.add_column("agent_tasks", sa.Column("turn_seq", sa.Integer()))
        op.create_index(
            _REQUEST_INDEX,
            "agent_tasks",
            ["user_id", "agent_type", "client_request_id"],
            unique=True,
        )
        op.create_index(
            _TURN_INDEX,
            "agent_tasks",
            ["session_id", "turn_seq"],
            unique=True,
        )
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _column_names(inspector)
    if "client_request_id" not in columns:
        op.add_column("agent_tasks", sa.Column("client_request_id", sa.String(64)))
    if "turn_seq" not in columns:
        op.add_column("agent_tasks", sa.Column("turn_seq", sa.Integer()))

    inspector = sa.inspect(bind)
    index_names = _index_names(inspector)
    if _REQUEST_INDEX not in index_names:
        op.create_index(
            _REQUEST_INDEX,
            "agent_tasks",
            ["user_id", "agent_type", "client_request_id"],
            unique=True,
        )
    if _TURN_INDEX not in index_names:
        op.create_index(
            _TURN_INDEX,
            "agent_tasks",
            ["session_id", "turn_seq"],
            unique=True,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop Agent idempotency columns automatically. "
        "Restore the pre-migration database or remove verified columns manually."
    )
