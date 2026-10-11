"""add saved searches and tracker filters

Revision ID: 20260406_0005
Revises: 20260406_0004
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260406_0005"
down_revision = "20260406_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tracker_columns = {column["name"] for column in inspector.get_columns("trackers")}

    tracker_additions = {
        "min_discount_percent": sa.Column("min_discount_percent", sa.Float(), nullable=True),
        "max_price_byn": sa.Column("max_price_byn", sa.Float(), nullable=True),
        "seller_type": sa.Column("seller_type", sa.String(length=32), nullable=True),
        "condition": sa.Column("condition", sa.String(length=32), nullable=True),
        "region_name": sa.Column("region_name", sa.String(length=64), nullable=True),
        "config_keyword": sa.Column("config_keyword", sa.String(length=128), nullable=True),
        "exclude_duplicates": sa.Column(
            "exclude_duplicates",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    }
    for name, column in tracker_additions.items():
        if name not in tracker_columns:
            op.add_column("trackers", column)

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "saved_searches" not in tables:
        op.create_table(
            "saved_searches",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("group_name", sa.String(length=128), nullable=True),
            sa.Column("query", sa.String(length=255), nullable=False),
            sa.Column(
                "strict_mode",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "target_discount_percent",
                sa.Float(),
                nullable=False,
                server_default="10",
            ),
            sa.Column("max_price_byn", sa.Float(), nullable=True),
            sa.Column("seller_type", sa.String(length=32), nullable=True),
            sa.Column("condition", sa.String(length=32), nullable=True),
            sa.Column("region_name", sa.String(length=64), nullable=True),
            sa.Column("config_keyword", sa.String(length=128), nullable=True),
            sa.Column(
                "exclude_duplicates",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        saved_search_columns = {
            column["name"] for column in inspector.get_columns("saved_searches")
        }
        saved_search_additions = {
            "group_name": sa.Column("group_name", sa.String(length=128), nullable=True),
            "exclude_duplicates": sa.Column(
                "exclude_duplicates",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        }
        for name, column in saved_search_additions.items():
            if name not in saved_search_columns:
                op.add_column("saved_searches", column)

    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("saved_searches")}
    if "idx_saved_searches_user" not in indexes:
        op.create_index("idx_saved_searches_user", "saved_searches", ["user_id"])
    if "idx_saved_searches_active" not in indexes:
        op.create_index("idx_saved_searches_active", "saved_searches", ["active"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "saved_searches" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("saved_searches")}
        if "idx_saved_searches_active" in indexes:
            op.drop_index("idx_saved_searches_active", table_name="saved_searches")
        if "idx_saved_searches_user" in indexes:
            op.drop_index("idx_saved_searches_user", table_name="saved_searches")
        op.drop_table("saved_searches")

    inspector = sa.inspect(bind)
    tracker_columns = {column["name"] for column in inspector.get_columns("trackers")}
    for name in (
        "config_keyword",
        "exclude_duplicates",
        "region_name",
        "condition",
        "seller_type",
        "max_price_byn",
        "min_discount_percent",
    ):
        if name in tracker_columns:
            op.drop_column("trackers", name)
