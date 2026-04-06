"""add ad_id to tracker events

Revision ID: 20260406_0007
Revises: 20260406_0006
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260406_0007"
down_revision = "20260406_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("tracker_events")}
    if "ad_id" not in columns:
        op.add_column("tracker_events", sa.Column("ad_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("tracker_events")}
    if "ad_id" in columns:
        op.drop_column("tracker_events", "ad_id")
