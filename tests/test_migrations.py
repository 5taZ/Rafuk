"""TEST-M6: Alembic migration sanity checks.

The audit flagged that we had no automated coverage of the migration
chain — a single bad rebase or a stray branch on ``down_revision``
would only surface when somebody ran ``alembic upgrade head`` against
a real DB. These tests catch the static issues immediately and (when
a PostgreSQL TEST_DATABASE_URL is provided) round-trip ``head`` →
``-1`` → ``head`` so we know each migration's ``downgrade()`` is
actually wired up.

RV-DB-01: the Postgres checks also compare the migrated schema with
``api/models.py`` — the rest of the suite runs on schemas built by
``Base.metadata.create_all``, so nothing else notices when the models
and the migrations drift apart.

Static checks run everywhere (CI + local). The Postgres checks are
gated on ``TEST_DATABASE_URL`` because most migrations use
Postgres-specific DDL (JSONB, partial WHERE indexes, CHECK
constraints with raw SQL) that SQLite cannot replay.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "migrations" / "alembic.ini"
# Bump together with docs/BACKUP_RUNBOOK.md ("Expected Alembic head").
EXPECTED_HEAD = "20261021_0027"


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # In-process alembic must not run fileConfig(): it disables the app
    # loggers for the rest of the session (see migrations/env.py).
    cfg.attributes["configure_logger"] = False
    return cfg


# ── Static checks ────────────────────────────────────────────────────────


def test_alembic_chain_has_single_head() -> None:
    """A merged main branch must have exactly one head — anything else
    means somebody pushed a parallel migration without an explicit merge.
    """
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Alembic should have exactly one head, found {len(heads)}: {heads!r}. "
        "Run `alembic merge -m 'merge heads' " + " ".join(heads) + "` to fix."
    )
    assert heads[0] == EXPECTED_HEAD, (
        f"Expected alembic head to be {EXPECTED_HEAD}, got {heads[0]!r}"
    )


def test_alembic_chain_is_walkable_from_head_to_base() -> None:
    """Each revision must link to its parent. A broken ``down_revision``
    or a missing parent file makes ``walk_revisions`` raise — catching
    cherry-pick mistakes that pass ruff but break ``alembic upgrade``.
    """
    script = ScriptDirectory.from_config(_alembic_config())
    revisions = list(script.walk_revisions("base", "heads"))
    assert revisions, "Alembic walked the chain but found zero revisions"
    # Sanity check: every revision has a non-empty ID and matches the
    # script directory listing.
    revision_ids = {rev.revision for rev in revisions}
    versions_dir = REPO_ROOT / "migrations" / "versions"
    on_disk_files = [
        p for p in versions_dir.glob("*.py")
        if not p.name.startswith("_") and p.name != "__init__.py"
    ]
    # Every walked revision must correspond to a real file. The reverse
    # is not necessarily true (deleted/replaced files may still live
    # in version control history), but the walk must not invent revs.
    assert len(revisions) <= len(on_disk_files), (
        f"Walked {len(revisions)} revisions but found "
        f"{len(on_disk_files)} files in {versions_dir}"
    )
    assert all(revision_ids), "Found a revision with empty revision ID"


def test_alembic_revisions_have_unique_ids() -> None:
    """Two migrations sharing a revision ID is a silent corruption that
    makes ``upgrade`` non-deterministic. Catch it here."""
    script = ScriptDirectory.from_config(_alembic_config())
    ids = [rev.revision for rev in script.walk_revisions("base", "heads")]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"Duplicate revision IDs: {duplicates}"


# ── Scheduler delivery-state revision on SQLite ─────────────────────────

_SCHEDULER_DELIVERY_REVISION = "20261021_0026"


def _run_alembic(db_path: Path, *steps: tuple[str, str]) -> None:
    """Run alembic commands in a subprocess: env.py calls ``asyncio.run`` and
    must not share this process' event loop or logging setup."""
    script = (
        "import sys\n"
        "from alembic import command\n"
        "from alembic.config import Config\n"
        "cfg = Config()\n"
        "cfg.set_main_option('script_location', sys.argv[1])\n"
        "for step in sys.argv[2:]:\n"
        "    name, rev = step.split(':')\n"
        "    getattr(command, name)(cfg, rev)\n"
    )
    subprocess.run(
        [sys.executable, "-c", script, str(REPO_ROOT / "migrations"),
         *(f"{name}:{rev}" for name, rev in steps)],
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"},
        capture_output=True,
        timeout=120,
    )


