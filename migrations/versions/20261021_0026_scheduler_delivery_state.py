"""Scheduler delivery state: per-tracker price diffs, alert outbox, reminder lifecycle.

Revision ID: 20261021_0026
Revises: 20260519_0025

RV-SCH-01: ``query_listing_states.price_history`` keeps the last few price
changes of a listing so each tracker diffs against the price it saw at its
own last check, instead of the single previous price the first tracker on
the search key consumed. NULL = unchanged since first seen, so no backfill.

RV-SCH-02: ``telegram_notification_dlq.dedup_key`` — the table doubles as
the tracker-alert outbox, and the cross-tracker dedup that used to happen
in memory now happens against queued rows.

RV-SCH-04: ``lead_reminders`` gets a delivery lifecycle (``status``,
``attempts``, ``next_attempt_at``, ``last_error``). Existing rows map
``sent`` → ``'sent'`` and everything else → ``'pending'``.

All tables touched here are small or the new columns are nullable without
a default, so the ALTERs are metadata-only on PostgreSQL 11+; batch mode
keeps SQLite (local dev) working.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0026"
down_revision: str | Sequence[str] | None = "20260519_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMINDER_STATUSES = "status IN ('pending', 'sent', 'failed', 'cancelled')"


def upgrade() -> None:
    with op.batch_alter_table("query_listing_states") as batch_op:
        batch_op.add_column(sa.Column("price_history", sa.JSON(), nullable=True))

    with op.batch_alter_table("telegram_notification_dlq") as batch_op:
        batch_op.add_column(sa.Column("dedup_key", sa.String(128), nullable=True))

    with op.batch_alter_table("lead_reminders") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(16), nullable=False, server_default="pending")
        )
        batch_op.add_column(
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("last_error", sa.String(64), nullable=True))

    reminders = sa.table(
        "lead_reminders",
        sa.column("sent", sa.Boolean()),
        sa.column("status", sa.String()),
    )
    op.execute(reminders.update().where(reminders.c.sent.is_(True)).values(status="sent"))

    with op.batch_alter_table("lead_reminders") as batch_op:
        batch_op.create_check_constraint("chk_lead_reminders_status", _REMINDER_STATUSES)
        batch_op.create_index("idx_lead_reminders_status_due", ["status", "remind_at"])


def downgrade() -> None:
    # The old scheduler re-sends every ``sent = false`` reminder each tick;
    # mark the terminal ones as done so a downgrade does not replay them.
    reminders = sa.table(
        "lead_reminders",
        sa.column("sent", sa.Boolean()),
        sa.column("status", sa.String()),
    )
    op.execute(
        reminders.update()
        .where(reminders.c.status.in_(("failed", "cancelled")))
        .values(sent=True)
    )
    with op.batch_alter_table("lead_reminders") as batch_op:
        batch_op.drop_index("idx_lead_reminders_status_due")
        batch_op.drop_constraint("chk_lead_reminders_status", type_="check")
        batch_op.drop_column("last_error")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("attempts")
        batch_op.drop_column("status")

    with op.batch_alter_table("telegram_notification_dlq") as batch_op:
        batch_op.drop_column("dedup_key")

    with op.batch_alter_table("query_listing_states") as batch_op:
        batch_op.drop_column("price_history")
