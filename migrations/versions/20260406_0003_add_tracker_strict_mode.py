"""add strict mode to trackers

Revision ID: 20260406_0003
Revises: 20260406_0002
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260406_0003"
down_revision = "20260406_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tracker_columns = {column["name"] for column in inspector.get_columns("trackers")}
    if "strict_mode" not in tracker_columns:
        op.add_column(
            "trackers",
            sa.Column("strict_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tracker_columns = {column["name"] for column in inspector.get_columns("trackers")}
    if "strict_mode" in tracker_columns:
        op.drop_column("trackers", "strict_mode")
