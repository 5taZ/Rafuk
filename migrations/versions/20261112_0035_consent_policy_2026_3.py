"""Allow consent policy version 2026.3 (user agreement acceptance)

LEGAL-02: the 2026.3 policy names the operator and the legal basis of each
purpose and adds the "terms" record (acceptance of the user agreement).
Only the version check and default change; no rows are rewritten.

Revision ID: 20261112_0035
Revises: 20261108_0034

"""
from collections.abc import Sequence
from contextlib import suppress

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0035"
down_revision: str | Sequence[str] | None = "20261108_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = "version IN ('2026.1', '2026.2', '2026.3')"
_OLD = "version IN ('2026.1', '2026.2')"


def _replace(check: str, default: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE user_consents DROP CONSTRAINT IF EXISTS chk_user_consents_version_known"
        )
        op.execute(f"ALTER TABLE user_consents ALTER COLUMN version SET DEFAULT '{default}'")
        op.execute(
            "ALTER TABLE user_consents ADD CONSTRAINT chk_user_consents_version_known "
            f"CHECK ({check})"
        )
        return
    with op.batch_alter_table("user_consents") as batch_op:
        with suppress(Exception):
            batch_op.drop_constraint("chk_user_consents_version_known", type_="check")
        batch_op.alter_column(
            "version", existing_type=sa.String(length=16), server_default=default,
        )
        batch_op.create_check_constraint("chk_user_consents_version_known", check)


def upgrade() -> None:
    _replace(_NEW, "2026.3")


def downgrade() -> None:
    op.execute("UPDATE user_consents SET version = '2026.2' WHERE version = '2026.3'")
    _replace(_OLD, "2026.2")
