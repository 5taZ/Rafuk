"""Allow consent policy version 2026.4 (age 16+, statuses and payment)

LEGAL-08: the 2026.4 user agreement requires users to be at least 16
(Law 99-З, art. 5: below 16 the legal representative consents) and states
that paid subscriptions need a separate public offer. Acceptance now
confirms the age, so every user accepts the agreement again. Only the
version check and default change; no rows are rewritten.

Revision ID: 20261113_0039
Revises: 20261113_0038

"""
from collections.abc import Sequence
from contextlib import suppress

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_0039"
down_revision: str | Sequence[str] | None = "20261113_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = "version IN ('2026.1', '2026.2', '2026.3', '2026.4')"
_OLD = "version IN ('2026.1', '2026.2', '2026.3')"


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
    _replace(_NEW, "2026.4")


def downgrade() -> None:
    op.execute("UPDATE user_consents SET version = '2026.3' WHERE version = '2026.4'")
    _replace(_OLD, "2026.3")
