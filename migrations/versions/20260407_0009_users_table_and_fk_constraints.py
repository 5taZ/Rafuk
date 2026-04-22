"""add users table and FK constraints for referential integrity

Revision ID: 20260407_0009
Revises: 20260407_0008
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import BigInteger, DateTime, Integer, String

revision = "20260407_0009"
down_revision = "20260407_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    is_sqlite = bind.dialect.name == "sqlite"

    # Step 1: Create users table
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", Integer, primary_key=True, autoincrement=True),
            sa.Column("telegram_user_id", BigInteger, unique=True, nullable=False, index=True),
            sa.Column("first_name", String(128), nullable=False, server_default=""),
            sa.Column("username", String(128), nullable=True),
            sa.Column("is_bot", sa.Boolean, nullable=False, server_default="false"),
            sa.Column(
                "created_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("last_seen_at", DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_users_telegram_id", "users", ["telegram_user_id"])

    # Step 2: Add deleted_at column to tables with ActiveMixin
    tables_with_active = ["trackers", "saved_searches"]
    for table_name in tables_with_active:
        if table_name in tables:
            columns = {col["name"] for col in inspector.get_columns(table_name)}
            if "deleted_at" not in columns:
                op.add_column(
                    table_name,
                    sa.Column("deleted_at", DateTime(timezone=True), nullable=True),
                )

    # Step 3: Add FK constraints (PostgreSQL only — SQLite doesn't
    # support ALTER TABLE ADD CONSTRAINT)
    if not is_sqlite:
        # TrackerEvent.tracker_id -> Tracker.id
        if "tracker_events" in tables:
            fks = [fk["name"] for fk in inspector.get_foreign_keys("tracker_events") if fk["name"]]
            if "fk_tracker_events_tracker_id" not in fks:
                op.create_foreign_key(
                    "fk_tracker_events_tracker_id",
                    "tracker_events",
                    "trackers",
                    ["tracker_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

        # Tracker.user_id -> User.id
        if "trackers" in tables:
            fks = [fk["name"] for fk in inspector.get_foreign_keys("trackers") if fk["name"]]
            if "fk_trackers_user_id" not in fks:
                op.create_foreign_key(
                    "fk_trackers_user_id",
                    "trackers",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

        # SavedSearch.user_id -> User.id
        if "saved_searches" in tables:
            fks = [fk["name"] for fk in inspector.get_foreign_keys("saved_searches") if fk["name"]]
            if "fk_saved_searches_user_id" not in fks:
                op.create_foreign_key(
                    "fk_saved_searches_user_id",
                    "saved_searches",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

        # TrackerEvent.user_id -> User.id
        if "tracker_events" in tables:
            fks = [fk["name"] for fk in inspector.get_foreign_keys("tracker_events") if fk["name"]]
            if "fk_tracker_events_user_id" not in fks:
                op.create_foreign_key(
                    "fk_tracker_events_user_id",
                    "tracker_events",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

        # LeadItem.user_id -> User.id
        if "lead_items" in tables:
            fks = [fk["name"] for fk in inspector.get_foreign_keys("lead_items") if fk["name"]]
            if "fk_lead_items_user_id" not in fks:
                op.create_foreign_key(
                    "fk_lead_items_user_id",
                    "lead_items",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

        # WatchlistItem.user_id -> User.id
        if "watchlist_items" in tables:
            fks = [
                fk["name"] for fk in inspector.get_foreign_keys("watchlist_items") if fk["name"]
            ]
            if "fk_watchlist_items_user_id" not in fks:
                op.create_foreign_key(
                    "fk_watchlist_items_user_id",
                    "watchlist_items",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

    # Step 4: Add indexes for FK columns (helpful for CASCADE delete performance)
    if "tracker_events" in tables:
        indexes = {idx["name"] for idx in inspector.get_indexes("tracker_events")}
        if "idx_tracker_events_tracker_id" not in indexes:
            op.create_index("idx_tracker_events_tracker_id", "tracker_events", ["tracker_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    is_sqlite = bind.dialect.name == "sqlite"

    # Drop FK constraints (PostgreSQL only)
    if not is_sqlite:
        if "tracker_events" in tables:
            op.drop_constraint(
                "fk_tracker_events_tracker_id",
                "tracker_events",
                type_="foreignkey",
            )
            op.drop_constraint("fk_tracker_events_user_id", "tracker_events", type_="foreignkey")

        if "trackers" in tables:
            op.drop_constraint("fk_trackers_user_id", "trackers", type_="foreignkey")

        if "saved_searches" in tables:
            op.drop_constraint("fk_saved_searches_user_id", "saved_searches", type_="foreignkey")

        if "lead_items" in tables:
            op.drop_constraint("fk_lead_items_user_id", "lead_items", type_="foreignkey")

        if "watchlist_items" in tables:
            op.drop_constraint("fk_watchlist_items_user_id", "watchlist_items", type_="foreignkey")

    # Drop indexes
    if "tracker_events" in tables:
        op.drop_index("idx_tracker_events_tracker_id", table_name="tracker_events")

    # Drop deleted_at columns
    for table_name in ["trackers", "saved_searches"]:
        if table_name in tables:
            columns = {col["name"] for col in inspector.get_columns(table_name)}
            if "deleted_at" in columns:
                op.drop_column(table_name, "deleted_at")

    # Drop users table
    if "users" in tables:
        op.drop_index("idx_users_telegram_id", table_name="users")
        op.drop_table("users")
