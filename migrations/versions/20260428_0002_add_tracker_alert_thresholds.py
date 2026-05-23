"""Add alert_price_threshold + alert_discount_percent to trackers and saved_searches

Hard alerts run on top of the soft `min_discount_percent` filter:
crossing either threshold escalates a listing into its own
event_type (`price_threshold_alert` / `discount_alert`) so users
can build "ping me when ANY iPhone drops under 1500 BYN" workflows
on top of the existing tracker.

The columns live on the shared TrackerFiltersMixin, so both
`trackers` and `saved_searches` get them. SavedSearch just doesn't
read them — keeping the schema symmetric is cheaper than splitting
the mixin.

Revision ID: 20260428_0002
Revises: 20260428_0001
Create Date: 2026-04-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260428_0002"
down_revision: str | None = "20260428_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("trackers", "saved_searches"):
        op.add_column(
            table,
            sa.Column("alert_price_threshold", sa.Float(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("alert_discount_percent", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    for table in ("trackers", "saved_searches"):
        op.drop_column(table, "alert_discount_percent")
        op.drop_column(table, "alert_price_threshold")
