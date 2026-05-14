"""Add AI audit text hash columns

Revision ID: 20260514_0018
Revises: 20260514_0017

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260514_0018"
down_revision: str | Sequence[str] | None = "20260514_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_audit_log", sa.Column("query_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "ai_audit_log",
        sa.Column("result_summary_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_audit_log", "result_summary_hash")
    op.drop_column("ai_audit_log", "query_hash")
