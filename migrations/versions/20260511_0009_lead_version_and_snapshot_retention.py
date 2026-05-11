"""Add lead item optimistic-lock version

Revision ID: 20260511_0009
Revises: 20260511_0008

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_0009"
down_revision: str | Sequence[str] | None = "20260511_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dialect_name() -> str:
    bind = op.get_bind()
    return bind.dialect.name if bind is not None else ""


def upgrade() -> None:
    with op.batch_alter_table("lead_items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE lead_items "
            "ADD CONSTRAINT chk_lead_items_version_positive "
            "CHECK (version >= 1)"
        )
    else:
        with op.batch_alter_table("lead_items") as batch_op:
            batch_op.create_check_constraint(
                "chk_lead_items_version_positive",
                "version >= 1",
            )


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE lead_items "
            "DROP CONSTRAINT IF EXISTS chk_lead_items_version_positive"
        )
    else:
        with op.batch_alter_table("lead_items") as batch_op:
            batch_op.drop_constraint(
                "chk_lead_items_version_positive",
                type_="check",
            )
    with op.batch_alter_table("lead_items") as batch_op:
        batch_op.drop_column("version")
