"""Use the Free / Pro names for the five built-in account levels."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0029"
down_revision: str | Sequence[str] | None = "20261105_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMES = (
    ("bare_search", "Голый Поиск", "Free"),
    ("scout", "Скаут Барахолки", "Pro"),
    ("flipper", "Флиппер", "Pro Max"),
    ("shark", "Куфарная Акула", "Pro Max Ultra"),
    ("market_maker", "Имба Маркетмейкер", "Pro Max Ultra Duo"),
)


def _rename(*, reverse: bool = False) -> None:
    statuses = sa.table(
        "account_statuses", sa.column("code", sa.String(32)),
        sa.column("display_name", sa.String(64)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    # Owners can edit catalogue labels: preserve those overrides in either direction.
    for code, old, new in _NAMES:
        before, after = (new, old) if reverse else (old, new)
        op.execute(statuses.update().where(
            statuses.c.code == code, statuses.c.display_name == before,
        ).values(display_name=after, updated_at=sa.func.now()))


def upgrade() -> None:
    _rename()


def downgrade() -> None:
    _rename(reverse=True)
