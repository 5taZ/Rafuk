"""Add ip_address to ai_audit_log

Revision ID: 20260513_0015
Revises: 20260513_0014

OPUS-17: Law-99-З audit traceability — capture the trusted client
IP at AI-decision time so the audit row matches the IP UserConsent
already records on grant.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260513_0015"
down_revision: str | Sequence[str] | None = "20260513_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_audit_log") as batch_op:
        batch_op.add_column(sa.Column("ip_address", sa.String(length=45), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_audit_log") as batch_op:
        batch_op.drop_column("ip_address")
