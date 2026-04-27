"""Add user_consents table for PD processing and AI analysis consent tracking.

Revision ID: 20260427_0002
Revises: 20260428_0002
Create Date: 2026-04-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260427_0002"
down_revision: str | None = "20260428_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_consents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "consent_type",
            sa.String(32),
            nullable=False,
            comment="ai_analysis | pd_processing | cross_border",
        ),
        sa.Column(
            "version",
            sa.String(16),
            nullable=False,
            server_default="2026.1",
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_consents_user", "user_consents", ["user_id"])
    op.create_index("idx_user_consents_type", "user_consents", ["consent_type"])


def downgrade() -> None:
    op.drop_index("idx_user_consents_type", table_name="user_consents")
    op.drop_index("idx_user_consents_user", table_name="user_consents")
    op.drop_table("user_consents")
