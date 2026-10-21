"""RV-SEC-05 / RV-BE-16: right to erasure (Belarus Law No. 99-З).

``DELETE /account`` used to be undone by the very next request: the Mini
App keeps its signed initData for up to ``telegram_init_data_max_age``
seconds and every authenticated request auto-provisions a ``users`` row.
The pieces that make the deletion stick live here so the router, the auth
dependency, the auto-provision middleware and the user-creation helpers
share one implementation:

* the erasure tombstone ``account:deleted:{tg}`` — refuses initData minted
  before the deletion (``auth_date <= deleted_at``). A freshly opened Mini
  App carries a newer ``auth_date`` and may provision a new account;
* ``purge_user_data`` — explicit bulk DELETEs instead of the ORM cascade;
* ``pseudonymize_admin_audit`` — strips the erased user's identifiers from
  ``admin_audit_log`` while keeping the audit rows.

Cache outages fail open: a lost tombstone means a still-open Mini App
session can re-provision an empty profile (logged), whereas failing closed
would lock every user out while Redis is down.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import (
    AdminAuditLog,
    AIAuditLog,
    Contact,
    DealExpense,
    LeadItem,
    LeadItemPriceSnapshot,
    LeadReminder,
    SavedSearch,
    TelegramNotificationDLQ,
    Tracker,
    TrackerEvent,
    User,
    UserConsent,
)

logger = logging.getLogger(__name__)

ACCOUNT_DELETED_KEY = "account:deleted:{telegram_user_id}"
ACCOUNT_DELETED_ERROR = "account_deleted"
ACCOUNT_DELETED_MESSAGE = (
    "Аккаунт удалён. Чтобы начать заново, закройте приложение и откройте его снова."
)
# Slack on top of the initData lifetime: verify_telegram_init_data accepts
# auth_date up to 5 s in the future and the tombstone lands a moment after
# ``deleted_at`` is taken.
_TOMBSTONE_TTL_MARGIN_SECONDS = 60
# TELEGRAM_INIT_DATA_MAX_AGE <= 0 disables the age check, but the tombstone
# still must not keep a Telegram id around forever.
_TOMBSTONE_UNBOUNDED_TTL_SECONDS = 7 * 24 * 3600

# Keys of admin_audit_log payloads that identify nobody once the row is
# unlinked from the user (api/routers/admin.py: _user_status_payload,
# _status_limit_payload). Everything else — target_telegram_user_id, the
# admin's free-text note, keys added later — is dropped: an allowlist fails
# safe when the payload grows.
_ADMIN_AUDIT_RETAINED_KEYS = frozenset({
    "old",
    "new",
    "status_code",
    "granted_at",
    "expires_at",
    "ai_daily_limit",
    "assistant_daily_limit",
})


def account_deleted_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={"error": ACCOUNT_DELETED_ERROR, "message": ACCOUNT_DELETED_MESSAGE},
    )


def tombstone_ttl_seconds(init_data_max_age: int) -> int:
    if init_data_max_age <= 0:
        return _TOMBSTONE_UNBOUNDED_TTL_SECONDS
    return int(init_data_max_age) + _TOMBSTONE_TTL_MARGIN_SECONDS


def init_data_auth_date(telegram_user: Any) -> int | None:
    """``auth_date`` of a verified initData; None for the bot's service-token
    principal and the auth-bypass user, which carry no signed session."""
    raw = getattr(telegram_user, "raw", None)
    if not isinstance(raw, dict):
        return None
    try:
        auth_date = int(raw.get("auth_date") or 0)
    except (TypeError, ValueError):
        return None
    return auth_date if auth_date > 0 else None


def _tombstone_key(telegram_user_id: int) -> str:
    return ACCOUNT_DELETED_KEY.format(telegram_user_id=int(telegram_user_id))


async def mark_account_deleted(
    cache: Any,
    telegram_user_id: int,
    *,
    deleted_at: float,
    ttl_seconds: int,
) -> bool:
    """Write the tombstone; False when it did not stick (no cache, Redis down)."""
    if cache is None:
        return False
    key = _tombstone_key(telegram_user_id)
    try:
        await cache.set(key, f"{deleted_at:.6f}", ttl=ttl_seconds)
        # RedisCache.set swallows RedisError, so read back to learn the outcome.
        return await cache.get(key) is not None
    except Exception:  # noqa: BLE001 — erasure must not depend on the cache
        logger.warning(
            "account_erasure.tombstone_write_failed telegram_user_id=%s",
            telegram_user_id, exc_info=True,
        )
        return False


async def clear_account_tombstone(cache: Any, telegram_user_id: int) -> None:
    if cache is None:
        return
    try:
        await cache.delete(_tombstone_key(telegram_user_id))
    except Exception:  # noqa: BLE001 — best effort, the key expires on its own
        logger.warning(
            "account_erasure.tombstone_clear_failed telegram_user_id=%s",
            telegram_user_id, exc_info=True,
        )


async def account_deleted_at(cache: Any, telegram_user_id: int) -> float | None:
    if cache is None:
        return None
    try:
        raw = await cache.get(_tombstone_key(telegram_user_id))
    except Exception:  # noqa: BLE001 — fail open, see module docstring
        logger.warning(
            "account_erasure.tombstone_lookup_failed telegram_user_id=%s",
            telegram_user_id, exc_info=True,
        )
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def is_erased_session(cache: Any, telegram_user_id: int, auth_date: int | None) -> bool:
    """True when the initData was minted before the account was deleted."""
    if auth_date is None:
        return False
    deleted_at = await account_deleted_at(cache, telegram_user_id)
    return deleted_at is not None and auth_date <= deleted_at


@dataclass(frozen=True, slots=True)
class _RequestPrincipal:
    telegram_user_id: int
    auth_date: int | None
    cache: Any


# Set by get_telegram_user for the current request. The user-creation
# helpers (workflow_store.ensure_user, account_status.get_or_create_profile_user)
# take only a DB session, so they read the principal from here instead of
# every router passing the cache and initData through.
_current_principal: ContextVar[_RequestPrincipal | None] = ContextVar(
    "rv_sec05_current_principal", default=None,
)


def remember_request_principal(telegram_user: Any, cache: Any) -> None:
    _current_principal.set(_RequestPrincipal(
        telegram_user_id=int(telegram_user.user_id),
        auth_date=init_data_auth_date(telegram_user),
        cache=cache,
    ))


async def ensure_user_creation_allowed(telegram_user_id: int) -> None:
    """Refuse to create a ``users`` row from an initData that predates the
    account deletion.

    get_telegram_user already rejects such initData; this closes the race
    of a request that authenticated just before ``DELETE /account`` and
    reaches the insert after it committed.
    """
    principal = _current_principal.get()
    if principal is None or principal.telegram_user_id != int(telegram_user_id):
        return
    if await is_erased_session(principal.cache, principal.telegram_user_id, principal.auth_date):
        raise account_deleted_error()


def scrub_admin_audit_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: scrub_admin_audit_payload(value)
            for key, value in payload.items()
            if key in _ADMIN_AUDIT_RETAINED_KEYS
        }
    if isinstance(payload, list):
        return [scrub_admin_audit_payload(item) for item in payload]
    return payload


def _pseudonymized_admin_audit_payload(payload: Any) -> dict[str, Any]:
    scrubbed = scrub_admin_audit_payload(payload) if isinstance(payload, dict) else {}
    return {**scrubbed, "target_erased": True}


async def pseudonymize_admin_audit(session: AsyncSession, *, user_id: int) -> int:
    """RV-SEC-05: keep the admin-audit rows about an erased user but drop
    what identifies them.

    ``target_user_id`` is ON DELETE SET NULL, which left the Telegram id and
    the admin's note in ``payload`` for good. Both foreign keys are cleared
    explicitly so SQLite (FKs off in tests) ends up like Postgres. Rows the
    erased user wrote as an admin keep their payload — it describes the
    target, not the actor.
    """
    rows = (
        await session.execute(
            select(AdminAuditLog.id, AdminAuditLog.payload)
            .where(AdminAuditLog.target_user_id == user_id)
        )
    ).all()
    if rows:
        await session.execute(
            update(AdminAuditLog),
            [
                {
                    "id": row_id,
                    "target_user_id": None,
                    "payload": _pseudonymized_admin_audit_payload(payload),
                }
                for row_id, payload in rows
            ],
        )
    await session.execute(
        update(AdminAuditLog)
        .where(AdminAuditLog.actor_user_id == user_id)
        .values(actor_user_id=None)
        .execution_options(synchronize_session=False)
    )
    return len(rows)


async def purge_user_data(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    user_id: int | None,
) -> dict[str, int]:
    """RV-BE-16: delete everything the user owns with one bulk DELETE per table.

    ``session.delete(user)`` walked the ``cascade="all, delete-orphan"``
    relationships: it loaded every tracker, event, lead, snapshot and up to
    a year of ``ai_audit_log`` into the worker and deleted them row by row.
    Children go first, so the result does not depend on ON DELETE CASCADE
    (off on SQLite) and the partitioned ``ai_audit_log`` is hit with one
    ``user_id`` predicate instead of per-row primary keys. Rows tied to the
    user's leads/trackers are matched both ways, mirroring what the Postgres
    cascade would remove. The caller owns the transaction.
    """
    counts: dict[str, int] = {}

    async def _delete(table: str, stmt: Any) -> None:
        result = await session.execute(stmt.execution_options(synchronize_session=False))
        counts[table] = int(result.rowcount or 0)

    dlq_scope = TelegramNotificationDLQ.telegram_user_id == telegram_user_id
    if user_id is not None:
        dlq_scope = or_(dlq_scope, TelegramNotificationDLQ.user_id == user_id)
    await _delete("telegram_notification_dlq", delete(TelegramNotificationDLQ).where(dlq_scope))
    if user_id is None:
        return counts

    lead_ids = select(LeadItem.id).where(LeadItem.user_id == user_id)
    tracker_ids = select(Tracker.id).where(Tracker.user_id == user_id)
    await _delete(
        "lead_item_price_snapshots",
        delete(LeadItemPriceSnapshot).where(LeadItemPriceSnapshot.lead_item_id.in_(lead_ids)),
    )
    await _delete(
        "deal_expenses",
        delete(DealExpense).where(
            or_(DealExpense.user_id == user_id, DealExpense.lead_id.in_(lead_ids))
        ),
    )
    await _delete(
        "lead_reminders",
        delete(LeadReminder).where(
            or_(LeadReminder.user_id == user_id, LeadReminder.lead_id.in_(lead_ids))
        ),
    )
    await _delete(
        "tracker_events",
        delete(TrackerEvent).where(
            or_(TrackerEvent.user_id == user_id, TrackerEvent.tracker_id.in_(tracker_ids))
        ),
    )
    for table, model in (
        ("lead_items", LeadItem),
        ("trackers", Tracker),
        ("saved_searches", SavedSearch),
        ("contacts", Contact),
        ("user_consents", UserConsent),
        ("ai_audit_log", AIAuditLog),
    ):
        await _delete(table, delete(model).where(model.user_id == user_id))
    counts["admin_audit_log"] = await pseudonymize_admin_audit(session, user_id=user_id)
    await _delete("users", delete(User).where(User.id == user_id))
    return counts
