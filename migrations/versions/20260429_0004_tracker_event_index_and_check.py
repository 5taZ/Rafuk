"""Add composite index and CHECK constraint on tracker_events

Revision ID: 20260429_0004
Revises: 20260429_0003
Create Date: 2026-04-29

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260429_0004"
down_revision: str | Sequence[str] | None = "20260429_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    # Composite index for scheduler queries that filter by tracker_id
    # and sort by created_at.
    indexes = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :name"
        ),
        {"name": "idx_tracker_events_tracker_created"},
    ).scalar()
    if not indexes:
        op.create_index(
            "idx_tracker_events_tracker_created",
            "tracker_events",
            ["tracker_id", "created_at"],
        )
    # NOTE: The CHECK constraint chk_tracker_events_event_type is already
    # created by migration 20260429_0001 (with 3 values including
    # trend_reversal). No need to recreate it here.


def downgrade() -> None:
    conn = op.get_bind()
    indexes = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :name"
        ),
        {"name": "idx_tracker_events_tracker_created"},
    ).scalar()
    if indexes:
        op.drop_index("idx_tracker_events_tracker_created", table_name="tracker_events")
