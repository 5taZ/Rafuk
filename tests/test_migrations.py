from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from alembic.util import CommandError

from api.models import Base

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "migrations" / "versions"

# The status CHECKs that 20260407_0008 creates on Postgres (migrations skip them on SQLite).
LEGACY_CHECKS = {
    "lead_items": {
        "chk_lead_items_status": "status IN ('new', 'reviewing', 'in_progress', 'negotiating', "
        "'deferred', 'closed', 'abandoned')",
    },
    "watchlist_items": {
        "chk_watchlist_items_workflow_status": (
            "workflow_status IN ('watching', 'lead', 'deferred', 'archived')"
        ),
        "chk_watchlist_items_market_status": (
            "market_status IN ('active', 'missing', 'price_drop', 'duplicate')"
        ),
    },
}
USER_SCOPED_TABLES = (
    "trackers",
    "saved_searches",
    "tracker_events",
    "lead_items",
    "watchlist_items",
)


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


def _checks(engine: sa.Engine, table: str) -> dict[str, str]:
    return {
        check["name"]: check["sqltext"]
        for check in sa.inspect(engine).get_check_constraints(table)
    }


def _add_checks(engine: sa.Engine, table: str, checks: dict[str, str]) -> None:
    """Emulate Postgres CHECKs on SQLite: batch mode rebuilds the table with them."""
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(table, recreate="always") as batch:
            for name, condition in checks.items():
                batch.create_check_constraint(name, condition)


def _load_revision(prefix: str) -> ModuleType:
    (path,) = VERSIONS.glob(f"{prefix}_*.py")
    spec = importlib.util.spec_from_file_location(f"revision_{prefix}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordedOps:
    """Stands in for alembic's ``op``: records schema operations instead of running them."""

    def __init__(self, connection: sa.Connection, calls: list[tuple]) -> None:
        self._connection = connection
        self._calls = calls

    def get_bind(self) -> sa.Connection:
        return self._connection

    def __getattr__(self, name: str):
        def record(*args, **kwargs) -> None:
            self._calls.append((name, args, kwargs))

        return record


def _run_as_postgres(
    engine: sa.Engine,
    module: ModuleType,
    step: str,
    checks: dict[str, set[str]] | None = None,
) -> list[tuple]:
    """Run a revision's Postgres branches against SQLite.

    DDL goes to a recorder (SQLite cannot run it); data statements really execute and are
    recorded too, so tests can check what runs and in which order. ``checks`` names the CHECK
    constraints the revision should see as present, keyed by table.
    """
    calls: list[tuple] = []
    with pytest.MonkeyPatch.context() as patch, engine.begin() as connection:
        patch.setattr(connection.dialect, "name", "postgresql")
        execute = connection.execute

        def recording_execute(statement, *args, **kwargs):
            calls.append(("execute", (str(statement),), {}))
            return execute(statement, *args, **kwargs)

        patch.setattr(connection, "execute", recording_execute)
        patch.setattr(module, "op", _RecordedOps(connection, calls))
        if checks is not None:
            patch.setattr(module, "_check_names", lambda _bind, table: set(checks.get(table, ())))
        getattr(module, step)()
    return calls


def _position(calls: list[tuple], name: str, needle: str) -> int:
    for index, (call_name, args, _) in enumerate(calls):
        if call_name == name and any(needle in str(arg) for arg in args):
            return index
    raise AssertionError(f"no {name} call mentioning {needle!r} in {calls}")


def _rows(engine: sa.Engine, sql: str) -> list:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(sa.text(sql))]


