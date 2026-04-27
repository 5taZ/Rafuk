"""Add ai_audit_log table for AI decision audit trail (Belarus Law No. 91-Z).

Revision ID: 20260427_0003
Revises: 20260427_0002
Create Date: 2026-04-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260427_0003"
down_revision: str | None = "20260427_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("ad_id", sa.String(32), nullable=True),
        sa.Column("query", sa.String(256), nullable=True),
        sa.Column("result_summary", sa.String(512), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_audit_user", "ai_audit_log", ["user_id"])
    op.create_index("idx_ai_audit_created", "ai_audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_ai_audit_created", table_name="ai_audit_log")
    op.drop_index("idx_ai_audit_user", table_name="ai_audit_log")
    op.drop_table("ai_audit_log")
