"""Constrain stored consent rows to the active policy version

Revision ID: 20260511_0008
Revises: 20260511_0007

DB-CRITICAL fix (issues §4.1): the original 0008 migration enforced a
strict ``CHECK (version = '2026.1')`` constraint, which would block
every INSERT once the application code shipped with the new default
``'2026.2'`` (see ``api/models.UserConsent.version``). A deployment
that stopped between 0008 and the follow-up 0011 ('bump consent
policy version') would experience IntegrityError on every consent
record insert.

This revision now creates the forward-compatible
``chk_user_consents_version_known`` constraint with ``IN
('2026.1', '2026.2')`` directly and bumps the column default to
``'2026.2'``. The follow-up 0011 still runs in a fresh deploy — it is
idempotent (drops + recreates the same constraint) — so existing
production databases that already executed both 0008 and 0011 are
unaffected. The unique change is that a partial deploy that lands
0008 alone is now safe.
"""
from collections.abc import Sequence
from contextlib import suppress

import sqlalchemy as sa
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
        # Drop any legacy strict-equality constraint left behind by an
        # older 0008 run and recreate the forward-compat one. IF EXISTS
        # makes the migration idempotent across re-runs / partial states.
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
        # SQLite path (test stand). batch_alter_table rebuilds the
        # table; drop_constraint with NOT EXISTS is unsupported, so
        # we wrap in try/except to remain idempotent.
        with op.batch_alter_table("user_consents") as batch_op:
            with suppress(Exception):
                batch_op.drop_constraint(
                    "chk_user_consents_version_current",
                    type_="check",
                )
            with suppress(Exception):
                batch_op.drop_constraint(
                    "chk_user_consents_version_known",
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
        op.execute(
            "ALTER TABLE user_consents "
            "ALTER COLUMN version SET DEFAULT '2026.1'"
        )
    else:
        with op.batch_alter_table("user_consents") as batch_op:
            with suppress(Exception):
                batch_op.drop_constraint(
                    "chk_user_consents_version_known",
                    type_="check",
                )
            batch_op.alter_column(
                "version",
                existing_type=sa.String(length=16),
                server_default="2026.1",
            )
