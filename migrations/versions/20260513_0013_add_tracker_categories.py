"""Add category scope to trackers

Revision ID: 20260513_0013
Revises: 20260512_0012

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260513_0013"
down_revision: str | Sequence[str] | None = "20260512_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("trackers") as batch_op:
        batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("category_label", sa.String(length=128), nullable=True))
        batch_op.create_index(
            "idx_trackers_query_category",
            ["query", "strict_mode", "category_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("trackers") as batch_op:
        batch_op.drop_index("idx_trackers_query_category")
        batch_op.drop_column("category_label")
        batch_op.drop_column("category_id")
