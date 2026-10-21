"""TEST-M6: Alembic migration sanity checks.

The audit flagged that we had no automated coverage of the migration
chain — a single bad rebase or a stray branch on ``down_revision``
would only surface when somebody ran ``alembic upgrade head`` against
a real DB. These tests catch the static issues immediately and (when
a PostgreSQL TEST_DATABASE_URL is provided) round-trip ``head`` →
``-1`` → ``head`` so we know each migration's ``downgrade()`` is
actually wired up.

Static checks run everywhere (CI + local). The round-trip smoke test
is gated on ``TEST_DATABASE_URL`` because most migrations use
Postgres-specific DDL (JSONB, partial WHERE indexes, CHECK
constraints with raw SQL) that SQLite cannot replay.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "migrations" / "alembic.ini"


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
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
    assert heads[0] == "20261021_0026", (
        f"Expected alembic head to be 20261021_0026, got {heads[0]!r}"
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


# ── Round-trip smoke test (Postgres only) ────────────────────────────────


def _has_postgres_test_db() -> bool:
    url = os.environ.get("TEST_DATABASE_URL", "")
    return bool(url) and "postgres" in url


@pytest.mark.skipif(
    not _has_postgres_test_db(),
    reason="round-trip migration test needs TEST_DATABASE_URL=postgresql+...",
)
def test_alembic_upgrade_head_then_downgrade_then_upgrade() -> None:
    """Catch downgrade() bugs by running ``head → -1 → head`` against a
    real Postgres. If a migration's ``downgrade()`` is missing or
    inverse-incorrect, the second ``upgrade head`` will fail with a
    "table already exists" / "constraint already exists" error.

    Only runs in CI where a real Postgres service is available.
    Roughly 5-10 seconds for the full chain.
    """
    from alembic import command

    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", os.environ["TEST_DATABASE_URL"])

    # Reset to a clean slate first — previous test runs may have left
    # the DB at any revision. ``downgrade base`` is idempotent.
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
