"""Add recommendation short-term and long-term memory tables.

Revision ID: 20260824_08
Revises: 20260821_07
Create Date: 2026-08-24
"""

from alembic import op
from sqlalchemy import inspect

from backend.db.models import AgentSessionMemory, UserMemoryFact


revision = "20260824_08"
down_revision = "20260821_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    context = op.get_context()
    tables = (AgentSessionMemory.__table__, UserMemoryFact.__table__)
    if context.as_sql:
        if context.get_current_revision():
            for table in tables:
                table.create(bind=bind)
        return
    inspector = inspect(bind)
    for table in tables:
        if not inspector.has_table(table.name):
            table.create(bind=bind)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop recommendation memory data automatically. "
        "Restore the pre-migration database or remove verified tables manually."
    )
