"""Add retry_count + next_retry_at to telegram_notification_dlq

Revision ID: 20260513_0014
Revises: 20260513_0013

OPUS-2: turns the DLQ from a write-only graveyard into a real
retry queue. The pump in scheduler/collector uses
``next_retry_at`` for cheap dispatch and bumps ``retry_count``
with exponential back-off until it gives up at 5 attempts.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260513_0014"
down_revision: str | Sequence[str] | None = "20260513_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("telegram_notification_dlq") as batch_op:
        batch_op.add_column(
            sa.Column(
                "retry_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "idx_telegram_notification_dlq_pump",
            ["next_retry_at", "retry_count"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("telegram_notification_dlq") as batch_op:
        batch_op.drop_index("idx_telegram_notification_dlq_pump")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("retry_count")
