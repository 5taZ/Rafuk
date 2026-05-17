"""Consent and account management router — PD processing, AI consent, data export/deletion."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import get_settings
from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.limiter import limiter
from api.models import (
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
from api.schemas import (
    AccountDeletionConfirmation,
    AIConsentInfoResponse,
    ConsentGrantRequest,
    ConsentStatusResponse,
)
from api.services.ai_audit import audit_text_sha256, sanitize_audit_text
from api.services.client_ip import get_client_ip
from api.services.consent_policy import CURRENT_POLICY_VERSION, VALID_CONSENT_TYPES
from api.services.workflow_store import ensure_user, resolve_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


def _ai_provider_host(base_url: str) -> str:
    try:
        return urlparse(base_url).hostname or "unknown"
    except ValueError:
        return "unknown"


def _default_ai_provider_name(host: str) -> str:
    if host == "generativelanguage.googleapis.com":
        return "Google Gemini API"
    if host.endswith("together.xyz"):
        return "Together AI"
    return host or "external AI provider"


def _default_ai_provider_region(host: str) -> str:
    if host == "generativelanguage.googleapis.com" or host.endswith("together.xyz"):
        return "США"
    return ""


def _money(value) -> float | None:
    return float(value) if value is not None else None


@router.get("/ai-consent-info", response_model=AIConsentInfoResponse)
@limiter.limit("20/minute")
async def get_ai_consent_info(
    request: Request,
    _user=Depends(get_telegram_user),
):
    settings = get_settings()
    host = _ai_provider_host(settings.ai_base_url)
    provider = (
        (settings.ai_provider_name or _default_ai_provider_name(host)).strip()
        or _default_ai_provider_name(host)
    )
    region = (settings.ai_provider_region or _default_ai_provider_region(host)).strip()
    model = settings.ai_model.strip() or "configured model"
    display_label = f"{provider}, модель {model}"
    if region:
        display_label = f"{display_label} ({region})"
    return AIConsentInfoResponse(
        provider_name=provider,
        provider_region=region,
        provider_host=host,
        model=model,
        policy_version=CURRENT_POLICY_VERSION,
        display_label=display_label,
    )


@router.get("/consent/{consent_type}", response_model=ConsentStatusResponse)
@limiter.limit("10/minute")
async def get_consent_status(
    request: Request,
    consent_type: str,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Check whether the current user has granted a specific consent."""
    if consent_type not in VALID_CONSENT_TYPES:
        raise HTTPException(
            status_code=400, detail="Указанный тип согласия не найден"
        )

    async with session_factory() as session:
        uid = await resolve_user_id(session, _user.user_id)
        if uid is None:
            return ConsentStatusResponse(consent_type=consent_type, granted=False)

        stmt = (
            select(UserConsent)
            .where(
                UserConsent.user_id == uid,
                UserConsent.consent_type == consent_type,
                UserConsent.revoked_at.is_(None),
            )
            .order_by(UserConsent.granted_at.desc())
            .limit(1)
        )
        consent = (await session.execute(stmt)).scalar_one_or_none()

    if consent:
        # Consent is only valid if it matches the current policy version.
        # When CURRENT_POLICY_VERSION is bumped, users must re-consent.
        if consent.version != CURRENT_POLICY_VERSION:
            return ConsentStatusResponse(consent_type=consent_type, granted=False)
        return ConsentStatusResponse(
            consent_type=consent_type,
            granted=True,
            version=consent.version,
            granted_at=consent.granted_at,
        )
    return ConsentStatusResponse(consent_type=consent_type, granted=False)


