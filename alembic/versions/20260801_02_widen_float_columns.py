"""Use MySQL DOUBLE for migrated SQLite REAL values.

Revision ID: 20260801_02
Revises: 20260801_01
Create Date: 2026-08-01
"""

from alembic import op
from sqlalchemy.dialects import mysql


revision = "20260801_02"
down_revision = "20260801_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    op.alter_column(
        "comments",
        "sentiment_score",
        existing_type=mysql.FLOAT(),
        type_=mysql.DOUBLE(asdecimal=False),
        existing_nullable=True,
    )
    op.alter_column(
        "topics",
        "weight",
        existing_type=mysql.FLOAT(),
        type_=mysql.DOUBLE(asdecimal=False),
        existing_nullable=True,
    )


def downgrade() -> None:
    raise RuntimeError("Refusing to reduce DOUBLE columns back to lossy MySQL FLOAT.")
