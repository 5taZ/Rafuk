"""Merge watchlist_items into lead_items as status='watching'

This migration unifies the watchlist and leads pipelines into a single
``lead_items`` table. Old ``watchlist_items`` rows are copied with
``status='watching'`` and the source table is dropped.

Revision ID: 20260427_0001
Revises: 20260410_0009
Create Date: 2026-04-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260427_0001"
down_revision: str | None = "20260410_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Allowed statuses BEFORE this migration (already include bought/sold from
# 59ad46ea83ba) — we add ``watching`` and ``researching``/``skipped``
# (the latter two were used by the LeadStatusEnum but never permitted by
# the DB constraint, which would have rejected them at insert time).
NEW_STATUSES = (
    "watching",
    "new",
    "reviewing",
    "in_progress",
    "researching",
    "negotiating",
    "deferred",
    "closed",
    "abandoned",
    "bought",
    "sold",
    "skipped",
)
OLD_STATUSES = (
    "new",
    "reviewing",
    "in_progress",
    "negotiating",
    "deferred",
    "closed",
    "abandoned",
    "bought",
    "sold",
)


def _quoted_status_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    lead_columns = (
        {col["name"] for col in inspector.get_columns("lead_items")}
        if "lead_items" in tables
        else set()
    )

    # 1. Add watchlist-derived columns onto lead_items (only if missing).
    if "lead_items" in tables:
        if "initial_price_byn" not in lead_columns:
            op.add_column(
                "lead_items",
                sa.Column("initial_price_byn", sa.Float(), nullable=True),
            )
        if "market_median_byn" not in lead_columns:
            op.add_column(
                "lead_items",
                sa.Column("market_median_byn", sa.Float(), nullable=True),
            )
        if "duplicate_count" not in lead_columns:
            op.add_column(
                "lead_items",
                sa.Column(
                    "duplicate_count",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            )
        if "last_seen_at" not in lead_columns:
            op.add_column(
                "lead_items",
                sa.Column(
                    "last_seen_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
            )
        if "notes" not in lead_columns:
            op.add_column(
                "lead_items",
                sa.Column("notes", sa.String(length=512), nullable=True),
            )

    # 2. Update the status check constraint to include 'watching' (and
    #    fill in 'researching' / 'skipped' that were already in the
    #    LeadStatusEnum).
    op.execute("ALTER TABLE lead_items DROP CONSTRAINT IF EXISTS chk_lead_items_status")
    op.create_check_constraint(
        "chk_lead_items_status",
        "lead_items",
        f"status IN ({_quoted_status_list(NEW_STATUSES)})",
    )

    # 3. Copy watchlist_items rows into lead_items as status='watching'.
    #    Skip rows where (user_id, ad_id) already has a lead — those were
    #    promoted manually by the user.
    if "watchlist_items" in tables:
        op.execute(
            """
            INSERT INTO lead_items (
                user_id, ad_id, query, title, link,
                price_byn, thumbnail,
                status, source,
                market_status, missing_since_at,
                initial_price_byn, market_median_byn,
                duplicate_count, last_seen_at, notes,
                created_at, updated_at
            )
            SELECT
                w.user_id, w.ad_id, w.query, w.title, w.link,
                w.current_price_byn, w.thumbnail,
                'watching', 'watchlist',
                w.market_status, w.missing_since_at,
                w.initial_price_byn, w.market_median_byn,
                w.duplicate_count, w.last_seen_at, w.notes,
                w.created_at, w.updated_at
            FROM watchlist_items w
            WHERE NOT EXISTS (
                SELECT 1 FROM lead_items l
                WHERE l.user_id = w.user_id AND l.ad_id = w.ad_id
            )
            """
        )
        # 4. Drop legacy table.
        op.drop_table("watchlist_items")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # Restore the legacy table shape.
    if "watchlist_items" not in tables:
        op.create_table(
            "watchlist_items",
            sa.Column(
                "id",
                sa.Integer(),
                autoincrement=True,
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey(
                    "users.id",
                    ondelete="CASCADE",
                    name="fk_watchlist_items_user_id_users",
                ),
                nullable=False,
            ),
            sa.Column("ad_id", sa.BigInteger(), nullable=False),
            sa.Column("query", sa.String(length=255), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("link", sa.String(length=512), nullable=False),
            sa.Column("thumbnail", sa.String(length=512), nullable=True),
            sa.Column("initial_price_byn", sa.Float(), nullable=True),
            sa.Column("current_price_byn", sa.Float(), nullable=True),
            sa.Column(
                "workflow_status",
                sa.String(length=32),
                nullable=False,
                server_default="default",
            ),
            sa.Column(
                "market_status",
                sa.String(length=32),
                nullable=False,
                server_default="active",
            ),
            sa.Column(
                "duplicate_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("market_median_byn", sa.Float(), nullable=True),
            sa.Column("notes", sa.String(length=512), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "missing_since_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "user_id",
                "ad_id",
                name="uq_watchlist_items_user_ad",
            ),
        )
        op.create_index(
            "idx_watchlist_items_user",
            "watchlist_items",
            ["user_id"],
        )
        op.create_index(
            "idx_watchlist_items_market_status",
            "watchlist_items",
            ["market_status"],
        )

        # Move watching leads back into watchlist_items
        op.execute(
            """
            INSERT INTO watchlist_items (
                user_id, ad_id, query, title, link, thumbnail,
                initial_price_byn, current_price_byn,
                workflow_status, market_status, duplicate_count,
                market_median_byn, notes, last_seen_at,
                missing_since_at, created_at, updated_at
            )
            SELECT
                user_id, ad_id, query, title, link, thumbnail,
                COALESCE(initial_price_byn, price_byn), price_byn,
                'default', market_status, duplicate_count,
                market_median_byn, notes, last_seen_at,
                missing_since_at, created_at, updated_at
            FROM lead_items
            WHERE status = 'watching'
            """
        )
        op.execute("DELETE FROM lead_items WHERE status = 'watching'")

    # Restore the old status constraint.
    op.execute("ALTER TABLE lead_items DROP CONSTRAINT IF EXISTS chk_lead_items_status")
    op.create_check_constraint(
        "chk_lead_items_status",
        "lead_items",
        f"status IN ({_quoted_status_list(OLD_STATUSES)})",
    )

    # Drop watchlist-derived columns from lead_items.
    inspector = sa.inspect(bind)
    lead_columns = (
        {col["name"] for col in inspector.get_columns("lead_items")}
        if "lead_items" in inspector.get_table_names()
        else set()
    )
    for col in (
        "notes",
        "last_seen_at",
        "duplicate_count",
        "market_median_byn",
        "initial_price_byn",
    ):
        if col in lead_columns:
            op.drop_column("lead_items", col)