@router.post("/consent", response_model=ConsentStatusResponse, status_code=201)
@limiter.limit("10/minute")
async def grant_consent(
    request: Request,
    payload: ConsentGrantRequest,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Grant a consent (e.g. AI analysis, PD processing, cross-border transfer)."""
    # A-6: runtime check removed — ConsentGrantRequest.consent_type is a
    # Literal, so Pydantic returns 422 for invalid values before we get here.

    # Reject stale consent versions — forces re-consent when policy changes
    if payload.version != CURRENT_POLICY_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Устаревшая версия политики. Текущая: {CURRENT_POLICY_VERSION}",
        )

    async with session_factory() as session:
        uid = await ensure_user(
            session,
            telegram_user_id=_user.user_id,
            first_name=getattr(_user, "first_name", ""),
        )

        # Check if already granted with current version
        stmt = (
            select(UserConsent)
            .where(
                UserConsent.user_id == uid,
                UserConsent.consent_type == payload.consent_type,
                UserConsent.revoked_at.is_(None),
            )
            .order_by(UserConsent.granted_at.desc())
            .limit(1)
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing and existing.version == payload.version:
            return ConsentStatusResponse(
                consent_type=payload.consent_type,
                granted=True,
                version=existing.version,
                granted_at=existing.granted_at,
            )

        # Revoke any previous consents of the same type
        if existing:
            existing.revoked_at = datetime.now(UTC)

        # Capture client IP for audit trail (Belarus Law No. 99-З)
        client_ip = get_client_ip(request)

        consent = UserConsent(
            user_id=uid,
            consent_type=payload.consent_type,
            version=payload.version,
            ip_address=client_ip,
        )
        session.add(consent)
        try:
            await session.commit()
        except IntegrityError:
            # DB-M6: the new UNIQUE partial index on
            # (user_id, consent_type) WHERE revoked_at IS NULL makes
            # two concurrent grant_consent calls for the same (user,
            # type) pair impossible at the storage layer. The loser
            # of the race lands here — we've rolled back, so re-open
            # a fresh transaction, fetch whichever row did land, and
            # return it. The user sees the same "granted" outcome
            # they would have seen on a sequential retry.
            await session.rollback()
            winner = await session.scalar(
                select(UserConsent)
                .where(
                    UserConsent.user_id == uid,
                    UserConsent.consent_type == payload.consent_type,
                    UserConsent.revoked_at.is_(None),
                )
                .order_by(UserConsent.granted_at.desc())
                .limit(1)
            )
            if winner is None:
                # Extremely unlikely — integrity error without a
                # surviving row means some other code path deleted
                # it between our commit and our re-read. Surface as
                # 500 rather than pretend success.
                raise
            logger.info(
                "User %d grant_consent raced with a sibling request; "
                "returning the surviving active consent",
                _user.user_id,
            )
            return ConsentStatusResponse(
                consent_type=payload.consent_type,
                granted=True,
                version=winner.version,
                granted_at=winner.granted_at,
            )
        await session.refresh(consent)

    logger.info(
        "User %d granted consent %s v%s", _user.user_id, payload.consent_type, payload.version
    )

    return ConsentStatusResponse(
        consent_type=payload.consent_type,
        granted=True,
        version=consent.version,
        granted_at=consent.granted_at,
    )


@router.delete("/consent/{consent_type}", status_code=204)
@limiter.limit("10/minute")
async def revoke_consent(
    request: Request,
    consent_type: str,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Revoke a previously granted consent."""
    if consent_type not in VALID_CONSENT_TYPES:
        raise HTTPException(
            status_code=400, detail="Указанный тип согласия не найден"
        )

    revoked_count = 0
    async with session_factory() as session:
        uid = await resolve_user_id(session, _user.user_id)
        if uid is None:
            return

        stmt = (
            select(UserConsent)
            .where(
                UserConsent.user_id == uid,
                UserConsent.consent_type == consent_type,
                UserConsent.revoked_at.is_(None),
            )
        )
        results = (await session.execute(stmt)).scalars().all()
        now = datetime.now(UTC)
        for consent in results:
            consent.revoked_at = now
        revoked_count = len(results)

        # C-08: ``pd_processing`` is the broadest consent — revoking
        # it means the user has withdrawn permission to process their
        # personal data. Tracker notifications keep flowing through
        # the scheduler unless the trackers themselves are paused, so
        # we pause every active tracker for this user with a
        # distinguishable reason. Re-granting + manually un-pausing is
        # an explicit re-opt-in. Other consent types (``ai_analysis``,
        # ``cross_border``) only revoke specific feature scopes and
        # do not require pausing trackers.
        paused_tracker_count = 0
        if revoked_count and consent_type == "pd_processing":
            pause_result = await session.execute(
                update(Tracker)
                .where(
                    Tracker.user_id == uid,
                    Tracker.paused.is_(False),
                )
                .values(
                    paused=True,
                    paused_at=now,
                    pause_reason="consent_revoked",
                    updated_at=now,
                )
            )
            paused_tracker_count = pause_result.rowcount or 0

        await session.commit()

    if revoked_count:
        logger.info("User %d revoked consent %s", _user.user_id, consent_type)
        if consent_type == "pd_processing":
            logger.info(
                "User %d revoked pd_processing — paused %d active tracker(s) "
                "with pause_reason='consent_revoked'",
                _user.user_id, paused_tracker_count,
            )
        # OPUS-1: revoke means the previously processed AI artefacts
        # (task results, listing-assistant cache, rate-limit buckets,
        # initdata replay tracker) must also be evicted — otherwise
        # the user has revoked permission to process while data we
        # already processed is still cached. Same best-effort
        # contract as delete_account: log + continue on failure so
        # the user's revoke acknowledgement isn't blocked by a
        # transient Redis blip.
        try:
            from api.routers.ai_analysis import clear_user_ai_data  # noqa: PLC0415 — avoid cycle

            # OPUS-20: hand the warm app cache through so the cleanup
            # doesn't open + close a fresh Redis pool just for this
            # single user.
            await clear_user_ai_data(
                _user.user_id,
                cache=getattr(request.app.state, "cache", None),
                # AI-HIGH (issues §3.2): hand over the session factory
                # so the helper can walk this user's audit log and
                # evict the (ad_id, query) entries from the shared
                # ai_analysis cache too.
                session_factory=request.app.state.session_factory,
            )
        except Exception:
            logger.warning(
                "Failed to clear per-user cache for user %d after consent revoke",
                _user.user_id,
                exc_info=True,
            )


@router.delete("", status_code=204)
@limiter.limit("10/minute")
async def delete_account(
    request: Request,
    payload: AccountDeletionConfirmation = Body(...),
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Delete all user data (right to erasure under Belarus Law No. 99-З).

    Cascade-deletes from all tables where the user has data.

    BE-M3: requires an explicit ``confirmation`` JSON body that matches
    the user's Telegram first_name (case-insensitive, stripped) OR the
    string of their Telegram user id. Without a matching confirmation
    we return 400 — a stray client-side click no longer triggers an
    irreversible wipe. The check is server-side; the frontend modal is
    a UX courtesy, not the security boundary.
    """
    expected_name = (_user.first_name or "").strip().casefold()
    expected_id_str = str(_user.user_id)
    typed = (payload.confirmation or "").strip().casefold()

    name_matches = bool(expected_name) and typed == expected_name
    id_matches = typed == expected_id_str

    if not (name_matches or id_matches):
        # Log as info so ops can spot mass-typo / brute-force patterns
        # without blowing up the warn channel — a single mistype is
        # entirely normal user behaviour.
        logger.info(
            "Account deletion rejected for user %d: confirmation mismatch",
            _user.user_id,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "Confirmation does not match. Type your Telegram first "
                "name to confirm account deletion."
            ),
        )

    user_internal_id: int | None = None
    async with session_factory() as session:
        # Find the internal user id
        stmt = select(User).where(User.telegram_user_id == _user.user_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user:
            # Save id before deletion — object becomes detached after commit
            user_internal_id = user.id

        dlq_conditions = [TelegramNotificationDLQ.telegram_user_id == _user.user_id]
        if user_internal_id is not None:
            dlq_conditions.append(TelegramNotificationDLQ.user_id == user_internal_id)
        await session.execute(delete(TelegramNotificationDLQ).where(or_(*dlq_conditions)))

        if user:
            # Cascade deletes happen via ORM relationships + DB ON DELETE CASCADE
            await session.delete(user)
        await session.commit()

    # BE-C6: Redis cleanup is owned end-to-end by clear_user_ai_data
    # in ai_analysis.py — it scans every per-user namespace
    # (ai_task / ai_rate / ai_daily, plus auth:blacklist
    # and auth:initdata) and clears the in-memory shadow stores.
    # The previous direct ``cache.delete("ai_rate:{tg}")`` here was a
    # no-op (real keys are ``ai_rate:{tg}:{endpoint}``) so it stayed
    # silently broken until we audited it. Single call below = single
    # source of truth.
    try:
        from api.routers.ai_analysis import clear_user_ai_data  # deferred to avoid circular import

        # OPUS-20: reuse the app cache to avoid spinning up a brand-
        # new Redis pool for a single user-deletion call.
        await clear_user_ai_data(
            _user.user_id,
            cache=getattr(request.app.state, "cache", None),
            # AI-HIGH (issues §3.2): walk audit log and evict
            # ai_analysis:* keys for the entries this user touched.
            session_factory=request.app.state.session_factory,
        )
    except Exception:
        logger.warning(
            "Failed to clear per-user cache for user %d during account deletion",
            _user.user_id,
            exc_info=True,
        )

    logger.info("User %s (telegram_id=%d) deleted their account", user_internal_id, _user.user_id)


@router.get("/export")
@limiter.limit("10/minute")
async def export_account_data(
    request: Request,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Export all user data as JSON (right to data portability)."""
    async with session_factory() as session:
        # User profile
        stmt = select(User).where(User.telegram_user_id == _user.user_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        uid = user.id

        # BE-02: every collection in this export is bounded with an
        # explicit LIMIT so a single power user with tens of thousands
        # of leads / expenses can't OOM the worker. Each cap is large
        # enough to cover the practical 99th-percentile user but small
        # enough that the JSON payload stays well under the response
        # buffer ceiling. If we ever hit the cap, the response will
        # include the most recent rows (ORDER BY created_at DESC) and
        # the truncated flag below tells the client to use the API for
        # the full history rather than the JSON dump.
        export_row_cap = 5000

        profile = {
            "telegram_user_id": user.telegram_user_id,
            "first_name": user.first_name,
            "username": user.username,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        }

        saved_searches = (
            await session.execute(
                select(SavedSearch)
                .where(SavedSearch.user_id == uid)
                .order_by(SavedSearch.created_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        saved_searches_data = [
            {
                "id": s.id,
                "name": s.name,
                "group_name": s.group_name,
                "query": s.query,
                "strict_mode": s.strict_mode,
                "target_discount_percent": _money(s.target_discount_percent),
                "max_price_byn": _money(s.max_price_byn),
                "seller_type": s.seller_type,
                "condition": s.condition,
                "region_name": s.region_name,
                "config_keyword": s.config_keyword,
                "exclude_duplicates": s.exclude_duplicates,
                "active": s.active,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in saved_searches
        ]

        # Trackers
        trackers = (
            await session.execute(
                select(Tracker)
                .where(Tracker.user_id == uid)
                .order_by(Tracker.created_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        trackers_data = [
            {
                "id": t.id,
                "query": t.query,
                "strict_mode": t.strict_mode,
                "interval_min": t.interval_min,
                "min_discount_percent": t.min_discount_percent,
                "max_price_byn": t.max_price_byn,
                "seller_type": t.seller_type,
                "condition": t.condition,
                "region_name": t.region_name,
                "config_keyword": t.config_keyword,
                "active": t.active,
                "paused": t.paused,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in trackers
        ]

        # Tracker events (capped to prevent memory issues)
        tracker_events = (
            await session.execute(
                select(TrackerEvent)
                .where(TrackerEvent.user_id == uid)
                .order_by(TrackerEvent.created_at.desc())
                .limit(500)
            )
        ).scalars().all()
        events_data = [
            {
                "id": e.id,
                "tracker_id": e.tracker_id,
                "event_type": e.event_type,
                "title": e.title,
                "price_byn": float(e.price_byn) if e.price_byn is not None else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in tracker_events
        ]

        # Leads (excluding watching — those are in watchlist_data)
        leads = (
            await session.execute(
                select(LeadItem)
                .where(LeadItem.user_id == uid, LeadItem.status != "watching")
                .order_by(LeadItem.created_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        leads_data = [
            {
                "id": lead.id,
                "ad_id": lead.ad_id,
                "query": lead.query,
                "title": lead.title,
                "price_byn": _money(lead.price_byn),
                "buy_price_byn": _money(lead.buy_price_byn),
                "sold_price_byn": _money(lead.sold_price_byn),
                "target_resale_byn": _money(lead.target_resale_byn),
                "status": lead.status,
                "source": lead.source,
                "notes": lead.notes,
                "market_status": lead.market_status,
                "missing_since_at": (
                    lead.missing_since_at.isoformat() if lead.missing_since_at else None
                ),
                "sold_at": lead.sold_at.isoformat() if lead.sold_at else None,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
                "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
            }
            for lead in leads
        ]

        price_snapshots = (
            await session.execute(
                select(LeadItemPriceSnapshot)
                .join(LeadItem, LeadItemPriceSnapshot.lead_item_id == LeadItem.id)
                .where(LeadItem.user_id == uid)
                .order_by(LeadItemPriceSnapshot.snapped_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        price_snapshots_data = [
            {
                "id": p.id,
                "lead_item_id": p.lead_item_id,
                "price_byn": _money(p.price_byn),
                "snapped_at": p.snapped_at.isoformat() if p.snapped_at else None,
            }
            for p in price_snapshots
        ]

        reminders = (
            await session.execute(
                select(LeadReminder)
                .where(LeadReminder.user_id == uid)
                .order_by(LeadReminder.remind_at.desc(), LeadReminder.id.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        reminders_data = [
            {
                "id": r.id,
                "lead_id": r.lead_id,
                "remind_at": r.remind_at.isoformat() if r.remind_at else None,
                "message": r.message,
                "sent": r.sent,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reminders
        ]

        # Expenses
        expenses = (
            await session.execute(
                select(DealExpense)
                .where(DealExpense.user_id == uid)
                .order_by(DealExpense.created_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        expenses_data = [
            {
                "id": e.id,
                "lead_id": e.lead_id,
                "expense_type": e.expense_type,
                "amount_byn": _money(e.amount_byn),
                "notes": e.notes,
                "expense_date": e.expense_date.isoformat() if e.expense_date else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in expenses
        ]

        # Consents
        consents = (
            await session.execute(
                select(UserConsent)
                .where(UserConsent.user_id == uid)
                .order_by(UserConsent.granted_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        consents_data = [
            {
                "id": c.id,
                "consent_type": c.consent_type,
                "version": c.version,
                "ip_address": c.ip_address,
                "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            }
            for c in consents
        ]

        ai_audit_logs = (
            await session.execute(
                select(AIAuditLog)
                .where(AIAuditLog.user_id == uid)
                .order_by(AIAuditLog.created_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        ai_audit_logs_data = [
            {
                "id": a.id,
                "endpoint": a.endpoint,
                "ad_id": a.ad_id,
                "query": sanitize_audit_text(a.query, max_length=256, context="export.query"),
                "query_hash": a.query_hash or audit_text_sha256(a.query),
                "result_summary": sanitize_audit_text(
                    a.result_summary,
                    max_length=512,
                    context="export.result_summary",
                ),
                "result_summary_hash": (
                    a.result_summary_hash or audit_text_sha256(a.result_summary)
                ),
                "model": a.model,
                "latency_ms": a.latency_ms,
                # OPUS-17: include the IP captured at AI call time so
                # the export's audit slice matches the consent slice.
                "ip_address": a.ip_address,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in ai_audit_logs
        ]

        # Watchlist items (LeadItem with status='watching')
        watchlist = (
            await session.execute(
                select(LeadItem)
                .where(LeadItem.user_id == uid, LeadItem.status == "watching")
                .order_by(LeadItem.created_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        watchlist_data = [
            {
                "id": w.id,
                "ad_id": w.ad_id,
                "query": w.query,
                "title": w.title,
                "price_byn": _money(w.price_byn),
                "initial_price_byn": _money(w.initial_price_byn),
                "market_median_byn": _money(w.market_median_byn),
                "target_resale_byn": _money(w.target_resale_byn),
                "notes": w.notes,
                "market_status": w.market_status,
                "missing_since_at": (
                    w.missing_since_at.isoformat() if w.missing_since_at else None
                ),
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "updated_at": w.updated_at.isoformat() if w.updated_at else None,
            }
            for w in watchlist
        ]

        contacts = (
            await session.execute(
                select(Contact)
                .where(Contact.user_id == uid)
                .order_by(Contact.saved_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        contacts_data = [
            {
                "id": c.id,
                "phone": c.phone,
                "seller_name": c.seller_name,
                "kufar_profile": c.kufar_profile,
                "saved_at": c.saved_at.isoformat() if c.saved_at else None,
            }
            for c in contacts
        ]

        notification_dlq = (
            await session.execute(
                select(TelegramNotificationDLQ)
                .where(
                    or_(
                        TelegramNotificationDLQ.user_id == uid,
                        TelegramNotificationDLQ.telegram_user_id == _user.user_id,
                    )
                )
                .order_by(TelegramNotificationDLQ.created_at.desc())
                .limit(export_row_cap)
            )
        ).scalars().all()
        notification_dlq_data = [
            {
                "id": d.id,
                "telegram_user_id": d.telegram_user_id,
                "source": d.source,
                "message": d.message,
                "error_kind": d.error_kind,
                "error_message": d.error_message,
                "retry_after_seconds": d.retry_after_seconds,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in notification_dlq
        ]

    # BE-02: surface per-collection truncation so a power user knows
    # their export is partial. Each collection caps at export_row_cap
    # (most-recent first); the API provides full history via the
    # individual endpoints if the user actually needs it.
    truncated = {
        "saved_searches": len(saved_searches_data) >= export_row_cap,
        "trackers": len(trackers_data) >= export_row_cap,
        "tracker_events": len(events_data) >= 500,
        "leads": len(leads_data) >= export_row_cap,
        "price_snapshots": len(price_snapshots_data) >= export_row_cap,
        "reminders": len(reminders_data) >= export_row_cap,
        "expenses": len(expenses_data) >= export_row_cap,
        "consents": len(consents_data) >= export_row_cap,
        "ai_audit_logs": len(ai_audit_logs_data) >= export_row_cap,
        "watchlist": len(watchlist_data) >= export_row_cap,
        "contacts": len(contacts_data) >= export_row_cap,
        "notification_dlq": len(notification_dlq_data) >= export_row_cap,
    }

    payload = {
        "profile": profile,
        "saved_searches": saved_searches_data,
        "trackers": trackers_data,
        "tracker_events": events_data,
        "leads": leads_data,
        "price_snapshots": price_snapshots_data,
        "reminders": reminders_data,
        "expenses": expenses_data,
        "watchlist": watchlist_data,
        "contacts": contacts_data,
        "notification_dlq": notification_dlq_data,
        "consents": consents_data,
        "ai_audit_logs": ai_audit_logs_data,
        "exported_at": datetime.now(UTC).isoformat(),
        "truncated": truncated,
        "export_row_cap": export_row_cap,
    }

    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="rafuks_data_export.json"',
        },
    )
