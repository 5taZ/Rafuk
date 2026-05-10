"""Add CHECK constraint on deal_expenses.expense_type (DB-C1)

The model in api/models.py has declared
``CheckConstraint("expense_type IN ('delivery', 'repair', 'customs',
'packaging', 'transport', 'other')")`` since the table was first
introduced (20260407_0010), but no migration ever applied it to
production. That left the column an open string field on disk —
the application enforces the values at the API layer (Pydantic
schemas), but a stray bot job, a manual psql session, or a future
PATCH path could insert anything and break the analytics rollups
that bucket by ``expense_type``.

This migration adds the constraint with the same allowed values
the model declares, plus a NOT VALID + VALIDATE pattern so the
ALTER doesn't lock the table while validating existing rows. If
existing rows have unexpected values they'll fail VALIDATE —
that's the desired behaviour: surface the data drift and let ops
decide whether to backfill or relax the list.

Revision ID: 20260510_0005
Revises: 20260510_0004

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260510_0005"
down_revision: str | Sequence[str] | None = "20260510_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED_TYPES = (
    "'delivery', 'repair', 'customs', "
    "'packaging', 'transport', 'other'"
)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    if dialect == "postgresql":
        # NOT VALID skips the full-table scan during ALTER, so we don't
        # block writers on bigger production tables; VALIDATE then
        # checks existing rows in a non-blocking second pass.
        op.execute(
            "ALTER TABLE deal_expenses "
            "ADD CONSTRAINT chk_deal_expenses_expense_type "
            f"CHECK (expense_type IN ({_ALLOWED_TYPES})) NOT VALID"
        )
        op.execute(
            "ALTER TABLE deal_expenses "
            "VALIDATE CONSTRAINT chk_deal_expenses_expense_type"
        )
    else:
        # SQLite (test runner) does not support NOT VALID — apply the
        # constraint inline. ADD CONSTRAINT is a no-op outside of
        # CREATE TABLE on older SQLite, so wrap in batch_alter_table
        # which rebuilds the table.
        with op.batch_alter_table("deal_expenses") as batch_op:
            batch_op.create_check_constraint(
                "chk_deal_expenses_expense_type",
                f"expense_type IN ({_ALLOWED_TYPES})",
            )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE deal_expenses "
            "DROP CONSTRAINT IF EXISTS chk_deal_expenses_expense_type"
        )
    else:
        with op.batch_alter_table("deal_expenses") as batch_op:
            batch_op.drop_constraint(
                "chk_deal_expenses_expense_type", type_="check"
            )