def _insert_user_scoped_rows(engine: sa.Engine, telegram_id: int) -> None:
    """One row per user-scoped table as the pre-0009 code wrote them (user_id = Telegram ID)."""
    statements = (
        "INSERT INTO trackers (id, user_id, query, active) VALUES (1, :uid, 'kept', TRUE)",
        "INSERT INTO trackers (id, user_id, query, active) VALUES (2, :uid, 'untracked', FALSE)",
        "INSERT INTO saved_searches (user_id, name, query) VALUES (:uid2, 'n', 'q')",
        "INSERT INTO tracker_events (tracker_id, user_id, query, event_type, title, link) "
        "VALUES (1, :uid, 'kept', 'new_listing', 't', 'l')",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(sa.text(statement), {"uid": telegram_id, "uid2": telegram_id + 2})
        for ad_id, status in enumerate(("closed", "abandoned", "negotiating"), start=1):
            connection.execute(
                sa.text(
                    "INSERT INTO lead_items (user_id, ad_id, query, title, link, status) "
                    "VALUES (:uid, :ad_id, 'q', 't', 'l', :status)"
                ),
                {"uid": telegram_id, "ad_id": ad_id, "status": status},
            )
        for ad_id, status in enumerate(("lead", "archived", "deferred"), start=1):
            connection.execute(
                sa.text(
                    "INSERT INTO watchlist_items (user_id, ad_id, query, title, link, "
                    "workflow_status, market_status) "
                    "VALUES (:uid, :ad_id, 'q', 't', 'l', :status, 'missing')"
                ),
                {"uid": telegram_id + 1, "ad_id": ad_id, "status": status},
            )


def test_single_head_is_latest_revision() -> None:
    config = Config(str(ROOT / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["20261011_0011"]


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


def test_upgrade_from_0008_with_user_rows_and_legacy_checks(
    migrated_db: tuple[Config, sa.Engine],
) -> None:
    """A populated 0008 database (legacy statuses, 0008 CHECKs, empty users) reaches head."""
    config, engine = migrated_db
    command.upgrade(config, "20260407_0008")
    for table, checks in LEGACY_CHECKS.items():
        _add_checks(engine, table, checks)
    telegram_id = 5_123_456_789
    _insert_user_scoped_rows(engine, telegram_id)

    # Before the fix the status UPDATEs ran under the 0008 CHECKs and failed here.
    command.upgrade(config, "head")

    assert _rows(engine, "SELECT status FROM lead_items ORDER BY ad_id") == [
        ("sold",),
        ("skipped",),
        ("negotiating",),
    ]
    assert _rows(
        engine, "SELECT workflow_status, market_status FROM watchlist_items ORDER BY ad_id"
    ) == [("in_progress", "missing"), ("skipped", "missing"), ("deferred", "missing")]
    lead_checks = _checks(engine, "lead_items")
    assert "'sold'" in lead_checks["chk_lead_items_status"]
    assert "'closed'" not in lead_checks["chk_lead_items_status"]
    watchlist_checks = _checks(engine, "watchlist_items")
    assert "'in_progress'" in watchlist_checks["chk_watchlist_items_workflow_status"]
    assert "'lead'" not in watchlist_checks["chk_watchlist_items_workflow_status"]
    assert "chk_watchlist_items_market_status" in watchlist_checks
    assert _rows(engine, "SELECT telegram_user_id FROM users ORDER BY telegram_user_id") == [
        (telegram_id,),
        (telegram_id + 1,),
        (telegram_id + 2,),
    ]
    # 0011: the tracker /untrack had switched off is a deletion now, the active one is not.
    trackers = _rows(engine, "SELECT id, active, deleted_at IS NOT NULL FROM trackers")
    assert sorted(trackers) == [(1, 1, 0), (2, 0, 1)]
    assert _schema_diff(engine) == []
    # The rebuilt tables keep their rows, indexes and unique constraints.
    assert "idx_lead_items_user_status" in _indexes(engine, "lead_items")
    with engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO lead_items (user_id, ad_id, query, title, link, status) "
                "VALUES (:uid, 1, 'q', 't', 'l', 'new')"
            ),
            {"uid": telegram_id},
        )


def test_0009_creates_no_user_fk_on_postgres(migrated_db: tuple[Config, sa.Engine]) -> None:
    """On Postgres 0009 used to add user_id -> users.id while users was still empty."""
    config, engine = migrated_db
    command.upgrade(config, "20260407_0008")
    _insert_user_scoped_rows(engine, 5_123_456_789)

    calls = _run_as_postgres(engine, _load_revision("20260407_0009"), "upgrade")

    foreign_keys = [args for name, args, _ in calls if name == "create_foreign_key"]
    assert foreign_keys == [
        ("fk_tracker_events_tracker_id", "tracker_events", "trackers", ["tracker_id"], ["id"])
    ]


def test_0010_postgres_swaps_checks_around_the_status_rewrite(
    migrated_db: tuple[Config, sa.Engine],
) -> None:
    config, engine = migrated_db
    command.upgrade(config, "20260407_0009")
    telegram_id = 5_123_456_789
    _insert_user_scoped_rows(engine, telegram_id)
    present = {table: set(checks) for table, checks in LEGACY_CHECKS.items()}

    calls = _run_as_postgres(engine, _load_revision("20261011_0010"), "upgrade", present)

    for table, check in (
        ("lead_items", "chk_lead_items_status"),
        ("watchlist_items", "chk_watchlist_items_workflow_status"),
        ("watchlist_items", "chk_watchlist_items_market_status"),
    ):
        dropped = _position(calls, "drop_constraint", check)
        rewritten = _position(calls, "execute", f"UPDATE {table}")
        created = _position(calls, "create_check_constraint", check)
        assert dropped < rewritten < created, (table, check)
    assert not [args for name, args, _ in calls if "NOT VALID" in str(args)]
    # The user FKs point at users.telegram_user_id, created after the users backfill.
    backfill = _position(calls, "execute", "INSERT INTO users")
    for table in USER_SCOPED_TABLES:
        position = _position(calls, "create_foreign_key", f"fk_{table}_user_id")
        assert calls[position][1][1:] == (table, "users", ["user_id"], ["telegram_user_id"])
        assert backfill < position
    assert _rows(engine, "SELECT status FROM lead_items ORDER BY ad_id") == [
        ("sold",),
        ("skipped",),
        ("negotiating",),
    ]


def _insert_canonical_statuses(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (telegram_user_id, first_name) VALUES (1, 'u')")
        )
        lead_statuses = ("sold", "skipped", "bought", "reselling", "reviewing")
        for ad_id, status in enumerate(lead_statuses, start=1):
            connection.execute(
                sa.text(
                    "INSERT INTO lead_items (user_id, ad_id, query, title, link, status) "
                    "VALUES (1, :ad_id, 'q', 't', 'l', :status)"
                ),
                {"ad_id": ad_id, "status": status},
            )
        for ad_id, status in enumerate(("in_progress", "skipped", "reviewing", "deferred"), 1):
            connection.execute(
                sa.text(
                    "INSERT INTO watchlist_items (user_id, ad_id, query, title, link, "
                    "workflow_status) VALUES (1, :ad_id, 'q', 't', 'l', :status)"
                ),
                {"ad_id": ad_id, "status": status},
            )


LEGACY_LEAD_AFTER_DOWNGRADE = [
    ("closed",),
    ("abandoned",),
    ("in_progress",),
    ("in_progress",),
    ("reviewing",),
]
LEGACY_WATCHLIST_AFTER_DOWNGRADE = [("lead",), ("archived",), ("watching",), ("deferred",)]


def test_downgrade_0010_rewrites_rows_for_the_restored_checks(
    migrated_db: tuple[Config, sa.Engine],
) -> None:
    config, engine = migrated_db
    command.upgrade(config, "head")
    # Emulate the canonical CHECKs that head has on Postgres.
    _add_checks(
        engine,
        "lead_items",
        {name: str(check.sqltext) for name, check in _model_checks("lead_items").items()},
    )
    _add_checks(
        engine,
        "watchlist_items",
        {name: str(check.sqltext) for name, check in _model_checks("watchlist_items").items()},
    )
    _insert_canonical_statuses(engine)

    command.downgrade(config, "20260407_0009")

    assert _rows(engine, "SELECT status FROM lead_items ORDER BY ad_id") == (
        LEGACY_LEAD_AFTER_DOWNGRADE
    )
    assert _rows(engine, "SELECT workflow_status FROM watchlist_items ORDER BY ad_id") == (
        LEGACY_WATCHLIST_AFTER_DOWNGRADE
    )
    assert "'closed'" in _checks(engine, "lead_items")["chk_lead_items_status"]
    assert "'lead'" in _checks(engine, "watchlist_items")["chk_watchlist_items_workflow_status"]
    # Every row satisfies the restored CHECKs, so ordinary updates keep working.
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE lead_items SET notes = 'n'"))
        connection.execute(sa.text("UPDATE watchlist_items SET current_price_byn = 1"))
    with engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
        connection.execute(sa.text("UPDATE lead_items SET status = 'sold' WHERE ad_id = 1"))

    command.upgrade(config, "head")
    assert _rows(engine, "SELECT status FROM lead_items ORDER BY ad_id")[:2] == [
        ("sold",),
        ("skipped",),
    ]


def test_downgrade_0010_postgres_restores_validated_legacy_checks(
    migrated_db: tuple[Config, sa.Engine],
) -> None:
    config, engine = migrated_db
    command.upgrade(config, "head")
    _insert_canonical_statuses(engine)
    present = {
        "lead_items": {"chk_lead_items_status"},
        "watchlist_items": {
            "chk_watchlist_items_workflow_status",
            "chk_watchlist_items_market_status",
        },
    }

    calls = _run_as_postgres(engine, _load_revision("20261011_0010"), "downgrade", present)

    for table, check in (
        ("lead_items", "chk_lead_items_status"),
        ("watchlist_items", "chk_watchlist_items_workflow_status"),
    ):
        dropped = _position(calls, "drop_constraint", check)
        rewritten = _position(calls, "execute", f"UPDATE {table}")
        created = _position(calls, "create_check_constraint", check)
        assert dropped < rewritten < created, (table, check)
    assert not [args for name, args, _ in calls if "NOT VALID" in str(args)]
    assert _rows(engine, "SELECT status FROM lead_items ORDER BY ad_id") == (
        LEGACY_LEAD_AFTER_DOWNGRADE
    )
    assert _rows(engine, "SELECT workflow_status FROM watchlist_items ORDER BY ad_id") == (
        LEGACY_WATCHLIST_AFTER_DOWNGRADE
    )


def _model_checks(table: str) -> dict[str, sa.CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in Base.metadata.tables[table].constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name
    }


def test_0011_marks_legacy_inactive_trackers_deleted(
    migrated_db: tuple[Config, sa.Engine],
) -> None:
    import asyncio

    from api.database import get_engine, get_session_factory
    from api.services.tracker_service import resume_paused_trackers

    config, engine = migrated_db
    command.upgrade(config, "20261011_0010")
    telegram_id = 5_123_456_789
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (telegram_user_id, first_name) VALUES (:uid, '')"),
            {"uid": telegram_id},
        )
        # The legacy /untrack left exactly this: active = false, deleted_at NULL.
        connection.execute(
            sa.text(
                "INSERT INTO trackers (id, user_id, query, active) VALUES "
                "(1, :uid, 'untracked', FALSE), (2, :uid, 'kept', TRUE), "
                "(3, :uid, 'deleted', FALSE)"
            ),
            {"uid": telegram_id},
        )
        connection.execute(
            sa.text("UPDATE trackers SET deleted_at = '2026-01-01 00:00:00' WHERE id = 3")
        )

    command.upgrade(config, "head")

    assert _rows(engine, "SELECT id, deleted_at IS NOT NULL FROM trackers ORDER BY id") == [
        (1, 1),
        (2, 0),
        (3, 1),
    ]
    assert _rows(engine, "SELECT deleted_at FROM trackers WHERE id = 3") == [
        ("2026-01-01 00:00:00",)
    ]
    # A pause that happens after the deploy (the user blocked the bot) is still resumable.
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE trackers SET active = FALSE WHERE id = 2"))

    async def resume() -> list[int]:
        async_engine = get_engine(str(engine.url).replace("sqlite://", "sqlite+aiosqlite://"))
        try:
            async with get_session_factory(async_engine)() as session:
                resumed = await resume_paused_trackers(session, user_id=telegram_id)
                await session.commit()
                return [tracker.id for tracker in resumed]
        finally:
            await async_engine.dispose()

    assert asyncio.run(resume()) == [2]
    assert _rows(engine, "SELECT id FROM trackers WHERE active ORDER BY id") == [(2,)]
