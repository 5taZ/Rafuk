from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError

from api.models import Base

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Config:
    from api.config import get_settings

    # migrations/env.py reads DATABASE_URL through get_settings().
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(ROOT / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def migrated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Config, sa.Engine]:
    db_path = tmp_path / "migrations.db"
    config = _alembic_config(monkeypatch, f"sqlite+aiosqlite:///{db_path.as_posix()}")
    engine = sa.create_engine(f"sqlite:///{db_path.as_posix()}")
    yield config, engine
    engine.dispose()


def _schema_diff(engine: sa.Engine) -> list:
    def include_object(obj, name, type_, reflected, compare_to) -> bool:
        # Migrations add foreign keys on Postgres only (see migrations/env.py).
        return type_ != "foreign_key_constraint"

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": include_object,
            },
        )
        return compare_metadata(context, Base.metadata)


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table)}


def _indexes(engine: sa.Engine, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(engine).get_indexes(table)}


def test_single_head_is_latest_revision() -> None:
    config = Config(str(ROOT / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["20261011_0010"]


def test_upgrade_head_matches_models(migrated_db: tuple[Config, sa.Engine]) -> None:
    config, engine = migrated_db
    command.upgrade(config, "head")

    assert _schema_diff(engine) == []
    assert "min_discount_percent" in _columns(engine, "saved_searches")
    # Redundant indexes are gone, the composite ones stay.
    assert "idx_users_telegram_id" not in _indexes(engine, "users")
    assert "idx_query_snapshots_query_time" not in _indexes(engine, "query_snapshots")
    assert "idx_lead_items_user" not in _indexes(engine, "lead_items")
    assert _indexes(engine, "query_listing_states") == {"idx_query_listing_states_active"}
    assert "idx_lead_items_user_status" in _indexes(engine, "lead_items")
    assert "idx_tracker_events_user_created" in _indexes(engine, "tracker_events")


def test_downgrade_base_and_upgrade_again(migrated_db: tuple[Config, sa.Engine]) -> None:
    config, engine = migrated_db
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert set(sa.inspect(engine).get_table_names()) <= {"alembic_version"}

    command.upgrade(config, "head")
    assert _schema_diff(engine) == []


def test_upgrade_normalizes_statuses_and_backfills_users(
    migrated_db: tuple[Config, sa.Engine],
) -> None:
    config, engine = migrated_db
    command.upgrade(config, "20260407_0009")
    telegram_id = 5_123_456_789  # above the int4 range on purpose
    with engine.begin() as connection:
        for ad_id, status in enumerate(("closed", "abandoned", "bought", "weird"), start=1):
            connection.execute(
                sa.text(
                    "INSERT INTO lead_items (user_id, ad_id, query, title, link, status) "
                    "VALUES (:user_id, :ad_id, 'q', 't', 'l', :status)"
                ),
                {"user_id": telegram_id, "ad_id": ad_id, "status": status},
            )
        for ad_id, status in enumerate(("lead", "archived", "deferred", "weird"), start=1):
            connection.execute(
                sa.text(
                    "INSERT INTO watchlist_items (user_id, ad_id, query, title, link, "
                    "workflow_status, market_status) "
                    "VALUES (:user_id, :ad_id, 'q', 't', 'l', :status, 'gone')"
                ),
                {"user_id": telegram_id + 1, "ad_id": ad_id, "status": status},
            )
        connection.execute(
            sa.text("INSERT INTO trackers (user_id, query) VALUES (:user_id, 'q')"),
            {"user_id": telegram_id},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        lead_statuses = (
            connection.execute(sa.text("SELECT status FROM lead_items ORDER BY ad_id"))
            .scalars()
            .all()
        )
        watchlist_rows = connection.execute(
            sa.text("SELECT workflow_status, market_status FROM watchlist_items ORDER BY ad_id")
        ).all()
        users = connection.execute(
            sa.text("SELECT telegram_user_id, first_name FROM users ORDER BY telegram_user_id")
        ).all()

    assert lead_statuses == ["sold", "skipped", "bought", "new"]
    assert [row[0] for row in watchlist_rows] == ["in_progress", "skipped", "deferred", "watching"]
    assert {row[1] for row in watchlist_rows} == {"active"}
    assert users == [(telegram_id, ""), (telegram_id + 1, "")]


def test_upgrade_drops_create_all_duplicates_and_downgrade_restores(
    migrated_db: tuple[Config, sa.Engine],
) -> None:
    config, engine = migrated_db
    command.upgrade(config, "20260407_0009")
    # Old local databases were built with create_all, whose index=True columns added
    # ix_* twins of the named idx_* indexes.
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE INDEX ix_trackers_user_id ON trackers (user_id)"))
        connection.execute(
            sa.text("CREATE INDEX ix_tracker_events_tracker_id ON tracker_events (tracker_id)")
        )

    command.upgrade(config, "head")
    assert "ix_trackers_user_id" not in _indexes(engine, "trackers")
    assert "idx_trackers_user" in _indexes(engine, "trackers")
    assert "ix_tracker_events_tracker_id" not in _indexes(engine, "tracker_events")
    assert "idx_tracker_events_tracker_id" in _indexes(engine, "tracker_events")

    command.downgrade(config, "20260407_0009")
    assert "min_discount_percent" not in _columns(engine, "saved_searches")
    assert "idx_users_telegram_id" in _indexes(engine, "users")
    assert {"idx_query_listing_states_query", "idx_query_listing_states_query_active"} <= (
        _indexes(engine, "query_listing_states")
    )


def test_downgrade_through_0008_keeps_working(migrated_db: tuple[Config, sa.Engine]) -> None:
    config, engine = migrated_db
    command.upgrade(config, "head")
    command.downgrade(config, "20260407_0008")
    command.downgrade(config, "20260406_0007")
    indexes = _indexes(engine, "tracker_events")
    assert "idx_tracker_events_tracker_id" not in indexes
    assert "idx_tracker_events_user" in indexes


def test_env_escapes_percent_in_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A "%" in the URL (URL-encoded password) used to break ConfigParser interpolation.
    db_dir = tmp_path / "with%40percent"
    db_dir.mkdir()
    db_path = db_dir / "migrations.db"
    config = _alembic_config(monkeypatch, f"sqlite+aiosqlite:///{db_path.as_posix()}")
    command.upgrade(config, "20260405_0001")
    assert db_path.exists()


def test_offline_mode_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _alembic_config(monkeypatch, f"sqlite+aiosqlite:///{(tmp_path / 'x.db').as_posix()}")
    with pytest.raises(CommandError, match="Offline mode"):
        command.upgrade(config, "head", sql=True)
