"""Add CHECK constraints, indexes, and cleanup function

P2-02: query_listing_states cleanup function
P2-03: lead_items.market_status CHECK constraint
P2-04: lead_items.source CHECK constraint
P2-05: idx_lead_items_user_created index
P2-17: UserConsent partial unique index

Revision ID: 20260509_0002
Revises: 20260509_0001

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260509_0002"
down_revision: str | Sequence[str] | None = "20260509_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE lead_items ADD CONSTRAINT chk_lead_items_market_status "
        "CHECK (market_status IN ('active', 'missing', 'price_drop'))"
    )

    op.execute(
        "ALTER TABLE lead_items ADD CONSTRAINT chk_lead_items_source "
        "CHECK (source IN ('manual', 'watchlist', 'detail_modal', 'bot_callback'))"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_items_user_created "
        "ON lead_items (user_id, created_at)"
    )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_consents_active "
        "ON user_consents (user_id, consent_type) WHERE revoked_at IS NULL"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION clean_query_listing_states(
            retention_days integer DEFAULT 30
        )
        RETURNS bigint AS $$
        DECLARE
            deleted_count bigint;
        BEGIN
            DELETE FROM query_listing_states
            WHERE active = false
              AND last_seen_at < now() - (retention_days || ' days')::interval;
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS clean_query_listing_states(integer)")
    op.execute("DROP INDEX IF EXISTS uq_user_consents_active")
    op.execute("DROP INDEX IF EXISTS idx_lead_items_user_created")
    op.execute("ALTER TABLE lead_items DROP CONSTRAINT IF EXISTS chk_lead_items_source")
    op.execute("ALTER TABLE lead_items DROP CONSTRAINT IF EXISTS chk_lead_items_market_status")
