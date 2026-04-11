"""add pause support and enriched event data

Revision ID: 20260410_0009
Revises: 59ad46ea83ba
Create Date: 2026-04-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '20260410_0009'
down_revision: Union[str, None] = '59ad46ea83ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add pause support to trackers
    op.add_column('trackers', sa.Column('paused', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('trackers', sa.Column('pause_reason', sa.String(128), nullable=True))
    op.add_column('trackers', sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True))
    
    # Enrich tracker events with more metadata
    op.add_column('tracker_events', sa.Column('thumbnail', sa.String(512), nullable=True))
    op.add_column('tracker_events', sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('tracker_events', sa.Column('seller_type', sa.String(32), nullable=True))
    op.add_column('tracker_events', sa.Column('region_name', sa.String(64), nullable=True))


def downgrade() -> None:
    # Remove enriched event fields
    op.drop_column('tracker_events', 'region_name')
    op.drop_column('tracker_events', 'seller_type')
    op.drop_column('tracker_events', 'parameters')
    op.drop_column('tracker_events', 'thumbnail')
    
    # Remove pause support
    op.drop_column('trackers', 'paused_at')
    op.drop_column('trackers', 'pause_reason')
    op.drop_column('trackers', 'paused')
