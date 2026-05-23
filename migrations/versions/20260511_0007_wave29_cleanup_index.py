"""Wave 29 — cleanup partial index on query_listing_states + ANALYZE

BE-06 / BE-08 from DEEP_DIVE_REVIEW_COMPREHENSIVE.md.

The scheduler's nightly cleanup runs

    DELETE FROM query_listing_states
    WHERE active = false AND last_seen_at < cutoff

(see ``scheduler.collector.cleanup_inactive_listing_states``). Before
this migration there was no index on either ``active`` or
``last_seen_at``: the compound ``idx_query_listing_states_query_active``
(query, active) is keyed on the wrong leading column for this query
and the cleanup ran as a seq-scan over the whole table.

Wave 17 (migration ``20260510_0006``) deliberately dropped the
standalone boolean ``idx_query_listing_states_active`` because a plain
boolean index is wasteful — Postgres' planner picks a seq scan over
it in almost every realistic shape. The proper fix isn't to bring it
back: it's a **partial** index keyed on ``last_seen_at`` that only
materialises rows with ``active = false``. In a populated table that
shrinks the index to the working set the cleanup actually scans
(typically <5% of the rows after a retention cycle) and gives the
planner a sorted ``last_seen_at`` to range-scan against the cutoff.

SQLite supports partial indexes via ``CREATE INDEX ... WHERE`` since
3.8.0, which is well below the version pinned by pytest-aiosqlite, so
the same SQL works in the test path.

BE-08 — every migration that introduces a new index should ``ANALYZE``
the affected table afterwards. Postgres' autovacuum runs analyze on a
schedule, but immediately after a deploy the planner still has the
pre-migration row distribution cached and may pick the seq scan over
the brand-new index for several hours. ``ANALYZE`` is cheap on a
table of this size and removes that window. SQLite's ``ANALYZE``
populates ``sqlite_stat1`` similarly.

Revision ID: 20260511_0007
Revises: 20260510_0006

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260511_0007"
down_revision: str | Sequence[str] | None = "20260510_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dialect_name() -> str:
    bind = op.get_bind()
    return bind.dialect.name if bind is not None else ""


def upgrade() -> None:
    dialect = _dialect_name()

    # ── BE-06: partial index for the cleanup predicate ──────────────────
    # ``IF NOT EXISTS`` so a repeated upgrade after a downgrade re-applies
    # cleanly without a missing-index error.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_query_listing_states_cleanup "
        "ON query_listing_states (last_seen_at) "
        "WHERE active = false"
    )

    # ── BE-08: refresh planner statistics so the new index is picked
    # immediately, not after the next autovacuum window. Both Postgres
    # and SQLite understand ``ANALYZE <table>``.
    if dialect == "postgresql":
        op.execute("ANALYZE query_listing_states")
    elif dialect == "sqlite":
        # SQLite's ANALYZE is per-database; per-table is a noop on
        # versions that don't recognise the table-name form, so the
        # plain form is safer.
        op.execute("ANALYZE")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_query_listing_states_cleanup")
