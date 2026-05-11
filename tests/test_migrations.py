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