def test_scheduler_delivery_state_migration_round_trips_on_sqlite(tmp_path: Path) -> None:
    """RV-SCH-01/02/04: 20261021_0026 downgrades and re-upgrades on SQLite
    (local dev) — Postgres is covered by the round-trip test below in CI.

    Starts from the model schema, so it also pins model and migration to
    the same columns. Only this revision is stepped (``-1`` / back to it),
    whatever else the chain holds around it.
    """
    from sqlalchemy import create_engine

    from api.models import Base

    db_path = tmp_path / "migration.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    def columns(table: str) -> set[str]:
        with sqlite3.connect(db_path) as con:
            return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO users (id, telegram_user_id, first_name) VALUES (1, 42, 'A')"
        )
        con.execute(
            "INSERT INTO lead_items (id, user_id, ad_id, query, title, link) "
            "VALUES (1, 1, 5, 'q', 't', 'l')"
        )
        con.execute(
            "INSERT INTO lead_reminders (lead_id, user_id, remind_at, message, sent, status) "
            "VALUES (1, 1, '2026-10-01 10:00:00', 'gave up', 0, 'failed')"
        )

    _run_alembic(db_path, ("stamp", _SCHEDULER_DELIVERY_REVISION), ("downgrade", "-1"))
    assert "price_history" not in columns("query_listing_states")
    assert "dedup_key" not in columns("telegram_notification_dlq")
    assert not {"status", "attempts", "next_attempt_at", "last_error"} & columns("lead_reminders")
    with sqlite3.connect(db_path) as con:
        # The old scheduler re-sends every unsent reminder: a terminal one
        # comes back as sent so the downgrade does not replay it.
        assert con.execute("SELECT sent FROM lead_reminders").fetchall() == [(1,)]

    _run_alembic(db_path, ("upgrade", _SCHEDULER_DELIVERY_REVISION))
    assert "price_history" in columns("query_listing_states")
    assert "dedup_key" in columns("telegram_notification_dlq")
    assert {"status", "attempts", "next_attempt_at", "last_error"} <= columns("lead_reminders")
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT status, attempts FROM lead_reminders").fetchall() == [
            ("sent", 0),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE lead_reminders SET status = 'bogus'")


# ── Postgres checks (dedicated scratch database) ─────────────────────────


def _has_postgres_test_db() -> bool:
    url = os.environ.get("TEST_DATABASE_URL", "")
    return bool(url) and "postgres" in url


requires_postgres = pytest.mark.skipif(
    not _has_postgres_test_db(),
    reason="migration checks against a real schema need TEST_DATABASE_URL=postgresql+...",
)


async def _execute_autocommit(url: sa.URL, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text(statement))
    finally:
        await engine.dispose()


