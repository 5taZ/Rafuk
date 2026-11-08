from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_role_migration_preserves_subscriptions_and_custom_tracker_limits() -> None:
    migration = importlib.import_module(
        "migrations.versions.20261108_0034_rolling_ai_limits_and_roles",
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    statuses = sa.Table(
        "account_statuses", metadata,
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("max_trackers", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime()),
    )
    users = sa.Table(
        "users", metadata, sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_status_code", sa.String(32)),
        sa.Column("status_expires_at", sa.String()), sa.Column("status_note", sa.String()),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(statuses.insert(), [
            {"code": code, "display_name": code, "sort_order": i, "max_trackers": limit}
            for i, (code, limit) in enumerate((
                ("bare_search", 3), ("scout", 8), ("flipper", 10),
                ("shark", 17), ("market_maker", 12),
            ))
        ])
        connection.execute(users.insert(), [
            {"id": i, "account_status_code": code,
             "status_expires_at": "2026-12-08", "status_note": "paid"}
            for i, code in enumerate(("shark", "market_maker", "flipper", "scout"), start=1)
        ])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = connection.execute(sa.text(
            "SELECT code, display_name, ai_5h_limit, ai_weekly_limit, max_trackers "
            "FROM account_statuses WHERE is_active ORDER BY sort_order",
        )).all()
        assert [(row[1], row[2], row[3]) for row in rows] == [
            ("Free", 0, 0), ("Pro", 20, 100), ("Max", 60, 350), ("Ultra", 150, 1000),
        ]
        assert {row[0]: row[4] for row in rows} == {
            "bare_search": 3, "scout": 8, "flipper": 10, "market_maker": 17,
        }
        assigned = connection.execute(sa.select(users).order_by(users.c.id)).all()
        assert [row.account_status_code for row in assigned] == [
            "market_maker", "market_maker", "flipper", "scout",
        ]
        assert all(row.status_expires_at == "2026-12-08" and row.status_note == "paid"
                   for row in assigned)
        assert connection.scalar(sa.text("SELECT count(*) FROM account_statuses")) == 5
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "ai_5h_limit" not in {column["name"] for column in sa.inspect(
            connection,
        ).get_columns("account_statuses")}
    engine.dispose()
