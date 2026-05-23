"""Add Telegram notification dead-letter queue

Revision ID: 20260511_0010
Revises: 20260511_0009

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_0010"
down_revision: str | Sequence[str] | None = "20260511_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_notification_dlq",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("message", sa.String(length=4096), nullable=False),
        sa.Column("error_kind", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_telegram_notification_dlq_created",
        "telegram_notification_dlq",
        ["created_at"],
    )
    op.create_index(
        "idx_telegram_notification_dlq_user",
        "telegram_notification_dlq",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_telegram_notification_dlq_user",
        table_name="telegram_notification_dlq",
    )
    op.drop_index(
        "idx_telegram_notification_dlq_created",
        table_name="telegram_notification_dlq",
    )
    op.drop_table("telegram_notification_dlq")
