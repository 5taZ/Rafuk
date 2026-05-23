"""Expand tracker event links to 2048 chars

Revision ID: 20260512_0012
Revises: 20260512_0011

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260512_0012"
down_revision: str | Sequence[str] | None = "20260512_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tracker_events") as batch_op:
        batch_op.alter_column(
            "link",
            existing_type=sa.String(length=512),
            type_=sa.String(length=2048),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tracker_events") as batch_op:
        batch_op.alter_column(
            "link",
            existing_type=sa.String(length=2048),
            type_=sa.String(length=512),
            existing_nullable=False,
        )
