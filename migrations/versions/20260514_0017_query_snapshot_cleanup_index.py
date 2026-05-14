"""Add query snapshot cleanup index

Revision ID: 20260514_0017
Revises: 20260514_0016

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260514_0017"
down_revision: str | Sequence[str] | None = "20260514_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dialect_name() -> str:
    bind = op.get_bind()
    return bind.dialect.name if bind is not None else ""


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_query_snapshots_snapshot_at "
        "ON query_snapshots (snapshot_at)"
    )
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute("ANALYZE query_snapshots")
    elif dialect == "sqlite":
        op.execute("ANALYZE")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_query_snapshots_snapshot_at")
