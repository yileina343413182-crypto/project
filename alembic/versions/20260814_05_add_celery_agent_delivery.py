"""Add M2 Celery delivery leases and idempotent Agent messages.

Revision ID: 20260814_05
Revises: 20260814_04
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260814_05"
down_revision = "20260814_04"
branch_labels = None
depends_on = None


_MESSAGE_INDEX = "ux_agent_messages_source_task_id"
_TASK_INDEXES = {
    "ix_agent_tasks_status_lease_until": ("status", "lease_until"),
    "ix_agent_tasks_celery_task_id": ("celery_task_id",),
}


def _column_names(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _index_names(inspector, table: str) -> set[str]:
    return {
        index.get("name")
        for index in inspector.get_indexes(table)
        if index.get("name")
    }


def _add_columns_and_indexes() -> None:
    op.add_column("agent_messages", sa.Column("source_task_id", sa.Integer()))
    op.create_index(
        _MESSAGE_INDEX,
        "agent_messages",
        ["source_task_id"],
        unique=True,
    )
    op.add_column("agent_tasks", sa.Column("celery_task_id", sa.String(64)))
    op.add_column("agent_tasks", sa.Column("worker_id", sa.String(255)))
    op.add_column("agent_tasks", sa.Column("lease_until", sa.DateTime()))
    op.add_column("agent_tasks", sa.Column("heartbeat_at", sa.DateTime()))
    op.add_column(
        "agent_tasks",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    for name, columns in _TASK_INDEXES.items():
        op.create_index(name, "agent_tasks", list(columns))


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        # base -> head 已由动态初始 revision 输出当前完整 Base；增量脚本才需要 ALTER。
        if context.get_current_revision():
            _add_columns_and_indexes()
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    message_columns = _column_names(inspector, "agent_messages")
    if "source_task_id" not in message_columns:
        op.add_column("agent_messages", sa.Column("source_task_id", sa.Integer()))

    inspector = sa.inspect(bind)
    if _MESSAGE_INDEX not in _index_names(inspector, "agent_messages"):
        op.create_index(
            _MESSAGE_INDEX,
            "agent_messages",
            ["source_task_id"],
            unique=True,
        )

    task_columns = _column_names(inspector, "agent_tasks")
    task_definitions = {
        "celery_task_id": sa.Column("celery_task_id", sa.String(64)),
        "worker_id": sa.Column("worker_id", sa.String(255)),
        "lease_until": sa.Column("lease_until", sa.DateTime()),
        "heartbeat_at": sa.Column("heartbeat_at", sa.DateTime()),
        "attempt_count": sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    }
    for name, column in task_definitions.items():
        if name not in task_columns:
            op.add_column("agent_tasks", column)

    inspector = sa.inspect(bind)
    task_indexes = _index_names(inspector, "agent_tasks")
    for name, columns in _TASK_INDEXES.items():
        if name not in task_indexes:
            op.create_index(name, "agent_tasks", list(columns))


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop Celery delivery or message idempotency columns automatically. "
        "Restore the pre-migration database or remove verified columns manually."
    )
