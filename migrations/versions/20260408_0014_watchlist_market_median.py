"""Add market_median_byn to watchlist_items

Revision ID: 20260408_0014
Revises: 20260408_0013
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260408_0014"
down_revision = "20260408_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "market_median_byn" not in columns:
            with op.batch_alter_table("watchlist_items") as batch_op:
                batch_op.add_column(sa.Column("market_median_byn", sa.Float, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "market_median_byn" in columns:
            with op.batch_alter_table("watchlist_items") as batch_op:
                batch_op.drop_column("market_median_byn")
