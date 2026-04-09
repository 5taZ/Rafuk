"""add bought and sold to lead status constraint

Revision ID: 59ad46ea83ba
Revises: 1a13fdcc7463
Create Date: 2026-04-09 19:54:47.513696

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59ad46ea83ba'
down_revision: Union[str, None] = '1a13fdcc7463'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old constraint and create new one with 'bought' and 'sold' statuses
    op.drop_constraint('chk_lead_items_status', 'lead_items', type_='check')
    op.create_check_constraint(
        'chk_lead_items_status',
        'lead_items',
        "status IN ('new', 'reviewing', 'in_progress', 'negotiating', 'deferred', 'closed', 'abandoned', 'bought', 'sold')",
    )


def downgrade() -> None:
    # Revert to old constraint without 'bought' and 'sold'
    op.drop_constraint('chk_lead_items_status', 'lead_items', type_='check')
    op.create_check_constraint(
        'chk_lead_items_status',
        'lead_items',
        "status IN ('new', 'reviewing', 'in_progress', 'negotiating', 'deferred', 'closed', 'abandoned')",
    )
