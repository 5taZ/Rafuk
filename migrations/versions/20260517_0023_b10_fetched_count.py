"""B-10: Add fetched_count to query_snapshots.

Revision ID: 20260517_0023
Revises: 20260517_0022

Raw pre-outlier sample size so the API can distinguish total_results
(Kufar's reported total), fetched_count (prices extracted before
outlier removal), and analyzed_count (post-outlier sample used for
stats). Nullable — no backfill needed.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260517_0023"
down_revision: str | Sequence[str] | None = "20260517_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("query_snapshots") as batch_op:
        batch_op.add_column(sa.Column("fetched_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("query_snapshots") as batch_op:
        batch_op.drop_column("fetched_count")
