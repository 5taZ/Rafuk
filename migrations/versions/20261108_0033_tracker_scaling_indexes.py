"""Index due tracker pagination and page-scoped event deduplication."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_0033"
down_revision: str | Sequence[str] | None = "20261108_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tracker events grow with every subscriber; keep writes available
    # while PostgreSQL builds the index on an existing installation.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_trackers_due", "trackers", ["last_checked_at", "id"],
            postgresql_where=sa.text("active IS TRUE AND paused IS FALSE"),
            sqlite_where=sa.text("active IS TRUE AND paused IS FALSE"),
            postgresql_concurrently=True, if_not_exists=True,
        )
        op.create_index(
            "idx_tracker_events_tracker_ad_created", "tracker_events",
            ["tracker_id", "ad_id", "created_at"],
            postgresql_concurrently=True, if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_tracker_events_tracker_ad_created", table_name="tracker_events",
            postgresql_concurrently=True,
        )
        op.drop_index("idx_trackers_due", table_name="trackers", postgresql_concurrently=True)
