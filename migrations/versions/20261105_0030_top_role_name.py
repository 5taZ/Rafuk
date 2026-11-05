"""Rename the highest built-in level to Pro Max Ultra Max."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0030"
down_revision: str | Sequence[str] | None = "20261105_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rename(before: str, after: str) -> None:
    statuses = sa.table(
        "account_statuses", sa.column("code", sa.String(32)),
        sa.column("display_name", sa.String(64)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(statuses.update().where(
        statuses.c.code == "market_maker", statuses.c.display_name == before,
    ).values(display_name=after, updated_at=sa.func.now()))


def upgrade() -> None:
    _rename("Pro Max Ultra Duo", "Pro Max Ultra Max")


def downgrade() -> None:
    _rename("Pro Max Ultra Max", "Pro Max Ultra Duo")
