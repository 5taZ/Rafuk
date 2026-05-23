"""Add bought_at to lead_items for hold-time computation.

Revision ID: 20260517_0021
Revises: 20260517_0020

E-FIND-02: a perekupshchik's hold time (days between purchase and
resale) is README-advertised but had no place to live — only
``created_at`` (when the lead row was inserted, often before the
buy) and ``sold_at`` were stored. Add a nullable
``bought_at: TIMESTAMPTZ`` populated by ``update_lead`` when the
status transitions into ``bought``. Existing leads stay with NULL
``bought_at`` and get the field populated on the next status flip.
Computed ``hold_time_days`` lives in the read schema, not the DB,
because it's trivially derivable.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260517_0021"
down_revision: str | Sequence[str] | None = "20260517_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("lead_items") as batch_op:
        batch_op.add_column(
            sa.Column("bought_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("lead_items") as batch_op:
        batch_op.drop_column("bought_at")
