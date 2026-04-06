"""add history snapshots and price tracking

Revision ID: 20260406_0002
Revises: 20260405_0001
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260406_0002"
down_revision = "20260405_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    tracker_columns = {column["name"] for column in inspector.get_columns("trackers")}
    if "last_seen_price_byn" not in tracker_columns:
        op.add_column("trackers", sa.Column("last_seen_price_byn", sa.Float(), nullable=True))
    if "last_checked_at" not in tracker_columns:
        op.add_column(
            "trackers",
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "query_snapshots" not in tables:
        op.create_table(
            "query_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("query", sa.String(length=255), nullable=False),
            sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("total_results", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("analyzed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mean_byn", sa.Float(), nullable=False, server_default="0"),
            sa.Column("median_byn", sa.Float(), nullable=False, server_default="0"),
            sa.Column("min_byn", sa.Float(), nullable=False, server_default="0"),
            sa.Column("max_byn", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("query", "snapshot_at", name="uq_query_snapshot_bucket"),
        )
    inspector = sa.inspect(bind)
    snapshot_indexes = {index["name"] for index in inspector.get_indexes("query_snapshots")}
    if "idx_query_snapshots_query" not in snapshot_indexes:
        op.create_index("idx_query_snapshots_query", "query_snapshots", ["query"])

    if "query_listing_states" not in tables:
        op.create_table(
            "query_listing_states",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("query", sa.String(length=255), nullable=False),
            sa.Column("ad_id", sa.BigInteger(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("link", sa.String(length=512), nullable=False),
            sa.Column("last_price_byn", sa.Float(), nullable=True),
            sa.Column("list_time", sa.String(length=64), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("query", "ad_id", name="uq_query_listing_state"),
        )
    inspector = sa.inspect(bind)
    listing_indexes = {index["name"] for index in inspector.get_indexes("query_listing_states")}
    if "idx_query_listing_states_query" not in listing_indexes:
        op.create_index("idx_query_listing_states_query", "query_listing_states", ["query"])
    if "idx_query_listing_states_active" not in listing_indexes:
        op.create_index("idx_query_listing_states_active", "query_listing_states", ["active"])


def downgrade() -> None:
    op.drop_index("idx_query_listing_states_active", table_name="query_listing_states")
    op.drop_index("idx_query_listing_states_query", table_name="query_listing_states")
    op.drop_table("query_listing_states")

    op.drop_index("idx_query_snapshots_query", table_name="query_snapshots")
    op.drop_table("query_snapshots")

    op.drop_column("trackers", "last_checked_at")
    op.drop_column("trackers", "last_seen_price_byn")
