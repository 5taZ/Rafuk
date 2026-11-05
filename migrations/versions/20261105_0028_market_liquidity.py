"""Store observations of comparable listings on Kufar."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0028"
down_revision: str | Sequence[str] | None = "20261021_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_liquidity_observations",
        sa.Column("ad_id", sa.BigInteger(), autoincrement=False, primary_key=True),
        sa.Column("segment", sa.String(64), nullable=False),
        sa.Column("price_byn", sa.Numeric(12, 2), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("missing_since", sa.DateTime(timezone=True)),
        sa.Column("available", sa.Boolean()),
    )
    op.create_index(
        "idx_market_liquidity_first_seen", "market_liquidity_observations", ["first_seen_at"],
    )
    op.create_index(
        "idx_market_liquidity_segment_seen", "market_liquidity_observations",
        ["segment", "first_seen_at"],
    )


def downgrade() -> None:
    op.drop_table("market_liquidity_observations")
