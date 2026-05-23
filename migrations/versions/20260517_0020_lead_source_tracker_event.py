"""Expand chk_lead_items_source CHECK to allow 'tracker_event'

The Mini App tracker event cards emit source='tracker_event' when adding
a lead from a listing. The original constraint (installed in 20260509_0002)
only allowed 4 values; this migration adds the fifth.

Revision ID: 20260517_0020
Revises: 20260517_0019

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260517_0020"
down_revision: str | Sequence[str] | None = "20260517_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind is not None and bind.dialect.name != "postgresql":
        return

    op.execute("ALTER TABLE lead_items DROP CONSTRAINT IF EXISTS chk_lead_items_source")
    op.execute(
        "ALTER TABLE lead_items ADD CONSTRAINT chk_lead_items_source "
        "CHECK (source IN "
        "('manual', 'watchlist', 'detail_modal', 'bot_callback', 'tracker_event'))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind is not None and bind.dialect.name != "postgresql":
        return

    op.execute("ALTER TABLE lead_items DROP CONSTRAINT IF EXISTS chk_lead_items_source")
    op.execute(
        "ALTER TABLE lead_items ADD CONSTRAINT chk_lead_items_source "
        "CHECK (source IN "
        "('manual', 'watchlist', 'detail_modal', 'bot_callback'))"
    )
