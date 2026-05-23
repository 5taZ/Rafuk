"""Wave 176: compound indexes for tracker_events and lead_items list endpoints.

PERF-NEW-1: (user_id, created_at, id) on tracker_events — covers the
WHERE user_id=? ORDER BY created_at DESC, id DESC pattern used by
/tracker-events. Plain column order (no DESC keyword) because SQLite
does not support DESC in CREATE INDEX column lists via Alembic's
op.create_index; Postgres uses the index for both ASC and DESC scans
on a B-tree, and the Postgres-specific covering index from
20260509_0001 (idx_tracker_events_user_created_id) already provides
the optimal DESC layout for production.

PERF-NEW-2: (user_id, status, updated_at) on lead_items — covers
WHERE user_id=? AND status=? ORDER BY updated_at DESC used by /leads
and /watchlist.

Revision ID: 20260519_0025
Revises: 20260518_0024
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260519_0025"
down_revision: str | Sequence[str] | None = "20260518_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PERF-NEW-1: /tracker-events filters by user_id and orders by created_at DESC.
    op.create_index(
        "idx_tracker_events_user_created",
        "tracker_events",
        ["user_id", "created_at", "id"],
    )
    # PERF-NEW-2: /leads and /watchlist filter by user_id+status and order by updated_at DESC.
    op.create_index(
        "idx_lead_items_user_status_updated",
        "lead_items",
        ["user_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_lead_items_user_status_updated", table_name="lead_items")
    op.drop_index("idx_tracker_events_user_created", table_name="tracker_events")
