"""add buy_price_byn remove notes from leads

Revision ID: 1a13fdcc7463
Revises: 20260408_0015
Create Date: 2026-04-09 19:28:34.775643

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a13fdcc7463'
down_revision: Union[str, None] = '20260408_0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add buy_price_byn column
    op.add_column('lead_items', sa.Column('buy_price_byn', sa.Numeric(10, 2), nullable=True))
    
    # Remove notes column
    op.drop_column('lead_items', 'notes')


def downgrade() -> None:
    # Add back notes column
    op.add_column('lead_items', sa.Column('notes', sa.String(512), nullable=True))
    
    # Remove buy_price_byn column
    op.drop_column('lead_items', 'buy_price_byn')
