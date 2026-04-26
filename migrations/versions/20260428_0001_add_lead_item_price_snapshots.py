"""Add lead_item_price_snapshots table for sparkline / price-trend charts

Adds a thin (id, lead_item_id, price_byn, snapped_at) table that
captures one row every time a price refresh sees a change vs. the
previous snapshot. Used by the watchlist sparkline in the Mini App
and by future price-trend charts on the deal-detail screen.

Cascade-deletes with the parent lead_items row so a deleted lot
doesn't leave orphan snapshots behind.

Revision ID: 20260428_0001
Revises: 20260427_0001
Create Date: 2026-04-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260428_0001"
down_revision: str | None = "20260427_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_item_price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "lead_item_id",
            sa.Integer(),
            sa.ForeignKey("lead_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price_byn", sa.Float(), nullable=False),
        sa.Column(
            "snapped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    # Composite (lead_item_id, snapped_at) index — covers the
    # "give me the last N points for this row" query the sparkline
    # uses, and the bulk fetch in the watchlist endpoint.
    op.create_index(
        "idx_lead_item_price_snapshots_lookup",
        "lead_item_price_snapshots",
        ["lead_item_id", "snapped_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_lead_item_price_snapshots_lookup",
        table_name="lead_item_price_snapshots",
    )
    op.drop_table("lead_item_price_snapshots")
