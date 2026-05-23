"""add thumbnail column to watchlist_items

Revision ID: 20260407_0011
Revises: 20260407_0010
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260407_0011"
down_revision = "20260407_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "thumbnail" not in columns:
            op.add_column(
                "watchlist_items",
                sa.Column("thumbnail", sa.String(512), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "thumbnail" in columns:
            op.drop_column("watchlist_items", "thumbnail")
