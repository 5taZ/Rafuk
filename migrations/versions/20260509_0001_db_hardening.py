"""DB hardening: triggers, indexes, JSONB, precision, cleanup

P0-03: tracker_events cleanup function (90-day TTL)
P0-04: updated_at triggers for lead_items, trackers, query_listing_states
P1-02: Covering index on tracker_events (user_id, created_at DESC, id DESC)
P1-03: tracker_events.parameters JSON -> JSONB
P1-06: Drop 7 redundant indexes created by 6040ea4a0fd6
P1-07: buy_price_byn/sold_price_byn Numeric(10,2) -> Numeric(12,2)

Revision ID: 20260509_0001
Revises: 7b413345fcf2
Create Date: 2026-05-09

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260509_0001"
down_revision: str | Sequence[str] | None = "7b413345fcf2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # DB-MEDIUM (issues §4.2): all DDL below is PostgreSQL-specific
    # (PL/pgSQL functions, ALTER COLUMN TYPE ... USING, JSONB). On
    # SQLite (test stand) every op.execute would crash, so the whole
    # migration is a no-op there. Production / staging always run on
    # Postgres so behaviour for real deployments is unchanged.
    bind = op.get_bind()
    if bind is not None and bind.dialect.name != "postgresql":
        return

    # ── P1-03: JSON -> JSONB ──────────────────────────────────────────────
    op.execute(
        "ALTER TABLE tracker_events "
        "ALTER COLUMN parameters TYPE JSONB "
        "USING parameters::jsonb"
    )

    # ── P1-02: Covering index ─────────────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tracker_events_user_created_id "
        "ON tracker_events (user_id, created_at DESC, id DESC)"
    )
    op.execute("DROP INDEX IF EXISTS idx_tracker_events_created")

    # ── P1-06: Drop redundant indexes (duplicated by composite indexes) ───
    op.execute("DROP INDEX IF EXISTS ix_ai_audit_log_user_id")
    op.execute("DROP INDEX IF EXISTS ix_lead_items_user_id")
    op.execute("DROP INDEX IF EXISTS ix_tracker_events_user_id")
    op.execute("DROP INDEX IF EXISTS ix_tracker_events_tracker_id")
    op.execute("DROP INDEX IF EXISTS ix_trackers_user_id")
    op.execute("DROP INDEX IF EXISTS ix_user_consents_user_id")
    op.execute("DROP INDEX IF EXISTS ix_lead_item_price_snapshots_lead_item_id")

    # ── P1-07: Numeric precision ───────────────────────────────────────────
    op.execute(
        "ALTER TABLE lead_items "
        "ALTER COLUMN buy_price_byn TYPE NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE lead_items "
        "ALTER COLUMN sold_price_byn TYPE NUMERIC(12,2)"
    )

    # ── P0-04: updated_at triggers ────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION trg_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    for tbl in ("lead_items", "trackers", "query_listing_states"):
        op.execute(
            f"CREATE TRIGGER set_updated_at "
            f"BEFORE UPDATE ON {tbl} "
            f"FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at()"
        )

    # ── P0-03: tracker_events cleanup function ────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION clean_tracker_events(
            retention_days integer DEFAULT 90
        )
        RETURNS bigint AS $$
        DECLARE
            deleted_count bigint;
        BEGIN
            DELETE FROM tracker_events
            WHERE created_at < now() - (retention_days || ' days')::interval;
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $$ LANGUAGE plpgsql
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind is not None and bind.dialect.name != "postgresql":
        return
    # ── Reverse triggers ──────────────────────────────────────────────────
    for tbl in ("lead_items", "trackers", "query_listing_states"):
        op.execute(f"DROP TRIGGER IF EXISTS set_updated_at ON {tbl}")
    op.execute("DROP FUNCTION IF EXISTS trg_set_updated_at()")
    op.execute("DROP FUNCTION IF EXISTS clean_tracker_events(integer)")

    # ── Reverse Numeric precision ─────────────────────────────────────────
    op.execute(
        "ALTER TABLE lead_items "
        "ALTER COLUMN buy_price_byn TYPE NUMERIC(10,2)"
    )
    op.execute(
        "ALTER TABLE lead_items "
        "ALTER COLUMN sold_price_byn TYPE NUMERIC(10,2)"
    )

    # ── Recreate dropped indexes ──────────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tracker_events_created "
        "ON tracker_events (created_at)"
    )

    # ── Reverse JSONB -> JSON ─────────────────────────────────────────────
    op.execute(
        "ALTER TABLE tracker_events "
        "ALTER COLUMN parameters TYPE JSON "
        "USING parameters::json"
    )

    # ── Drop covering index ───────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS idx_tracker_events_user_created_id")
