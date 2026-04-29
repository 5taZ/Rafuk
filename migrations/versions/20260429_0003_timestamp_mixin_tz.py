"""Add timezone to TimestampMixin.created_at columns

Revision ID: 20260429_0003
Revises: 20260429_0002
Create Date: 2026-04-29

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260429_0003"
down_revision: str | Sequence[str] | None = ("20260429_0002", "20260427_0003")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # TimestampMixin is used by Tracker and LeadItem
    # Table names are hardcoded (not from user input) so f-string is safe here,
    # but use text() for explicit parameterization style.
    from sqlalchemy import text

    for table in ("trackers", "lead_items"):
        op.execute(
            text(
                f"ALTER TABLE {table} ALTER COLUMN created_at "
                f"TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC'"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    from sqlalchemy import text

    for table in ("trackers", "lead_items"):
        op.execute(
            text(
                f"ALTER TABLE {table} ALTER COLUMN created_at "
                f"TYPE TIMESTAMP USING created_at AT TIME ZONE 'UTC'"
            )
        )
