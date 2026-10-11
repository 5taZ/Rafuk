"""add users table, deleted_at columns and the tracker_events.tracker_id FK

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
                "created_at", DateTime(timezone=True), nullable=False, server_default=sa.func.now()
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

    # Step 3: TrackerEvent.tracker_id -> Tracker.id (PostgreSQL only: SQLite has no
    # ALTER TABLE ADD CONSTRAINT). The user_id FKs are not created here: user_id holds the
    # Telegram ID and users is still empty, so an FK to users.id rejected every existing row
    # and failed the upgrade. 20261011_0010 backfills users and adds FKs to
    # users.telegram_user_id instead.
    if not is_sqlite and "tracker_events" in tables:
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

    # Drop FK constraints (PostgreSQL only), only those that still exist. The user_id FKs
    # exist only on databases migrated by an earlier version of this revision.
    if not is_sqlite:
        fk_names = {
            "tracker_events": ("fk_tracker_events_tracker_id", "fk_tracker_events_user_id"),
            "trackers": ("fk_trackers_user_id",),
            "saved_searches": ("fk_saved_searches_user_id",),
            "lead_items": ("fk_lead_items_user_id",),
            "watchlist_items": ("fk_watchlist_items_user_id",),
        }
        for table_name, names in fk_names.items():
            if table_name not in tables:
                continue
            existing = {fk["name"] for fk in inspector.get_foreign_keys(table_name) if fk["name"]}
            for name in names:
                if name in existing:
                    op.drop_constraint(name, table_name, type_="foreignkey")

    # idx_tracker_events_tracker_id is owned by 0008 (step 4 only backfills it), so the
    # 0008 downgrade drops it. Dropping it here too made 0008 -> 0007 fail.

    # Drop deleted_at columns
    for table_name in ["trackers", "saved_searches"]:
        if table_name in tables:
            columns = {col["name"] for col in inspector.get_columns(table_name)}
            if "deleted_at" in columns:
                op.drop_column(table_name, "deleted_at")

    # Drop users table
    if "users" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("users")}
        if "idx_users_telegram_id" in indexes:
            op.drop_index("idx_users_telegram_id", table_name="users")
        op.drop_table("users")
