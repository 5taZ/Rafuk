"""mark trackers that were inactive before this deploy as deleted

/start resumes trackers with active = false and deleted_at IS NULL, the state the scheduler
leaves when a user blocks the bot. Before the refactoring the bot's /untrack (and the API
before 0009 added the column) cleared only ``active``, so those deletions looked exactly like
pauses and /start brought them back. Every tracker that is inactive at migration time is
treated as deleted, so only trackers paused after this deploy can be resumed.

Revision ID: 20261011_0011
Revises: 20261011_0010
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261011_0011"
down_revision = "20261011_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "trackers" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("trackers")}
    if not {"active", "deleted_at"} <= columns:
        return
    # "NOT active" works for both Postgres booleans and SQLite's 0/1 integers.
    bind.execute(
        sa.text(
            "UPDATE trackers SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE NOT active AND deleted_at IS NULL"
        )
    )


def downgrade() -> None:
    # Data-only revision: the rows it marked cannot be told apart from real deletions, and
    # the previous schema has the same columns, so there is nothing to undo.
    pass
