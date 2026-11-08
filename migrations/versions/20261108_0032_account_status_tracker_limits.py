"""Configure concurrent tracker limits per account status."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_0032"
down_revision: str | Sequence[str] | None = "20261106_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("account_statuses", sa.Column(
        "max_trackers", sa.Integer(), nullable=False, server_default="10",
    ))
    statuses = sa.table(
        "account_statuses", sa.column("code", sa.String(32)),
        sa.column("max_trackers", sa.Integer()),
    )
    op.execute(statuses.update().where(statuses.c.code == "bare_search").values(max_trackers=3))
    op.create_check_constraint(
        "chk_account_statuses_max_trackers_bounds", "account_statuses",
        "max_trackers >= 0 AND max_trackers <= 10000",
    )


def downgrade() -> None:
    op.drop_constraint(
        "chk_account_statuses_max_trackers_bounds", "account_statuses", type_="check",
    )
    op.drop_column("account_statuses", "max_trackers")
