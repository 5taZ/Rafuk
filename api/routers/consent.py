"""Consent and account management router — PD processing, AI consent, data export/deletion."""

from __future__ import annotations

import ipaddress
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.models import UserConsent
from api.schemas import ConsentGrantRequest, ConsentStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


def _is_valid_ip(value: str) -> bool:
    """Return True if *value* is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False

VALID_CONSENT_TYPES = {"ai_analysis", "pd_processing", "cross_border"}
CURRENT_POLICY_VERSION = "2026.1"

_CLOUDFLARE_IP_RANGES = [
    ipaddress.ip_network("173.245.48.0/20"),
    ipaddress.ip_network("103.21.244.0/22"),
    ipaddress.ip_network("103.22.200.0/22"),
    ipaddress.ip_network("103.31.4.0/22"),
    ipaddress.ip_network("141.101.64.0/18"),
    ipaddress.ip_network("108.162.192.0/18"),
    ipaddress.ip_network("190.93.240.0/20"),
    ipaddress.ip_network("188.114.96.0/20"),
    ipaddress.ip_network("197.234.240.0/22"),
    ipaddress.ip_network("198.41.128.0/17"),
    ipaddress.ip_network("162.158.0.0/15"),
    ipaddress.ip_network("104.16.0.0/13"),
    ipaddress.ip_network("104.24.0.0/14"),
    ipaddress.ip_network("172.64.0.0/13"),
    ipaddress.ip_network("131.0.72.0/22"),
]


def _is_cloudflare_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _CLOUDFLARE_IP_RANGES)
    except ValueError:
        return False


def _get_client_ip(request: Request) -> str | None:
    """Extract client IP respecting Cloudflare headers.

    Priority:
    1. CF-Connecting-IP (set by Cloudflare edge).
    2. X-Forwarded-For last entry — ONLY when the direct peer is a
       Cloudflare IP.  Earlier entries may be client-spoofed.
    3. request.client.host as fallback.
    """
    client_ip = request.client.host if request.client else None

    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip and _is_valid_ip(cf_ip):
        return cf_ip

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and client_ip and _is_cloudflare_ip(client_ip):
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            candidate = parts[-1]
            if _is_valid_ip(candidate):
                return candidate

    return client_ip


@router.get("/consent/{consent_type}", response_model=ConsentStatusResponse)
async def get_consent_status(
    consent_type: str,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Check whether the current user has granted a specific consent."""
    if consent_type not in VALID_CONSENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Неизвестный тип согласия: {consent_type}"
        )

    async with session_factory() as session:
        from api.services.workflow_store import resolve_user_id

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
async def grant_consent(
    payload: ConsentGrantRequest,
    request: Request,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Grant a consent (e.g. AI analysis, PD processing, cross-border transfer)."""
    if payload.consent_type not in VALID_CONSENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Неизвестный тип согласия: {payload.consent_type}"
        )

    # Reject stale consent versions — forces re-consent when policy changes
    if payload.version != CURRENT_POLICY_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Устаревшая версия политики. Текущая: {CURRENT_POLICY_VERSION}",
        )

    async with session_factory() as session:
        from api.services.workflow_store import ensure_user

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

        # Capture client IP for audit trail (Belarus Law No. 91-Z)
        client_ip = _get_client_ip(request)

        consent = UserConsent(
            user_id=uid,
            consent_type=payload.consent_type,
            version=payload.version,
            ip_address=client_ip,
        )
        session.add(consent)
        await session.commit()
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
async def revoke_consent(
    consent_type: str,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Revoke a previously granted consent."""
    if consent_type not in VALID_CONSENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Неизвестный тип согласия: {consent_type}"
        )

    revoked_count = 0
    async with session_factory() as session:
        from api.services.workflow_store import resolve_user_id

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
        await session.commit()

    if revoked_count:
        logger.info("User %d revoked consent %s", _user.user_id, consent_type)


