"""B-06: Add q1_byn/q3_byn to query_snapshots.

Revision ID: 20260517_0022
Revises: 20260517_0021

Persists Q1/Q3 percentiles so /price-history can render the fair-price
band. Nullable — no backfill needed since the table is rolling.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260517_0022"
down_revision: str | Sequence[str] | None = "20260517_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("query_snapshots") as batch_op:
        batch_op.add_column(sa.Column("q1_byn", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("q3_byn", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("query_snapshots") as batch_op:
        batch_op.drop_column("q3_byn")
        batch_op.drop_column("q1_byn")
