"""Fix tracker_events CHECK constraint to include trend_reversal

Revision ID: 20260429_0001
Revises: 20260428_0002
Create Date: 2026-04-29

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260429_0001"
down_revision: str | None = "20260428_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    inspector = __import__("sqlalchemy").inspect(bind)
    constraints = [
        c["name"]
        for c in inspector.get_check_constraints("tracker_events")
        if c["name"]
    ]
    if "chk_tracker_events_event_type" in constraints:
        op.drop_constraint(
            "chk_tracker_events_event_type",
            "tracker_events",
            type_="check",
        )
    op.create_check_constraint(
        "chk_tracker_events_event_type",
        "tracker_events",
        "event_type IN ('new_listing', 'price_drop', 'trend_reversal')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    inspector = __import__("sqlalchemy").inspect(bind)
    constraints = [
        c["name"]
        for c in inspector.get_check_constraints("tracker_events")
        if c["name"]
    ]
    if "chk_tracker_events_event_type" in constraints:
        op.drop_constraint(
            "chk_tracker_events_event_type",
            "tracker_events",
            type_="check",
        )
    op.create_check_constraint(
        "chk_tracker_events_event_type",
        "tracker_events",
        "event_type IN ('new_listing', 'price_drop')",
    )
