"""Add market_status and missing_since_at to LeadItem, missing_since_at to WatchlistItem

Revision ID: 20260408_0015
Revises: 20260408_0014
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import DateTime, String

revision = "20260408_0015"
down_revision = "20260408_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # --- LeadItem: add market_status and missing_since_at ---
    if "lead_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("lead_items")}
        if "market_status" not in columns:
            op.add_column(
                "lead_items",
                sa.Column(
                    "market_status",
                    String(32),
                    nullable=False,
                    server_default="active",
                ),
            )
        if "missing_since_at" not in columns:
            op.add_column(
                "lead_items",
                sa.Column("missing_since_at", DateTime(timezone=True), nullable=True),
            )

    # --- WatchlistItem: add missing_since_at ---
    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "missing_since_at" not in columns:
            op.add_column(
                "watchlist_items",
                sa.Column("missing_since_at", DateTime(timezone=True), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "lead_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("lead_items")}
        if "missing_since_at" in columns:
            op.drop_column("lead_items", "missing_since_at")
        if "market_status" in columns:
            op.drop_column("lead_items", "market_status")

    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "missing_since_at" in columns:
            op.drop_column("watchlist_items", "missing_since_at")
