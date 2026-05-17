"""Preserve inline keyboard across DLQ retries.

Revision ID: 20260517_0019
Revises: 20260514_0018

AUDIT-LOW (audit follow-up): the Telegram notification DLQ used to
store only the message text, so a transient send failure that landed
in the DLQ would replay as a plain text message — the inline keyboard
("\ud83d\udcccВ покупки", "\u2b50В Избранное", "\ud83d\udd17 Открыть", or the trend-alert
"Открыть запрос" / "Открыть лот" pair) was silently dropped on retry,
leaving the user with a non-actionable message they couldn't recover.

Add a ``reply_markup_json`` text column to carry an aiogram
``InlineKeyboardMarkup`` serialized via Pydantic ``.model_dump_json()``.
The collector ``_queue_notification_dlq`` writes it; the retry pump
``retry_telegram_notification_dlq`` deserializes via
``InlineKeyboardMarkup.model_validate_json`` before resending. Nullable
because not every DLQ row carries a keyboard (notify_user calls
without one stay valid; threshold/listing alerts get the markup
restored).

The column is plain text rather than a JSON type because (a) SQLite
doesn't have a JSON type the same way Postgres does and we use both,
(b) the value is always shaped by aiogram's own schema so we don't
benefit from JSON-side validation. Length is unbounded because some
keyboards have many rows; in practice the payload stays well under
4 KB.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260517_0019"
down_revision: str | Sequence[str] | None = "20260514_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("telegram_notification_dlq") as batch_op:
        batch_op.add_column(
            sa.Column("reply_markup_json", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("telegram_notification_dlq") as batch_op:
        batch_op.drop_column("reply_markup_json")
