"""Use four active roles and shared five-hour/seven-day AI budgets."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_0034"
down_revision: str | Sequence[str] | None = "20261108_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("account_statuses") as batch:
        batch.add_column(sa.Column(
            "ai_5h_limit", sa.Integer(), nullable=False, server_default="0",
        ))
        batch.add_column(sa.Column(
            "ai_weekly_limit", sa.Integer(), nullable=False, server_default="0",
        ))
        batch.add_column(sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true(),
        ))
        batch.create_check_constraint(
            "chk_account_statuses_ai_window_limit_bounds",
            "ai_5h_limit >= 0 AND ai_5h_limit <= 10000 "
            "AND ai_weekly_limit >= 0 AND ai_weekly_limit <= 100000",
        )
    statuses = sa.table(
        "account_statuses", sa.column("code", sa.String(32)),
        sa.column("display_name", sa.String(64)), sa.column("sort_order", sa.Integer()),
        sa.column("ai_5h_limit", sa.Integer()), sa.column("ai_weekly_limit", sa.Integer()),
        sa.column("is_active", sa.Boolean()), sa.column("max_trackers", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for code, name, short, weekly in (
        ("bare_search", "Free", 0, 0), ("scout", "Pro", 20, 100),
        ("flipper", "Max", 60, 350), ("market_maker", "Ultra", 150, 1000),
    ):
        op.execute(statuses.update().where(statuses.c.code == code).values(
            display_name=name, ai_5h_limit=short, ai_weekly_limit=weekly,
            is_active=True, updated_at=sa.func.now(),
        ))
    # Keep the retired row for history and rollback. Existing subscribers
    # retain their expiry/note and move to the surviving Ultra role.
    old_limit = sa.select(statuses.c.max_trackers).where(
        statuses.c.code == "shark",
    ).scalar_subquery()
    op.execute(statuses.update().where(
        statuses.c.code == "market_maker", statuses.c.max_trackers < old_limit,
    ).values(max_trackers=old_limit))
    users = sa.table("users", sa.column("account_status_code", sa.String(32)))
    op.execute(users.update().where(
        users.c.account_status_code == "shark",
    ).values(account_status_code="market_maker"))
    op.execute(statuses.update().where(statuses.c.code == "shark").values(
        is_active=False, ai_5h_limit=150, ai_weekly_limit=1000, updated_at=sa.func.now(),
    ))
    op.execute(statuses.update().where(
        statuses.c.code == "market_maker",
    ).values(sort_order=40))


def downgrade() -> None:
    statuses = sa.table(
        "account_statuses", sa.column("code", sa.String(32)),
        sa.column("display_name", sa.String(64)), sa.column("sort_order", sa.Integer()),
    )
    for code, name in (
        ("flipper", "Pro Max"), ("shark", "Pro Max Ultra"),
        ("market_maker", "Pro Max Ultra Max"),
    ):
        op.execute(statuses.update().where(statuses.c.code == code).values(display_name=name))
    op.execute(statuses.update().where(statuses.c.code == "market_maker").values(sort_order=50))
    with op.batch_alter_table("account_statuses") as batch:
        batch.drop_constraint("chk_account_statuses_ai_window_limit_bounds", type_="check")
        batch.drop_column("is_active")
        batch.drop_column("ai_weekly_limit")
        batch.drop_column("ai_5h_limit")
