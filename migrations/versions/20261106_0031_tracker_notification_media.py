"""Keep listing media and delivery progress in the notification outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_0031"
down_revision: str | Sequence[str] | None = "20261105_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_notification_dlq", sa.Column("listing_payload", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_notification_dlq", "listing_payload")
