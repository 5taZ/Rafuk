"""Track eligibility for the new-user Pro trial.

Revision ID: 20261112_0037
Revises: 20261112_0036
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0037"
down_revision: str | Sequence[str] | None = "20261112_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("pro_trial_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "pro_trial_eligible")
