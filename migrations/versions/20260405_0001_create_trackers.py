"""create trackers table

Revision ID: 20260405_0001
Revises:
Create Date: 2026-04-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260405_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trackers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("query", sa.String(length=255), nullable=False),
        sa.Column("interval_min", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("last_seen_ad_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_trackers_user", "trackers", ["user_id"])
    op.create_index("idx_trackers_active", "trackers", ["active"])


def downgrade() -> None:
    op.drop_index("idx_trackers_active", table_name="trackers")
    op.drop_index("idx_trackers_user", table_name="trackers")
    op.drop_table("trackers")
