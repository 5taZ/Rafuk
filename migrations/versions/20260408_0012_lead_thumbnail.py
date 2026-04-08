"""add thumbnail column to lead_items

Revision ID: 20260408_0012
Revises: 20260407_0011
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260408_0012"
down_revision = "20260407_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "lead_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("lead_items")}
        if "thumbnail" not in columns:
            op.add_column(
                "lead_items",
                sa.Column("thumbnail", sa.String(512), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "lead_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("lead_items")}
        if "thumbnail" in columns:
            op.drop_column("lead_items", "thumbnail")
