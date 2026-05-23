"""Add price_threshold_alert and discount_alert to tracker_events CHECK

Revision ID: 20260430_0001
Revises: 20260429_0004
Create Date: 2026-04-30

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260430_0001"
down_revision: str | Sequence[str] | None = "20260429_0004"
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
        "event_type IN ('new_listing', 'price_drop', 'trend_reversal', "
        "'price_threshold_alert', 'discount_alert')",
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
        "event_type IN ('new_listing', 'price_drop', 'trend_reversal')",
    )
