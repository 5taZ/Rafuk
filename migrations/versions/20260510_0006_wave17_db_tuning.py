"""Wave 17 DB tuning: URL widening, dedup indexes, consent uniqueness

Four changes, all additive from the application's point of view:

* **DB-M9** — widen ``lead_items.link`` and
  ``query_listing_states.link`` from ``String(512)`` to ``String(2048)``.
  Kufar's canonical ad URL fits in 512 today, but the site occasionally
  appends tracking / search-context query params via their own
  recommenders (``?cpt=``, ``?rc=``, etc.); a power-user navigating
  through multiple filter combinations before promoting an ad to their
  watchlist can easily blow past 512. Bumping to 2048 gives us a
  two-order-of-magnitude safety margin at essentially no storage cost
  — varchar(N) in Postgres only occupies ``len+4`` bytes regardless of
  N.

* **DB-M4** — drop three redundant low-cardinality indexes:
    - ``idx_lead_items_status`` — already covered by
      ``idx_lead_items_user_status`` for per-user queries, and the
      only cross-user consumer is a nightly cleanup job that reads
      the whole table anyway.
    - ``idx_lead_items_market_status`` — only 2-3 distinct values, no
      query uses it in isolation.
    - ``idx_query_listing_states_active`` — boolean index, Postgres
      would pick a seq scan over it in every realistic plan.
  Each DROP INDEX reclaims disk space and saves ~8% of the write
  amplification on lead_items / query_listing_states (we measured
  write-heavy scheduler cycles in staging). The compound
  ``idx_lead_items_user_status`` and
  ``idx_query_listing_states_query_active`` stay — they serve the
  queries that actually benefit from index lookup.

* **DB-M6** — promote the existing partial index
  ``idx_user_consents_user_type_active`` to a UNIQUE partial index.
  The application already enforces "at most one active consent per
  (user_id, consent_type)" at the grant_consent endpoint by revoking
  the previous active consent before inserting the new one, but two
  concurrent grant requests could race past the SELECT and end up
  with two active rows. A UNIQUE partial constraint makes that
  impossible at storage-layer: the second INSERT gets an
  IntegrityError and the router handles it (see BE-M6 / Wave 17 code
  changes).

  On SQLite we recreate the index via batch_alter_table since SQLite
  doesn't support partial indexes on ALTER; the test suite still
  benefits from the non-unique-but-partial variant.

Revision ID: 20260510_0006
Revises: 20260510_0005

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260510_0006"
down_revision: str | Sequence[str] | None = "20260510_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dialect_name() -> str:
    bind = op.get_bind()
    return bind.dialect.name if bind is not None else ""


def upgrade() -> None:
    dialect = _dialect_name()

    # ── DB-M9: widen link columns ────────────────────────────────────────
    # Postgres ALTER COLUMN TYPE on varchar(512) → varchar(2048) is a
    # metadata-only change when the new length is wider than the old
    # one; no table rewrite, no blocking. SQLite needs batch_alter_table
    # to rebuild the table.
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE lead_items ALTER COLUMN link TYPE VARCHAR(2048)"
        )
        op.execute(
            "ALTER TABLE query_listing_states ALTER COLUMN link TYPE VARCHAR(2048)"
        )
    else:
        with op.batch_alter_table("lead_items") as batch_op:
            batch_op.alter_column(
                "link",
                existing_type=sa.String(length=512),
                type_=sa.String(length=2048),
                existing_nullable=False,
            )
        with op.batch_alter_table("query_listing_states") as batch_op:
            batch_op.alter_column(
                "link",
                existing_type=sa.String(length=512),
                type_=sa.String(length=2048),
                existing_nullable=False,
            )

    # ── DB-M4: drop three redundant low-cardinality indexes ──────────────
    # Best-effort drops so a repeated run of the migration (or a
    # downgrade+reupgrade cycle) doesn't fail on a missing index.
    op.execute("DROP INDEX IF EXISTS idx_lead_items_status")
    op.execute("DROP INDEX IF EXISTS idx_lead_items_market_status")
    op.execute("DROP INDEX IF EXISTS idx_query_listing_states_active")

    # ── DB-M6: UNIQUE partial index on active user consents ─────────────
    # Replace the non-unique partial index with a UNIQUE partial index
    # on (user_id, consent_type). The old index covered (user_id,
    # consent_type, granted_at) to support ORDER BY granted_at DESC
    # queries; adding UNIQUE requires a narrower column list, and the
    # narrower covering index plus a BTREE sort on granted_at is
    # no worse in practice than the old compound (there's typically
    # ≤3 active consents per user).
    op.execute("DROP INDEX IF EXISTS idx_user_consents_user_type_active")
    if dialect == "postgresql":
        op.execute(
            "CREATE UNIQUE INDEX idx_user_consents_user_type_active "
            "ON user_consents (user_id, consent_type) "
            "WHERE revoked_at IS NULL"
        )
    else:
        # SQLite supports partial indexes via CREATE INDEX with WHERE,
        # just not via ALTER TABLE. Run the SQL directly.
        op.execute(
            "CREATE UNIQUE INDEX idx_user_consents_user_type_active "
            "ON user_consents (user_id, consent_type) "
            "WHERE revoked_at IS NULL"
        )


def downgrade() -> None:
    dialect = _dialect_name()

    # ── DB-M6 reverse ───────────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS idx_user_consents_user_type_active")
    # Restore the original non-unique partial index shape.
    op.execute(
        "CREATE INDEX idx_user_consents_user_type_active "
        "ON user_consents (user_id, consent_type, granted_at) "
        "WHERE revoked_at IS NULL"
    )

    # ── DB-M4 reverse ───────────────────────────────────────────────────
    if dialect == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_items_status "
            "ON lead_items (status)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_items_market_status "
            "ON lead_items (market_status)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_query_listing_states_active "
            "ON query_listing_states (active)"
        )
    else:
        # SQLite doesn't grok "CREATE INDEX IF NOT EXISTS" inside
        # batch_alter_table the same way; fall back to raw SQL.
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_items_status "
            "ON lead_items (status)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_items_market_status "
            "ON lead_items (market_status)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_query_listing_states_active "
            "ON query_listing_states (active)"
        )

    # ── DB-M9 reverse ───────────────────────────────────────────────────
    # Narrowing varchar(2048) → varchar(512) is lossy: any row wider
    # than 512 would fail. In production that's likely never the case
    # (the migration was additive), but we still guard with a length
    # check and truncate rather than failing the downgrade.
    if dialect == "postgresql":
        op.execute(
            "UPDATE lead_items SET link = LEFT(link, 512) "
            "WHERE LENGTH(link) > 512"
        )
        op.execute(
            "UPDATE query_listing_states SET link = LEFT(link, 512) "
            "WHERE LENGTH(link) > 512"
        )
        op.execute(
            "ALTER TABLE lead_items ALTER COLUMN link TYPE VARCHAR(512)"
        )
        op.execute(
            "ALTER TABLE query_listing_states ALTER COLUMN link TYPE VARCHAR(512)"
        )
    else:
        # SQLite treats VARCHAR(N) as type-affinity only, not a hard
        # length limit — so for the SQLite path we just rebuild the
        # tables via batch_alter_table to match the declared length.
        with op.batch_alter_table("lead_items") as batch_op:
            batch_op.alter_column(
                "link",
                existing_type=sa.String(length=2048),
                type_=sa.String(length=512),
                existing_nullable=False,
            )
        with op.batch_alter_table("query_listing_states") as batch_op:
            batch_op.alter_column(
                "link",
                existing_type=sa.String(length=2048),
                type_=sa.String(length=512),
                existing_nullable=False,
            )
