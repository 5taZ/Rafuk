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

        # Lead items: status
        if "lead_items" in tables:
            constraints = [
                constraint["name"]
                for constraint in inspector.get_check_constraints("lead_items")
                if constraint["name"]
            ]
            if "chk_lead_items_status" not in constraints:
                op.create_check_constraint(
                    "chk_lead_items_status",
                    "lead_items",
                    "status IN ('new', 'reviewing', 'in_progress', 'negotiating', 'deferred', 'closed', 'abandoned')",
                )

        # Watchlist items: workflow_status
        if "watchlist_items" in tables:
            constraints = [
                constraint["name"]
                for constraint in inspector.get_check_constraints("watchlist_items")
                if constraint["name"]
            ]
            if "chk_watchlist_items_workflow_status" not in constraints:
                op.create_check_constraint(
                    "chk_watchlist_items_workflow_status",
                    "watchlist_items",
                    "workflow_status IN ('watching', 'lead', 'deferred', 'archived')",
                )

            # Watchlist items: market_status
            if "chk_watchlist_items_market_status" not in constraints:
                op.create_check_constraint(
                    "chk_watchlist_items_market_status",
                    "watchlist_items",
                    "market_status IN ('active', 'missing', 'price_drop', 'duplicate')",
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    is_sqlite = bind.dialect.name == "sqlite"

    # Drop CHECK constraints (PostgreSQL only)
    if not is_sqlite:
        if "tracker_events" in tables:
            op.drop_constraint(
                "chk_tracker_events_event_type",
                "tracker_events",
                type_="check",
            )

        if "lead_items" in tables:
            op.drop_constraint(
                "chk_lead_items_status",
                "lead_items",
                type_="check",
            )

        if "watchlist_items" in tables:
            op.drop_constraint(
                "chk_watchlist_items_workflow_status",
                "watchlist_items",
                type_="check",
            )
            op.drop_constraint(
                "chk_watchlist_items_market_status",
                "watchlist_items",
                type_="check",
            )

    # Drop composite indexes
    if "tracker_events" in tables:
        op.drop_index("idx_tracker_events_user_created", table_name="tracker_events")
        op.drop_index("idx_tracker_events_tracker_id", table_name="tracker_events")

    if "trackers" in tables:
        op.drop_index("idx_trackers_user_active", table_name="trackers")

    if "saved_searches" in tables:
        op.drop_index("idx_saved_searches_user_active", table_name="saved_searches")

    if "lead_items" in tables:
        op.drop_index("idx_lead_items_user_status", table_name="lead_items")

    if "watchlist_items" in tables:
        op.drop_index("idx_watchlist_items_user_market", table_name="watchlist_items")

    if "query_listing_states" in tables:
        op.drop_index(
            "idx_query_listing_states_query_active",
            table_name="query_listing_states",
        )

    if "query_snapshots" in tables:
        op.drop_index("idx_query_snapshots_query_time", table_name="query_snapshots")