@pytest.fixture
def migrated_database_url(monkeypatch: pytest.MonkeyPatch):
    """RV-DB-01: a throwaway Postgres database upgraded to head.

    conftest rebuilds the shared TEST_DATABASE_URL schema with
    ``Base.metadata.create_all`` around every test, so migration checks
    run there compared the models with themselves, and ``downgrade``
    ran against tables no migration had built. env.py takes its URL
    from Settings, hence DATABASE_URL is pointed at the scratch database
    for the duration of the test.
    """
    from api.config import get_settings

    server_url = make_url(os.environ["TEST_DATABASE_URL"])
    name = f"{server_url.database}_rv_{secrets.token_hex(4)}"
    asyncio.run(_execute_autocommit(server_url, f'CREATE DATABASE "{name}"'))
    scratch_url = server_url.set(database=name).render_as_string(hide_password=False)
    previous_url = os.environ.get("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
        yield scratch_url
    finally:
        monkeypatch.setenv("DATABASE_URL", previous_url)
        get_settings.cache_clear()
        asyncio.run(
            _execute_autocommit(server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        )


@requires_postgres
def test_alembic_upgrade_head_then_downgrade_then_upgrade(migrated_database_url: str) -> None:
    """Catch downgrade() bugs by running ``head → -1 → head`` against a
    real Postgres. If the newest migration's ``downgrade()`` is missing
    or inverse-incorrect, the second ``upgrade head`` will fail with a
    "table already exists" / "constraint already exists" error.

    RV-DB-01: starts from a freshly migrated database. The old version
    first ran ``downgrade base`` on the shared, create_all-built schema,
    which failed on the first index no migration had created. A full
    ``downgrade base`` is not attempted: older downgrades are known to be
    incomplete (RV-DB-14), the chain is effectively forward-only.
    """
    cfg = _alembic_config()
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")


# RV-DB-01: drift that is real but owned by another finding. Each entry
# must still show up in the diff — once it is fixed the entry has to go,
# so this list can only shrink.
_KNOWN_DRIFT: dict[tuple[str, str, str], str] = {
    ("modify_type", "lead_items", "updated_at"): (
        "RV-DB-04: still timestamp without time zone; converting it needs "
        "the timezone of the stored values confirmed on prod first"
    ),
}


def _flatten_diffs(diffs: list) -> list[tuple]:
    flat: list[tuple] = []
    for diff in diffs:
        flat.extend(diff if isinstance(diff, list) else [diff])
    return flat


def _drift_key(diff: tuple) -> tuple[str, str, str]:
    kind = diff[0]
    if kind.startswith("modify_"):
        return kind, diff[2], diff[3]
    if kind in {"add_column", "remove_column"}:
        return kind, diff[2], diff[3].name
    target = diff[1]
    table = getattr(target, "table", None)
    return kind, getattr(table, "name", "") or "", str(getattr(target, "name", target))


def _describe_drift(diff: tuple) -> str:
    kind, table, name = _drift_key(diff)
    if kind.startswith("modify_"):
        return f"{kind} {table}.{name}: database={diff[5]!r} model={diff[6]!r}"
    return f"{kind} {table + '.' if table else ''}{name}"


@requires_postgres
def test_models_match_migrated_schema(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RV-DB-01: ``alembic revision --autogenerate`` on a database at head
    must produce nothing. Runs through migrations/env.py, so the same
    include_object filter and compare options as a developer's
    autogenerate apply. A failure means api/models.py and the migrations
    disagree: add the missing migration, or fix the model if the
    migrated schema is the intended one."""
    from api.models import AIAuditLog, User

    # conftest narrows these BIGINT primary keys to Integer so SQLite
    # auto-increments them; compare the models as the application sees them.
    for model in (User, AIAuditLog):
        monkeypatch.setattr(model.__table__.c.id, "type", sa.BigInteger())

    captured: list[tuple] = []

    def _capture(context, revision, directives) -> None:
        captured.extend(_flatten_diffs(directives[0].upgrade_ops.as_diffs()))
        directives[:] = []  # inspect only, never write a revision file

    command.revision(
        _alembic_config(), autogenerate=True, process_revision_directives=_capture,
    )

    found = {_drift_key(diff): _describe_drift(diff) for diff in captured}
    unexpected = sorted(text for key, text in found.items() if key not in _KNOWN_DRIFT)
    assert not unexpected, (
        "api/models.py and the migrated schema disagree:\n  " + "\n  ".join(unexpected)
    )
    stale = sorted(f"{key}: {why}" for key, why in _KNOWN_DRIFT.items() if key not in found)
    assert not stale, (
        "Fixed drift is still listed in _KNOWN_DRIFT — remove it:\n  " + "\n  ".join(stale)
    )


def _check_literals(sqltext: str) -> tuple[frozenset[str], frozenset[str]]:
    """Quoted and numeric literals of a CHECK condition — enough to spot
    a value list or bound that changed on one side only, while ignoring
    how Postgres re-renders the expression (``= ANY (ARRAY[...])``,
    casts, parentheses)."""
    quoted = frozenset(re.findall(r"'([^']*)'", sqltext))
    unquoted = re.sub(r"'[^']*'", " ", sqltext)
    numbers = frozenset(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", unquoted))
    return quoted, numbers


async def _reflect_check_constraints(url: str, tables: list[str]) -> dict[str, dict[str, str]]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: {
                    table: {
                        check["name"]: check["sqltext"]
                        for check in sa.inspect(sync_conn).get_check_constraints(table)
                        if check.get("name")
                    }
                    for table in tables
                }
            )
    finally:
        await engine.dispose()


@requires_postgres
def test_check_constraints_match_migrated_schema(migrated_database_url: str) -> None:
    """RV-DB-01: autogenerate does not compare CHECK constraints at all,
    which is how two model-only CHECKs (RV-DB-05) and two database-only
    ones went unnoticed. Compare them by name, and their literals."""
    from api.models import Base

    model_checks = {
        table.name: {
            constraint.name: str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint) and isinstance(constraint.name, str)
        }
        for table in Base.metadata.sorted_tables
    }
    db_checks = asyncio.run(_reflect_check_constraints(migrated_database_url, list(model_checks)))

    problems: list[str] = []
    for table, model in model_checks.items():
        database = db_checks.get(table, {})
        problems += [f"{table}.{name}: only in api/models.py" for name in model.keys() - database]
        problems += [f"{table}.{name}: only in the migrations" for name in database.keys() - model]
        problems += [
            f"{table}.{name}: model {model[name]!r} vs database {database[name]!r}"
            for name in model.keys() & database.keys()
            if _check_literals(model[name]) != _check_literals(database[name])
        ]
    assert not problems, "CHECK constraints drifted:\n  " + "\n  ".join(sorted(problems))
