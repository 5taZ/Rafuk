"""contacts.phone nullable, watchlist workflow_status default

Revision ID: 20260408_0013
Revises: 20260408_0012
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import BigInteger, DateTime, Integer, String

revision = "20260408_0013"
down_revision = "20260408_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # Make contacts.phone nullable using batch mode (SQLite compatible)
    if "contacts" in tables:
        columns = {col["name"] for col in inspector.get_columns("contacts")}
        if "phone" in columns:
            with op.batch_alter_table("contacts") as batch_op:
                batch_op.alter_column("phone", nullable=True)

    # Update watchlist_items.workflow_status and change default
    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "workflow_status" in columns:
            op.execute(
                "UPDATE watchlist_items SET workflow_status = 'default' "
                "WHERE workflow_status IN ('watching', 'new', 'reviewing', 'skipped')"
            )
            op.execute(
                "UPDATE watchlist_items SET workflow_status = 'important' "
                "WHERE workflow_status IN ('in_progress', 'negotiating')"
            )
            with op.batch_alter_table("watchlist_items") as batch_op:
                batch_op.alter_column(
                    "workflow_status",
                    existing_type=String(32),
                    server_default="default",
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "contacts" in tables:
        columns = {col["name"] for col in inspector.get_columns("contacts")}
        if "phone" in columns:
            with op.batch_alter_table("contacts") as batch_op:
                batch_op.alter_column("phone", nullable=False)

    if "watchlist_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "workflow_status" in columns:
            with op.batch_alter_table("watchlist_items") as batch_op:
                batch_op.alter_column(
                    "workflow_status",
                    existing_type=String(32),
                    server_default="watching",
                )
