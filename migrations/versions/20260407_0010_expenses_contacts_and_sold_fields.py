"""add deal_expenses, contacts tables and sold fields to lead_items

Revision ID: 20260407_0010
Revises: 20260407_0009
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String

revision = "20260407_0010"
down_revision = "20260407_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # Step 1: Add sold fields to lead_items
    if "lead_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("lead_items")}

        if "sold_price_byn" not in columns:
            op.add_column(
                "lead_items",
                sa.Column("sold_price_byn", Numeric(10, 2), nullable=True),
            )

        if "sold_at" not in columns:
            op.add_column(
                "lead_items",
                sa.Column("sold_at", DateTime(timezone=True), nullable=True),
            )

    # Step 2: Create deal_expenses table
    if "deal_expenses" not in tables:
        op.create_table(
            "deal_expenses",
            sa.Column("id", Integer, primary_key=True, autoincrement=True),
            sa.Column("lead_id", Integer, sa.ForeignKey("lead_items.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("user_id", BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("expense_type", String(32), nullable=False),
            sa.Column("amount_byn", Numeric(10, 2), nullable=False),
            sa.Column("notes", String(255), nullable=True),
            sa.Column("expense_date", DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_deal_expenses_lead", "deal_expenses", ["lead_id"])
        op.create_index("idx_deal_expenses_user", "deal_expenses", ["user_id"])

    # Step 3: Create contacts table
    if "contacts" not in tables:
        op.create_table(
            "contacts",
            sa.Column("id", Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("phone", String(32), nullable=False),
            sa.Column("seller_name", String(128), nullable=True),
            sa.Column("kufar_profile", String(512), nullable=True),
            sa.Column("saved_at", DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "phone", name="uq_contacts_user_phone"),
        )
        op.create_index("idx_contacts_user", "contacts", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # Drop contacts table
    if "contacts" in tables:
        op.drop_index("idx_contacts_user", table_name="contacts")
        op.drop_table("contacts")

    # Drop deal_expenses table
    if "deal_expenses" in tables:
        op.drop_index("idx_deal_expenses_lead", table_name="deal_expenses")
        op.drop_index("idx_deal_expenses_user", table_name="deal_expenses")
        op.drop_table("deal_expenses")

    # Drop sold fields from lead_items
    if "lead_items" in tables:
        columns = {col["name"] for col in inspector.get_columns("lead_items")}

        if "sold_at" in columns:
            op.drop_column("lead_items", "sold_at")

        if "sold_price_byn" in columns:
            op.drop_column("lead_items", "sold_price_byn")
