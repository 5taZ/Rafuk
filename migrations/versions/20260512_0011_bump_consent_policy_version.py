"""Bump consent policy version to 2026.2

Revision ID: 20260512_0011
Revises: 20260511_0010

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260512_0011"
down_revision: str | Sequence[str] | None = "20260511_0010"
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
            "DROP CONSTRAINT IF EXISTS chk_user_consents_version_current"
        )
        op.execute(
            "ALTER TABLE user_consents "
            "DROP CONSTRAINT IF EXISTS chk_user_consents_version_known"
        )
        op.execute(
            "ALTER TABLE user_consents "
            "ALTER COLUMN version SET DEFAULT '2026.2'"
        )
        op.execute(
            "ALTER TABLE user_consents "
            "ADD CONSTRAINT chk_user_consents_version_known "
            "CHECK (version IN ('2026.1', '2026.2'))"
        )
    else:
        with op.batch_alter_table("user_consents") as batch_op:
            batch_op.drop_constraint(
                "chk_user_consents_version_current",
                type_="check",
            )
            batch_op.alter_column(
                "version",
                existing_type=sa.String(length=16),
                server_default="2026.2",
            )
            batch_op.create_check_constraint(
                "chk_user_consents_version_known",
                "version IN ('2026.1', '2026.2')",
            )


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE user_consents "
            "DROP CONSTRAINT IF EXISTS chk_user_consents_version_known"
        )
        op.execute("UPDATE user_consents SET version = '2026.1' WHERE version = '2026.2'")
        op.execute(
            "ALTER TABLE user_consents "
            "ALTER COLUMN version SET DEFAULT '2026.1'"
        )
        op.execute(
            "ALTER TABLE user_consents "
            "ADD CONSTRAINT chk_user_consents_version_current "
            "CHECK (version = '2026.1')"
        )
    else:
        with op.batch_alter_table("user_consents") as batch_op:
            batch_op.drop_constraint(
                "chk_user_consents_version_known",
                type_="check",
            )
        op.execute("UPDATE user_consents SET version = '2026.1' WHERE version = '2026.2'")
        with op.batch_alter_table("user_consents") as batch_op:
            batch_op.alter_column(
                "version",
                existing_type=sa.String(length=16),
                server_default="2026.1",
            )
            batch_op.create_check_constraint(
                "chk_user_consents_version_current",
                "version = '2026.1'",
            )
