"""Create the 16-table business schema; checkpoint SQLite is excluded.

Revision ID: 20260801_01
Revises: None
Create Date: 2026-08-01
"""

from alembic import op

from backend.db.models import Base


revision = "20260801_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop business tables automatically. "
        "Restore the pre-migration database or remove explicitly verified tables manually."
    )
