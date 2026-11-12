"""One Pro trial per Telegram account, surviving account erasure

TRIAL-01: ``users.pro_trial_eligible`` lives on the row that account
erasure deletes, so deleting the account and reopening the Mini App granted
a new trial every three days. ``pro_trial_claims`` keeps an HMAC of the
Telegram ID of every account that has had the trial or predates it. The
backfill covers rows already ineligible (accounts from before 0037 and
those whose trial was granted); new accounts still waiting for their trial
stay eligible.

Revision ID: 20261113_0038
Revises: 20261112_0037

"""
import hashlib
import hmac
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from api.config import get_settings

revision: str = "20261113_0038"
down_revision: str | Sequence[str] | None = "20261112_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _marker(key: bytes, telegram_user_id: int) -> str:
    # Must stay identical to api.services.account_status.pro_trial_marker.
    return hmac.new(key, str(int(telegram_user_id)).encode(), hashlib.sha256).hexdigest()


def upgrade() -> None:
    op.create_table(
        "pro_trial_claims",
        sa.Column("telegram_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "claimed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    ids = op.get_bind().execute(
        sa.text("SELECT telegram_user_id FROM users WHERE pro_trial_eligible = :no"),
        {"no": False},
    ).scalars().all()
    if not ids:
        return
    token = get_settings().bot_token.get_secret_value()
    key = hashlib.sha256(b"rafuk-pro-trial\0" + token.encode()).digest()
    claims = sa.table("pro_trial_claims", sa.column("telegram_hash", sa.String))
    op.bulk_insert(claims, [{"telegram_hash": _marker(key, tg_id)} for tg_id in set(ids)])


def downgrade() -> None:
    op.drop_table("pro_trial_claims")
