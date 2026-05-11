"""Constrain stored consent rows to the active policy version

Revision ID: 20260511_0008
Revises: 20260511_0007

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260511_0008"
down_revision: str | Sequence[str] | None = "20260511_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dialect_name() -> str:
    bind = op.get_bind()
    return bind.dialect.name if bind is not None else ""


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE user_consents "
            "ADD CONSTRAINT chk_user_consents_version_current "
            "CHECK (version = '2026.1')"
        )
    else:
        with op.batch_alter_table("user_consents") as batch_op:
            batch_op.create_check_constraint(
                "chk_user_consents_version_current",
                "version = '2026.1'",
            )


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE user_consents "
            "DROP CONSTRAINT IF EXISTS chk_user_consents_version_current"
        )
    else:
        with op.batch_alter_table("user_consents") as batch_op:
            batch_op.drop_constraint(
                "chk_user_consents_version_current",
                type_="check",
            )
