"""add workflow items

Revision ID: 20260406_0006
Revises: 20260406_0005
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260406_0006"
down_revision = "20260406_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "lead_items" not in tables:
        op.create_table(
            "lead_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("ad_id", sa.BigInteger(), nullable=False),
            sa.Column("query", sa.String(length=255), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("link", sa.String(length=512), nullable=False),
            sa.Column("price_byn", sa.Float(), nullable=True),
            sa.Column("target_resale_byn", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="new"),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
            sa.Column("notes", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "ad_id", name="uq_lead_items_user_ad"),
        )
        op.create_index("idx_lead_items_user", "lead_items", ["user_id"])
        op.create_index("idx_lead_items_status", "lead_items", ["status"])

    if "watchlist_items" not in tables:
        op.create_table(
            "watchlist_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("ad_id", sa.BigInteger(), nullable=False),
            sa.Column("query", sa.String(length=255), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("link", sa.String(length=512), nullable=False),
            sa.Column("initial_price_byn", sa.Float(), nullable=True),
            sa.Column("current_price_byn", sa.Float(), nullable=True),
            sa.Column(
                "workflow_status",
                sa.String(length=32),
                nullable=False,
                server_default="watching",
            ),
            sa.Column(
                "market_status",
                sa.String(length=32),
                nullable=False,
                server_default="active",
            ),
            sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("notes", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "ad_id", name="uq_watchlist_items_user_ad"),
        )
        op.create_index("idx_watchlist_items_user", "watchlist_items", ["user_id"])
        op.create_index("idx_watchlist_items_market_status", "watchlist_items", ["market_status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "watchlist_items" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("watchlist_items")}
        if "idx_watchlist_items_market_status" in indexes:
            op.drop_index("idx_watchlist_items_market_status", table_name="watchlist_items")
        if "idx_watchlist_items_user" in indexes:
            op.drop_index("idx_watchlist_items_user", table_name="watchlist_items")
        op.drop_table("watchlist_items")

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "lead_items" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("lead_items")}
        if "idx_lead_items_status" in indexes:
            op.drop_index("idx_lead_items_status", table_name="lead_items")
        if "idx_lead_items_user" in indexes:
            op.drop_index("idx_lead_items_user", table_name="lead_items")
        op.drop_table("lead_items")
