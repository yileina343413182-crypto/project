"""Add per-user anime viewing statuses.

Revision ID: 20260821_07
Revises: 20260819_06
Create Date: 2026-08-21
"""

from alembic import op
from sqlalchemy import inspect

from backend.db.models import UserAnimeStatus


revision = "20260821_07"
down_revision = "20260819_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    context = op.get_context()
    if context.as_sql:
        if context.get_current_revision():
            UserAnimeStatus.__table__.create(bind=bind)
        return
    if not inspect(bind).has_table(UserAnimeStatus.__tablename__):
        UserAnimeStatus.__table__.create(bind=bind)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop user anime status data automatically. "
        "Restore the pre-migration database or remove the verified table manually."
    )