@router.delete("", status_code=204)
async def delete_account(
    request: Request,
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Delete all user data (right to erasure under Belarus Law No. 91-Z).

    Cascade-deletes from all tables where the user has data.
    """
    from api.models import User

    user_internal_id: int | None = None
    async with session_factory() as session:
        # Find the internal user id
        stmt = select(User).where(User.telegram_user_id == _user.user_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if not user:
            return  # Already gone

        # Save id before deletion — object becomes detached after commit
        user_internal_id = user.id

        # Cascade deletes happen via ORM relationships + DB ON DELETE CASCADE
        await session.delete(user)
        await session.commit()

    # Clear Redis/AI task caches for this user
    try:
        from api.dependencies import get_cache

        cache = get_cache(request)
        # Best-effort: clear known cache prefixes
        import contextlib

        for prefix in (
            f"ai_rate:{_user.user_id}",
            f"ai_daily:{_user.user_id}",
        ):
            with contextlib.suppress(Exception):
                await cache.delete(prefix)
    except Exception:
        pass

    # Clear in-memory AI task/export shadow stores
    try:
        from api.routers.ai_analysis import clear_user_ai_data

        clear_user_ai_data(_user.user_id)
    except Exception:
        pass

    logger.info("User %d (telegram_id=%d) deleted their account", user_internal_id, _user.user_id)


@router.get("/export")
async def export_account_data(
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency
    ),
):
    """Export all user data as JSON (right to data portability)."""
    from api.models import (
        DealExpense,
        LeadItem,
        Tracker,
        TrackerEvent,
        User,
        UserConsent,
    )

    async with session_factory() as session:
        # User profile
        stmt = select(User).where(User.telegram_user_id == _user.user_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        uid = user.id

        profile = {
            "telegram_user_id": user.telegram_user_id,
            "first_name": user.first_name,
            "username": user.username,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        }

        # Trackers
        trackers = (
            await session.execute(select(Tracker).where(Tracker.user_id == uid))
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
                select(LeadItem).where(LeadItem.user_id == uid, LeadItem.status != "watching")
            )
        ).scalars().all()
        leads_data = [
            {
                "id": lead.id,
                "ad_id": lead.ad_id,
                "query": lead.query,
                "title": lead.title,
                "price_byn": (
                    float(lead.price_byn) if lead.price_byn is not None else None
                ),
                "buy_price_byn": (
                    float(lead.buy_price_byn) if lead.buy_price_byn is not None else None
                ),
                "sold_price_byn": (
                    float(lead.sold_price_byn) if lead.sold_price_byn is not None else None
                ),
                "status": lead.status,
                "source": lead.source,
                "notes": lead.notes,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
            }
            for lead in leads
        ]

        # Expenses
        expenses = (
            await session.execute(select(DealExpense).where(DealExpense.user_id == uid))
        ).scalars().all()
        expenses_data = [
            {
                "id": e.id,
                "lead_id": e.lead_id,
                "expense_type": e.expense_type,
                "amount_byn": e.amount_byn,
                "notes": e.notes,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in expenses
        ]

        # Consents
        consents = (
            await session.execute(select(UserConsent).where(UserConsent.user_id == uid))
        ).scalars().all()
        consents_data = [
            {
                "id": c.id,
                "consent_type": c.consent_type,
                "version": c.version,
                "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            }
            for c in consents
        ]

        # Watchlist items (LeadItem with status='watching')
        watchlist = (
            await session.execute(
                select(LeadItem).where(LeadItem.user_id == uid, LeadItem.status == "watching")
            )
        ).scalars().all()
        watchlist_data = [
            {
                "id": w.id,
                "ad_id": w.ad_id,
                "query": w.query,
                "title": w.title,
                "price_byn": (
                    float(w.price_byn) if w.price_byn is not None else None
                ),
                "initial_price_byn": (
                    float(w.initial_price_byn) if w.initial_price_byn is not None else None
                ),
                "market_median_byn": (
                    float(w.market_median_byn) if w.market_median_byn is not None else None
                ),
                "notes": w.notes,
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
            for w in watchlist
        ]

    payload = {
        "profile": profile,
        "trackers": trackers_data,
        "tracker_events": events_data,
        "leads": leads_data,
        "expenses": expenses_data,
        "watchlist": watchlist_data,
        "consents": consents_data,
        "exported_at": datetime.now(UTC).isoformat(),
    }

    from fastapi.responses import Response

    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="rafuks_data_export.json"',
        },
    )
