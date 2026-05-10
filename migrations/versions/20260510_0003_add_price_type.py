"""Add price_type column to query_listing_states and tracker_events

Distinguishes "negotiable" (price_byn=0, no free keywords) from "free"
(price_byn=0, contains free keywords like "бесплатно", "даром").
The detection logic lives in api.services.aggregator.detect_price_type.

Revision ID: 20260510_0003
Revises: 20260509_0002
Create Date: 2026-05-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260510_0003"
down_revision: str | Sequence[str] | None = "20260509_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "query_listing_states",
        sa.Column("price_type", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "tracker_events",
        sa.Column("price_type", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tracker_events", "price_type")
    op.drop_column("query_listing_states", "price_type")
