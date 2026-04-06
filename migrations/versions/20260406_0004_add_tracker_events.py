"""add tracker events table

Revision ID: 20260406_0004
Revises: 20260406_0003
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260406_0004"
down_revision = "20260406_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tracker_events" not in tables:
        op.create_table(
            "tracker_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tracker_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("query", sa.String(length=255), nullable=False),
            sa.Column(
                "strict_mode",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("link", sa.String(length=512), nullable=False),
            sa.Column("price_byn", sa.Float(), nullable=True),
            sa.Column("delta_byn", sa.Float(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("tracker_events")}
    if "idx_tracker_events_user" not in indexes:
        op.create_index("idx_tracker_events_user", "tracker_events", ["user_id"])
    if "idx_tracker_events_created" not in indexes:
        op.create_index("idx_tracker_events_created", "tracker_events", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "tracker_events" not in tables:
        return

    indexes = {index["name"] for index in inspector.get_indexes("tracker_events")}
    if "idx_tracker_events_created" in indexes:
        op.drop_index("idx_tracker_events_created", table_name="tracker_events")
    if "idx_tracker_events_user" in indexes:
        op.drop_index("idx_tracker_events_user", table_name="tracker_events")
    op.drop_table("tracker_events")
