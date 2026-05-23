"""Convert Float monetary columns to Numeric(12,2) for precision

Revision ID: 20260429_0002
Revises: 20260429_0001
Create Date: 2026-04-29

All price/discount columns that previously used IEEE 754 Float are
converted to NUMERIC(12,2) (or NUMERIC(5,2) for percentages) to
eliminate silent rounding errors in monetary calculations.
PostgreSQL can cast float → numeric automatically.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260429_0002"
down_revision: str | None = "20260429_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _alter_column(table: str, column: str, new_type: str) -> None:
    op.execute(
        f'ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}'
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite doesn't support ALTER COLUMN TYPE; numeric affinity
        # handles precision transparently, so this is a no-op.
        return

    # TrackerFiltersMixin columns (trackers table)
    _alter_column("trackers", "min_discount_percent", "NUMERIC(5,2)")
    _alter_column("trackers", "max_price_byn", "NUMERIC(12,2)")
    _alter_column("trackers", "alert_price_threshold", "NUMERIC(12,2)")
    _alter_column("trackers", "alert_discount_percent", "NUMERIC(5,2)")

    # Tracker.last_seen_price_byn
    _alter_column("trackers", "last_seen_price_byn", "NUMERIC(12,2)")

    # QuerySnapshot price columns
    _alter_column("query_snapshots", "mean_byn", "NUMERIC(12,2)")
    _alter_column("query_snapshots", "median_byn", "NUMERIC(12,2)")
    _alter_column("query_snapshots", "min_byn", "NUMERIC(12,2)")
    _alter_column("query_snapshots", "max_byn", "NUMERIC(12,2)")

    # QueryListingState.last_price_byn
    _alter_column("query_listing_states", "last_price_byn", "NUMERIC(12,2)")

    # TrackerEvent price columns
    _alter_column("tracker_events", "price_byn", "NUMERIC(12,2)")
    _alter_column("tracker_events", "delta_byn", "NUMERIC(12,2)")

    # LeadItem price columns (buy_price_byn and sold_price_byn already Numeric)
    _alter_column("lead_items", "price_byn", "NUMERIC(12,2)")
    _alter_column("lead_items", "target_resale_byn", "NUMERIC(12,2)")
    _alter_column("lead_items", "initial_price_byn", "NUMERIC(12,2)")
    _alter_column("lead_items", "market_median_byn", "NUMERIC(12,2)")

    # LeadItemPriceSnapshot.price_byn
    _alter_column("lead_item_price_snapshots", "price_byn", "NUMERIC(12,2)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # Revert all columns back to double precision
    columns_to_float = [
        ("trackers", "min_discount_percent"),
        ("trackers", "max_price_byn"),
        ("trackers", "alert_price_threshold"),
        ("trackers", "alert_discount_percent"),
        ("trackers", "last_seen_price_byn"),
        ("query_snapshots", "mean_byn"),
        ("query_snapshots", "median_byn"),
        ("query_snapshots", "min_byn"),
        ("query_snapshots", "max_byn"),
        ("query_listing_states", "last_price_byn"),
        ("tracker_events", "price_byn"),
        ("tracker_events", "delta_byn"),
        ("lead_items", "price_byn"),
        ("lead_items", "target_resale_byn"),
        ("lead_items", "initial_price_byn"),
        ("lead_items", "market_median_byn"),
        ("lead_item_price_snapshots", "price_byn"),
    ]
    for table, column in columns_to_float:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE double precision'
        )
