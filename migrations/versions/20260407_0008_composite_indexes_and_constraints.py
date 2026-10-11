"""add composite indexes and improve database integrity

Revision ID: 20260407_0008
Revises: 20260406_0007
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260407_0008"
down_revision = "20260406_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    is_sqlite = bind.dialect.name == "sqlite"

    # Add composite indexes for common query patterns
    # These replace single-column indexes for better performance

    # Trackers: user_id + active (most common query pattern)
    if "trackers" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("trackers")}
        if "idx_trackers_user_active" not in indexes:
            # Partial index for active trackers only (PostgreSQL only)
            if is_sqlite:
                # SQLite doesn't support partial indexes
                op.create_index(
                    "idx_trackers_user_active",
                    "trackers",
                    ["user_id", "active"],
                )
            else:
                op.create_index(
                    "idx_trackers_user_active",
                    "trackers",
                    ["user_id", "active"],
                    postgresql_where=sa.text("active = true"),
                )

    # Saved searches: user_id + active
    if "saved_searches" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("saved_searches")}
        if "idx_saved_searches_user_active" not in indexes:
            if is_sqlite:
                op.create_index(
                    "idx_saved_searches_user_active",
                    "saved_searches",
                    ["user_id", "active"],
                )
            else:
                op.create_index(
                    "idx_saved_searches_user_active",
                    "saved_searches",
                    ["user_id", "active"],
                    postgresql_where=sa.text("active = true"),
                )

    # Lead items: user_id + status (primary filter pattern)
    if "lead_items" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("lead_items")}
        if "idx_lead_items_user_status" not in indexes:
            op.create_index(
                "idx_lead_items_user_status",
                "lead_items",
                ["user_id", "status"],
            )

    # Watchlist: user_id + market_status
    if "watchlist_items" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("watchlist_items")}
        if "idx_watchlist_items_user_market" not in indexes:
            op.create_index(
                "idx_watchlist_items_user_market",
                "watchlist_items",
                ["user_id", "market_status"],
            )

    # Tracker events: user_id + created_at DESC (ORDER BY pattern)
    if "tracker_events" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("tracker_events")}
        if "idx_tracker_events_user_created" not in indexes:
            op.create_index(
                "idx_tracker_events_user_created",
                "tracker_events",
                ["user_id", "created_at"],
            )

        # Tracker events: tracker_id index (missing join index)
        if "idx_tracker_events_tracker_id" not in indexes:
            op.create_index(
                "idx_tracker_events_tracker_id",
                "tracker_events",
                ["tracker_id"],
            )

    # Query listing states: query + active composite
    if "query_listing_states" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("query_listing_states")}
        if "idx_query_listing_states_query_active" not in indexes:
            if is_sqlite:
                op.create_index(
                    "idx_query_listing_states_query_active",
                    "query_listing_states",
                    ["query"],
                )
            else:
                op.create_index(
                    "idx_query_listing_states_query_active",
                    "query_listing_states",
                    ["query"],
                    postgresql_where=sa.text("active = true"),
                )

    # Query snapshots: query + snapshot_at (time-range queries)
    if "query_snapshots" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("query_snapshots")}
        if "idx_query_snapshots_query_time" not in indexes:
            op.create_index(
                "idx_query_snapshots_query_time",
                "query_snapshots",
                ["query", "snapshot_at"],
            )

    # Add CHECK constraints for status/enum columns (PostgreSQL only)
    # SQLite doesn't support adding constraints via ALTER TABLE
    # No CHECKs on lead_items.status and watchlist_items.workflow_status here: the lists this
    # revision used to add rejected statuses the app already wrote (bought, sold, skipped...),
    # so upgrading such a database failed here. 20261011_0010 adds the canonical CHECKs after
    # rewriting the rows.
    if not is_sqlite:
        # Tracker events: event_type
        if "tracker_events" in tables:
            constraints = [
                constraint["name"]
                for constraint in inspector.get_check_constraints("tracker_events")
                if constraint["name"]
            ]
            if "chk_tracker_events_event_type" not in constraints:
                op.create_check_constraint(
                    "chk_tracker_events_event_type",
                    "tracker_events",
                    "event_type IN ('new_listing', 'price_drop')",
                )

        # Watchlist items: market_status
        if "watchlist_items" in tables:
            constraints = [
                constraint["name"]
                for constraint in inspector.get_check_constraints("watchlist_items")
                if constraint["name"]
            ]
            if "chk_watchlist_items_market_status" not in constraints:
                op.create_check_constraint(
                    "chk_watchlist_items_market_status",
                    "watchlist_items",
                    "market_status IN ('active', 'missing', 'price_drop', 'duplicate')",
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    is_sqlite = bind.dialect.name == "sqlite"

    # Every drop is guarded: the objects may be missing on databases created with
    # create_all, or already dropped by a later revision. The two status CHECKs exist only on
    # databases migrated before upgrade() stopped adding them.

    # Drop CHECK constraints (PostgreSQL only)
    if not is_sqlite:
        checks = {
            "tracker_events": ("chk_tracker_events_event_type",),
            "lead_items": ("chk_lead_items_status",),
            "watchlist_items": (
                "chk_watchlist_items_workflow_status",
                "chk_watchlist_items_market_status",
            ),
        }
        for table_name, names in checks.items():
            if table_name not in tables:
                continue
            existing = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(table_name)
                if constraint["name"]
            }
            for name in names:
                if name in existing:
                    op.drop_constraint(name, table_name, type_="check")

    # Drop composite indexes
    composite_indexes = {
        "tracker_events": ("idx_tracker_events_user_created", "idx_tracker_events_tracker_id"),
        "trackers": ("idx_trackers_user_active",),
        "saved_searches": ("idx_saved_searches_user_active",),
        "lead_items": ("idx_lead_items_user_status",),
        "watchlist_items": ("idx_watchlist_items_user_market",),
        "query_listing_states": ("idx_query_listing_states_query_active",),
        "query_snapshots": ("idx_query_snapshots_query_time",),
    }
    for table_name, names in composite_indexes.items():
        if table_name not in tables:
            continue
        existing = {index["name"] for index in inspector.get_indexes(table_name)}
        for name in names:
            if name in existing:
                op.drop_index(name, table_name=table_name)
