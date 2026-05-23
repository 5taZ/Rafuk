"""Add lead_reminders table

Revision ID: 20260501_0001
Revises: 20260430_0001
Create Date: 2026-05-01

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260501_0001"
down_revision: str | Sequence[str] | None = "20260430_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_reminders",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "remind_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("message", sa.String(255), nullable=True),
        sa.Column(
            "sent",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["lead_items.id"],
            name="fk_lead_reminders_lead_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_lead_reminders_user_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_lead_reminders_lead_id",
        "lead_reminders",
        ["lead_id"],
    )
    op.create_index(
        "ix_lead_reminders_user_id",
        "lead_reminders",
        ["user_id"],
    )
    op.create_index(
        "ix_lead_reminders_remind_at",
        "lead_reminders",
        ["remind_at"],
    )
    op.create_index(
        "idx_reminders_due",
        "lead_reminders",
        ["remind_at", "sent"],
    )


def downgrade() -> None:
    op.drop_index("idx_reminders_due", table_name="lead_reminders")
    op.drop_index("ix_lead_reminders_remind_at", table_name="lead_reminders")
    op.drop_index("ix_lead_reminders_user_id", table_name="lead_reminders")
    op.drop_index("ix_lead_reminders_lead_id", table_name="lead_reminders")
    op.drop_table("lead_reminders")
