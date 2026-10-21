"""RV-DB-05: create the CHECK constraints that only existed in the model

``chk_deal_expenses_amount_positive`` and
``chk_telegram_notification_dlq_retry_count_bounds`` have been declared in
``api/models.py`` since wave 169, but no migration ever created them, so
production accepted rows the model forbids. The RV-DB-01 drift test
(``tests/test_migrations.py``) now compares named CHECK constraints
between the models and the migrated schema, so this cannot recur silently.

PostgreSQL: ``ADD ... NOT VALID`` needs only a brief ACCESS EXCLUSIVE lock
(bounded by a 5 s lock_timeout) and skips the table scan; ``VALIDATE``
then runs in an autocommit block, so the scan holds SHARE UPDATE
EXCLUSIVE (reads and writes go on) instead of the ACCESS EXCLUSIVE lock
the migration transaction would keep until the whole run commits. Note
that the autocommit block commits the migrations run before this one
(env.py still runs a single transaction, RV-DB-02). If existing rows
break a constraint, VALIDATE fails: the NOT VALID constraint stays and
already guards new writes, and the upgrade can simply be re-run after the
data is fixed (the ADD is preceded by ``DROP CONSTRAINT IF EXISTS``).

SQLite (test stand): batch mode rebuilds the table with the CHECK.

Revision ID: 20261021_0027
Revises: 20261021_0026
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0027"
down_revision: str | Sequence[str] | None = "20261021_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, constraint, condition) — conditions are verbatim from api/models.py.
_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("deal_expenses", "chk_deal_expenses_amount_positive", "amount_byn > 0"),
    (
        "telegram_notification_dlq",
        "chk_telegram_notification_dlq_retry_count_bounds",
        "retry_count >= 0 AND retry_count <= 10",
    ),
)


def _dialect_name() -> str:
    bind = op.get_bind()
    return bind.dialect.name if bind is not None else ""


def _existing_checks(table: str) -> set[str]:
    return {
        check["name"]
        for check in sa.inspect(op.get_bind()).get_check_constraints(table)
        if check.get("name")
    }


def upgrade() -> None:
    if _dialect_name() == "postgresql":
        # Give up instead of queueing every query on these tables behind the
        # ALTER while some long transaction holds them; SET LOCAL ends with
        # the transaction the autocommit block below commits.
        op.execute("SET LOCAL lock_timeout = '5s'")
        for table, name, condition in _CHECKS:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID"
            )
        with op.get_context().autocommit_block():
            for table, name, _condition in _CHECKS:
                op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
        return

    for table, name, condition in _CHECKS:
        if name in _existing_checks(table):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.create_check_constraint(name, condition)


def downgrade() -> None:
    if _dialect_name() == "postgresql":
        for table, name, _condition in _CHECKS:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        return

    for table, name, _condition in _CHECKS:
        if name not in _existing_checks(table):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(name, type_="check")
