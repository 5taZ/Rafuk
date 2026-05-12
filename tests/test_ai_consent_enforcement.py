"""Tests for AI consent gate enforcement (Wave 27 — TEST-03).

Existing AI tests run with DEBUG=true (see conftest.configure_env),
which makes ``_check_ai_consent`` a no-op — and the audit flagged
that nothing exercises the actual production path where the consent
gate enforces a 403 for users who haven't granted consent.

This file runs the consent check with DEBUG=false to confirm:
* users without consent get a 403 with consent_type="ai_analysis";
* cross_border is the second gate — granting only ai_analysis still
  gets you blocked with consent_type="cross_border";
* granting BOTH passes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import config
from api.database import get_engine, get_session_factory
from api.models import Base, User, UserConsent
from api.services import ai_guards


@pytest.fixture
async def fresh_session_factory():
    engine = get_engine("sqlite+aiosqlite:///:memory:")
    factory = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return factory


def _make_request(session_factory):
    """Build the minimum request-shaped object _check_ai_consent
    needs — just app.state.session_factory."""
    request = SimpleNamespace()
    request.app = SimpleNamespace()
    request.app.state = SimpleNamespace()
    request.app.state.session_factory = session_factory
    return request


@pytest.fixture
def debug_off(monkeypatch):
    """Flip DEBUG=false for the duration of the test and rebuild the
    settings cache so the new value takes effect."""
    monkeypatch.setenv("DEBUG", "false")
    config.get_settings.cache_clear()
    yield
    # Restore for the rest of the test session.
    monkeypatch.setenv("DEBUG", "true")
    config.get_settings.cache_clear()


# ──────────────────────────────────────────────────────────────────────
# No consent at all → 403 with ai_analysis consent_type.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_consent_blocks_unknown_user_in_production(
    debug_off, fresh_session_factory,
) -> None:
    request = _make_request(fresh_session_factory)
    with pytest.raises(HTTPException) as excinfo:
        await ai_guards._check_ai_consent(request, user_id=999_999_999)
    assert excinfo.value.status_code == 403
    detail = excinfo.value.detail
    assert detail["error"] == "consent_required"
    assert detail["consent_type"] == "ai_analysis"


@pytest.mark.asyncio
async def test_ai_consent_blocks_user_without_ai_grant(
    debug_off, fresh_session_factory,
) -> None:
    # Seed the user, no consent rows.
    async with fresh_session_factory() as session:
        session.add(User(telegram_user_id=111, first_name="NoConsent"))
        await session.commit()

    request = _make_request(fresh_session_factory)
    with pytest.raises(HTTPException) as excinfo:
        await ai_guards._check_ai_consent(request, user_id=111)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["consent_type"] == "ai_analysis"


# ──────────────────────────────────────────────────────────────────────
# Partial consent (ai_analysis only) → 403 with cross_border.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_consent_demands_cross_border_after_ai_grant(
    debug_off, fresh_session_factory,
) -> None:
    async with fresh_session_factory() as session:
        user = User(telegram_user_id=222, first_name="Partial")
        session.add(user)
        await session.flush()
        session.add(UserConsent(
            user_id=user.id,
            consent_type="ai_analysis",
            version="2026.2",
            granted_at=datetime.now(UTC),
        ))
        await session.commit()

    request = _make_request(fresh_session_factory)
    with pytest.raises(HTTPException) as excinfo:
        await ai_guards._check_ai_consent(request, user_id=222)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["consent_type"] == "cross_border"


# ──────────────────────────────────────────────────────────────────────
# Full consent → passes.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_consent_passes_with_both_consents_granted(
    debug_off, fresh_session_factory,
) -> None:
    async with fresh_session_factory() as session:
        user = User(telegram_user_id=333, first_name="FullConsent")
        session.add(user)
        await session.flush()
        session.add_all([
            UserConsent(
                user_id=user.id,
                consent_type="ai_analysis",
                version="2026.2",
                granted_at=datetime.now(UTC),
            ),
            UserConsent(
                user_id=user.id,
                consent_type="cross_border",
                version="2026.2",
                granted_at=datetime.now(UTC),
            ),
        ])
        await session.commit()

    request = _make_request(fresh_session_factory)
    # Should not raise.
    await ai_guards._check_ai_consent(request, user_id=333)


# ──────────────────────────────────────────────────────────────────────
# Revoked consent should NOT pass even though the row exists.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_consent_rejects_revoked_grants(
    debug_off, fresh_session_factory,
) -> None:
    async with fresh_session_factory() as session:
        user = User(telegram_user_id=444, first_name="Revoked")
        session.add(user)
        await session.flush()
        now = datetime.now(UTC)
        session.add_all([
            UserConsent(
                user_id=user.id,
                consent_type="ai_analysis",
                version="2026.2",
                granted_at=now,
                revoked_at=now,  # revoked
            ),
            UserConsent(
                user_id=user.id,
                consent_type="cross_border",
                version="2026.2",
                granted_at=now,
            ),
        ])
        await session.commit()

    request = _make_request(fresh_session_factory)
    with pytest.raises(HTTPException) as excinfo:
        await ai_guards._check_ai_consent(request, user_id=444)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["consent_type"] == "ai_analysis"


# ──────────────────────────────────────────────────────────────────────
# DEBUG=true (the normal test env) short-circuits the whole check.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_consent_is_skipped_in_debug_mode(fresh_session_factory) -> None:
    # DEBUG=true from conftest.configure_env — no consent rows needed.
    request = _make_request(fresh_session_factory)
    await ai_guards._check_ai_consent(request, user_id=999)  # no raise
