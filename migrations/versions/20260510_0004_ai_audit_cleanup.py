"""Add cleanup function for ai_audit_log

DB-H3: ai_audit_log was growing unbounded — like tracker_events did
(closed in 20260509_0001). The audit table is a Law-91-Z compliance
artifact, not an analytics source, so we only need to keep enough
history for legal review periods. 365 days is the default retention;
ops can call ``SELECT clean_ai_audit_log(N)`` from a periodic cron
(or schedule via pg_cron when available) to trim older rows.

Note: full table-level partitioning (ATTACH PARTITION FOR VALUES …)
would be a stronger fix for very high-volume installations, but is a
much larger schema change with backfill considerations. For the
single-host deployment this project targets, the cleanup function
is sufficient and matches the pattern already used for tracker_events.

Revision ID: 20260510_0004
Revises: 20260510_0003
Create Date: 2026-05-10

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260510_0004"
down_revision: str | Sequence[str] | None = "20260510_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION clean_ai_audit_log(
            retention_days integer DEFAULT 365
        )
        RETURNS bigint AS $$
        DECLARE
            deleted_count bigint;
        BEGIN
            DELETE FROM ai_audit_log
            WHERE created_at < now() - (retention_days || ' days')::interval;
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    # Supporting index — DELETE on a 100k+ row audit table without an
    # index on created_at scans the whole table. lead_items already
    # gets the same treatment in 20260509_0001 covering index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_audit_log_created_at "
        "ON ai_audit_log (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ai_audit_log_created_at")
    op.execute("DROP FUNCTION IF EXISTS clean_ai_audit_log(integer)")
