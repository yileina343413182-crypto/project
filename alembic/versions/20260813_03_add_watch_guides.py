"""Add the per-user watch guide table when it is not already present.

Revision ID: 20260813_03
Revises: 20260801_02
Create Date: 2026-08-13
"""

from alembic import op
from sqlalchemy import inspect

from backend.db.models import WatchGuide


revision = "20260813_03"
down_revision = "20260801_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    context = op.get_context()
    if context.as_sql:
        # base -> head 的离线脚本已由动态初始 revision 输出当前完整元数据；
        # 从既有 revision 增量生成时，才需要在本 revision 显式补表。
        if context.get_current_revision():
            WatchGuide.__table__.create(bind=bind)
        return
    if inspect(bind).has_table(WatchGuide.__tablename__):
        return
    WatchGuide.__table__.create(bind=bind)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop watch_guides automatically. "
        "Restore the pre-migration database or remove the verified table manually."
    )
