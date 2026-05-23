"""add_updated_at_to_trackers

Revision ID: 7b413345fcf2
Revises: 6040ea4a0fd6
Create Date: 2026-05-09 17:16:33.067229

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7b413345fcf2'
down_revision: str | None = '6040ea4a0fd6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'trackers',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('trackers', 'updated_at')
