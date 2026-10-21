"""Tests for consent and account management router."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from fastapi import Depends, Request
from httpx import ASGITransport, AsyncClient

from api.main import app
from api.middleware.telegram_auth import TelegramInitData


def _fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=999888, first_name="ConsentTest", raw={})


@pytest.fixture
def _override_auth():
    from api.dependencies import get_telegram_user

    app.dependency_overrides[get_telegram_user] = _fake_telegram_user
    yield
    app.dependency_overrides.pop(get_telegram_user, None)


@pytest.fixture
async def client(_override_auth):
    from api.database import get_engine, get_session_factory

    engine = get_engine()
    app.state.session_factory = get_session_factory(engine)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            # FE-H7: CSRF middleware now requires Origin AND the browser-
            # only X-Requested-With header on state-changing methods.
            # AsyncClient doesn't go through our TestClient shim, so we
            # set both explicitly here.
            headers={
                "origin": "http://localhost:8081",
                "x-requested-with": "XMLHttpRequest",
            },
        ) as c:
            yield c
    finally:
        delattr(app.state, "session_factory")
        await engine.dispose()


# ── Consent status ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_status_defaults_to_not_granted(client):
    resp = await client.get("/api/v1/account/consent/ai_analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is False
    assert data["consent_type"] == "ai_analysis"


@pytest.mark.asyncio
async def test_ai_consent_info_matches_default_provider(client):
    resp = await client.get("/api/v1/account/ai-consent-info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_name"] == "Google Gemini API"
    assert data["provider_region"] == "США"
    assert data["provider_host"] == "generativelanguage.googleapis.com"
    assert data["model"] == "gemini-2.5-flash"
    assert data["policy_version"] == "2026.2"
    assert data["display_label"] == "Google Gemini API, модель gemini-2.5-flash (США)"


@pytest.mark.asyncio
async def test_ai_consent_info_uses_provider_overrides(client, monkeypatch):
    from api.config import get_settings
    from api.routers import consent as consent_router

    monkeypatch.setenv("AI_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("AI_MODEL", "custom-model")
    monkeypatch.setenv("AI_PROVIDER_NAME", "Custom EU LLM")
    monkeypatch.setenv("AI_PROVIDER_REGION", "ЕС")
    get_settings.cache_clear()
    consent_router.get_settings.cache_clear()
    try:
        resp = await client.get("/api/v1/account/ai-consent-info")
    finally:
        get_settings.cache_clear()
        consent_router.get_settings.cache_clear()
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_name"] == "Custom EU LLM"
    assert data["provider_region"] == "ЕС"
    assert data["provider_host"] == "llm.example.com"
    assert data["model"] == "custom-model"
    assert data["display_label"] == "Custom EU LLM, модель custom-model (ЕС)"


@pytest.mark.asyncio
async def test_consent_status_rejects_invalid_type(client):
    resp = await client.get("/api/v1/account/consent/invalid_type")
    assert resp.status_code == 400


# ── Grant consent ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_consent(client):
    resp = await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "ai_analysis", "version": "2026.2"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["granted"] is True
    assert data["consent_type"] == "ai_analysis"
    assert data["version"] == "2026.2"


@pytest.mark.asyncio
async def test_grant_consent_idempotent(client):
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )
    # Second grant with same version should return 201 (not error)
    resp = await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )
    assert resp.status_code == 201
    assert resp.json()["granted"] is True


@pytest.mark.asyncio
async def test_grant_consent_rejects_invalid_type(client):
    resp = await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "bogus", "version": "2026.2"},
    )
    # A-6: Pydantic Literal now catches invalid types at schema level (422)
    assert resp.status_code == 422


# ── Consent status after grant ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_status_shows_granted_after_grant(client):
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "cross_border", "version": "2026.2"},
    )
    resp = await client.get("/api/v1/account/consent/cross_border")
    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is True
    assert data["version"] == "2026.2"
    assert data["granted_at"] is not None


# ── Revoke consent ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_consent(client):
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "ai_analysis", "version": "2026.2"},
    )
    resp = await client.delete("/api/v1/account/consent/ai_analysis")
    assert resp.status_code == 204

    # Should now show not granted
    status = await client.get("/api/v1/account/consent/ai_analysis")
    assert status.json()["granted"] is False


@pytest.mark.asyncio
async def test_revoke_consent_clears_per_user_cache(client, monkeypatch):
    """OPUS-1: revoke consent must wipe AI/initdata caches for the
    user, otherwise data processed under the now-revoked consent
    keeps living in Redis until TTL — which contradicts the
    "right to revoke" promise.

    RV-SEC-02: usage counters and auth state must survive the revoke —
    wiping them let a revoke → grant cycle reset the daily AI quota.
    """
    import fnmatch

    from api.services.cache import MemoryCache, RedisCache

    fake_cache = MemoryCache()
    target_uid = 999888  # _fake_telegram_user user_id

    artefacts = {
        f"ai_task:u{target_uid}:abc": {"x": 1},
        f"ai_listing:u{target_uid}:abc": {"title_suggestion": "private"},
    }
    account_state = {
        f"ai_rate:{target_uid}:default": {"count": 5},
        f"ai_daily:{target_uid}": {"count": 2},
        f"quota:ai:{target_uid}:2026-05-18": 2,
        f"quota:assistant:{target_uid}:2026-05-18": 1,
        f"auth:blacklist:{target_uid}": {"reason": "test"},
        f"auth:replay_warn:{target_uid}": 3,
    }
    for k, v in {**artefacts, **account_state}.items():
        await fake_cache.set_json(k, v)

    class _FakeRedis:
        def __init__(self, storage):
            self._storage = storage

        async def scan(self, cursor, match="*", count=100):
            return 0, [k for k in self._storage if fnmatch.fnmatch(k, match)]

        async def delete(self, *keys):
            for k in keys:
                self._storage.pop(k, None)
            return len(keys)

    fake_cache._client = _FakeRedis(fake_cache._storage)
    monkeypatch.setattr(RedisCache, "from_url", staticmethod(lambda *a, **kw: fake_cache))

    async def _ping_true(self_):
        return True

    monkeypatch.setattr(RedisCache, "ping", _ping_true)

    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "ai_analysis", "version": "2026.2"},
    )
    resp = await client.delete("/api/v1/account/consent/ai_analysis")
    assert resp.status_code == 204

    for key in artefacts:
        assert await fake_cache.get_json(key) is None, f"{key} should be cleared on revoke"
    for key, value in account_state.items():
        assert await fake_cache.get_json(key) == value, f"{key} must survive a revoke"


# ── Account export ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_account_data_with_tracker_numeric_fields(client):
    """RV-BE-01: Tracker.min_discount_percent / max_price_byn are Numeric
    columns, so they load as Decimal. They went into the payload raw and
    json.dumps raised — every user with a tracker got a 500 instead of
    their data export (the Mini App always sends min_discount_percent)."""
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "ai_analysis", "version": "2026.2"},
    )
    from sqlalchemy import select

    from api.main import app
    from api.models import Tracker, User

    async with app.state.session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_user_id == _fake_telegram_user().user_id)
            )
        ).scalar_one()
        session.add(Tracker(
            user_id=user.id,
            query="iphone 13",
            min_discount_percent=10,
            max_price_byn=1500.5,
        ))
        await session.commit()

    resp = await client.get("/api/v1/account/export")
    assert resp.status_code == 200
    tracker = resp.json()["trackers"][0]
    assert tracker["min_discount_percent"] == 10.0
    assert tracker["max_price_byn"] == 1500.5


@pytest.mark.asyncio
async def test_export_account_data(client):
    # Grant consent — this also creates the user via ensure_user
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "ai_analysis", "version": "2026.2"},
    )
    from sqlalchemy import select

    from api.main import app
    from api.models import (
        AIAuditLog,
        Contact,
        LeadItem,
        LeadItemPriceSnapshot,
        LeadReminder,
        SavedSearch,
        TelegramNotificationDLQ,
        User,
    )

    async with app.state.session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_user_id == _fake_telegram_user().user_id)
            )
        ).scalar_one()
        saved_search = SavedSearch(
            user_id=user.id,
            name="Phones",
            query="iphone",
            target_discount_percent=12.5,
        )
        lead = LeadItem(
            user_id=user.id,
            ad_id=12345,
            query="iphone",
            title="iPhone 13",
            link="https://www.kufar.by/item/12345",
            price_byn=900,
            status="new",
            source="manual",
        )
        session.add_all([saved_search, lead])
        await session.flush()
        session.add_all([
            LeadItemPriceSnapshot(
                lead_item_id=lead.id,
                price_byn=900,
                snapped_at=datetime.now(UTC),
            ),
            LeadReminder(
                user_id=user.id,
                lead_id=lead.id,
                remind_at=datetime.now(UTC),
                message="check seller",
            ),
            AIAuditLog(
                user_id=user.id,
                endpoint="analyze",
                ad_id="12345",
                query="iphone +375291234567 user@example.com",
                result_summary="ok @seller",
                model="test-model",
            ),
            TelegramNotificationDLQ(
                user_id=user.id,
                telegram_user_id=user.telegram_user_id,
                source="test",
                message="failed notification",
                error_kind="retryable",
            ),
            Contact(
                user_id=user.id,
                phone="+375291234567",
                seller_name="Seller",
                kufar_profile="https://www.kufar.by/user/test",
            ),
        ])
        await session.commit()

    resp = await client.get("/api/v1/account/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "profile" in data
    assert "consents" in data
    assert "exported_at" in data
    for key in (
        "saved_searches",
        "price_snapshots",
        "reminders",
        "ai_audit_logs",
        "notification_dlq",
        "contacts",
    ):
        assert key in data
        assert data[key], f"{key} should be exported"
    # Should have at least one consent
    assert len(data["consents"]) >= 1
    assert data["saved_searches"][0]["query"] == "iphone"
    assert data["price_snapshots"][0]["price_byn"] == 900.0
    assert data["reminders"][0]["message"] == "check seller"
    assert data["ai_audit_logs"][0]["query"] == "iphone [phone] [email]"
    assert data["ai_audit_logs"][0]["query_hash"] == hashlib.sha256(
        b"iphone +375291234567 user@example.com"
    ).hexdigest()
    assert data["ai_audit_logs"][0]["result_summary"] == "ok [handle]"
    assert data["ai_audit_logs"][0]["result_summary_hash"] == hashlib.sha256(
        b"ok @seller"
    ).hexdigest()
    assert "+375291234567" not in data["ai_audit_logs"][0]["query"]
    assert "user@example.com" not in data["ai_audit_logs"][0]["query"]
    assert "@seller" not in data["ai_audit_logs"][0]["result_summary"]
    assert data["ai_audit_logs"][0]["model"] == "test-model"
    assert data["notification_dlq"][0]["message"] == "failed notification"
    assert data["contacts"][0]["phone"] == "+375291234567"


# ── Account deletion ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_account(client):
    # Create consent first
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )
    # BE-M3: confirmation must match the user's Telegram first_name
    # (case-insensitive). _fake_telegram_user returns "ConsentTest".
    resp = await client.request(
        "DELETE",
        "/api/v1/account",
        json={"confirmation": "consenttest"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_account_removes_orphan_notification_dlq_rows(client):
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )
    from sqlalchemy import select

    from api.main import app
    from api.models import TelegramNotificationDLQ, User

    other_telegram_id = 555666
    async with app.state.session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_user_id == _fake_telegram_user().user_id)
            )
        ).scalar_one()
        session.add_all([
            TelegramNotificationDLQ(
                user_id=None,
                telegram_user_id=user.telegram_user_id,
                source="orphan",
                message="orphaned failed notification",
                error_kind="retryable",
            ),
            TelegramNotificationDLQ(
                user_id=user.id,
                telegram_user_id=user.telegram_user_id,
                source="linked",
                message="linked failed notification",
                error_kind="retryable",
            ),
            TelegramNotificationDLQ(
                user_id=None,
                telegram_user_id=other_telegram_id,
                source="other",
                message="other user's failed notification",
                error_kind="retryable",
            ),
        ])
        await session.commit()

    resp = await client.request(
        "DELETE",
        "/api/v1/account",
        json={"confirmation": "consenttest"},
    )
    assert resp.status_code == 204

    async with app.state.session_factory() as session:
        rows = (await session.execute(select(TelegramNotificationDLQ))).scalars().all()
    assert [row.telegram_user_id for row in rows] == [other_telegram_id]


@pytest.mark.asyncio
async def test_delete_account_requires_confirmation(client):
    """BE-M3: DELETE /account without a confirmation body returns 422
    (Pydantic missing-field) — a stray click on the confirm button or
    a CSRF-replay attempt against an old endpoint shape is rejected
    before any data is touched."""
    resp = await client.request("DELETE", "/api/v1/account")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_account_rejects_mismatched_confirmation(client):
    """BE-M3: server-side validation, not just frontend modal — typing
    the wrong name returns 400 even if the request reaches us."""
    resp = await client.request(
        "DELETE",
        "/api/v1/account",
        json={"confirmation": "Eve"},
    )
    assert resp.status_code == 400
    assert "Confirmation does not match" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_account_accepts_telegram_id_as_confirmation(client):
    """BE-M3: users without a usable first_name (e.g. emoji-only or
    blank) can still confirm by typing their numeric Telegram id."""
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )
    resp = await client.request(
        "DELETE",
        "/api/v1/account",
        json={"confirmation": "999888"},  # _fake_telegram_user user_id
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_clear_user_ai_data_covers_all_namespaces(monkeypatch):
    """BE-C6: clear_user_ai_data used to leave per-user Redis
    namespaces behind because the matching call from delete_account
    issued ``cache.delete("ai_rate:{tg}")`` (no trailing ``:*``) on
    keys actually shaped ``ai_rate:{tg}:{endpoint}``. After the
    fix, every documented per-user namespace must be empty for the
    deleted user while a sibling user's data stays intact.

    We swap RedisCache.from_url for a MemoryCache wrapped with a
    tiny scan/delete adapter so the test never needs a real Redis.
    """
    import fnmatch

    from api.routers.ai_analysis import clear_user_ai_data
    from api.services.cache import MemoryCache, RedisCache

    cache = MemoryCache()
    target_uid = 4242
    other_uid = 9999

    # Seed entries for both users across every namespace
    # clear_user_ai_data promises to wipe.
    seeds = {
        f"ai_task:u{target_uid}:abc": {"x": 1},
        f"ai_listing:u{target_uid}:abc": {"title_suggestion": "private"},
        f"ai_rate:{target_uid}:default": {"count": 5},
        f"ai_daily:{target_uid}": {"count": 2},
        f"quota:ai:{target_uid}:2026-05-18": 2,
        f"quota:assistant:{target_uid}:2026-05-18": 1,
        f"auth:blacklist:{target_uid}": {"reason": "test"},
        "auth:initdata:targetdigest": {
            "user_id": target_uid,
            "ip": "10.0.0.1",
            "first_seen_ts": 0,
        },
        # Sibling user — these MUST survive.
        f"ai_task:u{other_uid}:keep": {"x": 1},
        f"ai_listing:u{other_uid}:keep": {"title_suggestion": "keep"},
        f"ai_rate:{other_uid}:default": {"count": 5},
        f"quota:ai:{other_uid}:2026-05-18": 2,
        "auth:initdata:otherdigest": {
            "user_id": other_uid,
            "ip": "10.0.0.2",
            "first_seen_ts": 0,
        },
    }
    for k, v in seeds.items():
        await cache.set_json(k, v)

    # Tiny adapter that exposes the redis-py methods clear_user_ai_data
    # actually calls (scan + delete), backed by the MemoryCache's
    # internal storage so set/get_json continue to work.
    class _FakeRedis:
        def __init__(self, storage):
            self._storage = storage

        async def scan(self, cursor, match="*", count=100):
            keys = [k for k in self._storage if fnmatch.fnmatch(k, match)]
            return 0, keys  # one-shot scan

        async def delete(self, *keys):
            for k in keys:
                self._storage.pop(k, None)
            return len(keys)

    cache._client = _FakeRedis(cache._storage)

    # Patch RedisCache.from_url to hand back our pre-seeded cache and
    # short-circuit ping() so clear_user_ai_data takes the Redis branch.
    monkeypatch.setattr(RedisCache, "from_url", staticmethod(lambda *a, **kw: cache))

    async def _ping_true(self_):
        return True

    monkeypatch.setattr(RedisCache, "ping", _ping_true)

    await clear_user_ai_data(target_uid)

    # Target user's entries should all be gone.
    for key in (
        f"ai_task:u{target_uid}:abc",
        f"ai_listing:u{target_uid}:abc",
        f"ai_rate:{target_uid}:default",
        f"ai_daily:{target_uid}",
        f"quota:ai:{target_uid}:2026-05-18",
        f"quota:assistant:{target_uid}:2026-05-18",
        f"auth:blacklist:{target_uid}",
        "auth:initdata:targetdigest",
    ):
        assert await cache.get_json(key) is None, f"{key} should be cleared"

    # Sibling user's entries must still be there.
    for key in (
        f"ai_task:u{other_uid}:keep",
        f"ai_listing:u{other_uid}:keep",
        f"ai_rate:{other_uid}:default",
        f"quota:ai:{other_uid}:2026-05-18",
        "auth:initdata:otherdigest",
    ):
        assert await cache.get_json(key) is not None, f"{key} should survive"


@pytest.mark.asyncio
async def test_clear_user_ai_data_covers_memory_cache_fallback():
    from api.routers.ai_analysis import clear_user_ai_data
    from api.services.cache import MemoryCache

    cache = MemoryCache()
    target_uid = 5252
    other_uid = 6262
    seeds = {
        f"ai_task:u{target_uid}:abc": {"x": 1},
        f"ai_listing:u{target_uid}:abc": {"title_suggestion": "private"},
        f"ai_rate:{target_uid}:default": {"count": 5},
        f"ai_daily:{target_uid}": {"count": 2},
        f"quota:ai:{target_uid}:2026-05-18": 2,
        f"quota:assistant:{target_uid}:2026-05-18": 1,
        f"auth:blacklist:{target_uid}": {"reason": "test"},
        f"auth:replay_warn:{target_uid}": {"count": 1},
        "auth:initdata:memory-target": {"user_id": target_uid, "ip": "10.0.0.1"},
        f"ai_task:u{other_uid}:keep": {"x": 1},
        f"quota:ai:{other_uid}:2026-05-18": 2,
        "auth:initdata:memory-other": {"user_id": other_uid, "ip": "10.0.0.2"},
    }
    for key, value in seeds.items():
        await cache.set_json(key, value)

    await clear_user_ai_data(target_uid, cache=cache)

    for key in (
        f"ai_task:u{target_uid}:abc",
        f"ai_listing:u{target_uid}:abc",
        f"ai_rate:{target_uid}:default",
        f"ai_daily:{target_uid}",
        f"quota:ai:{target_uid}:2026-05-18",
        f"quota:assistant:{target_uid}:2026-05-18",
        f"auth:blacklist:{target_uid}",
        f"auth:replay_warn:{target_uid}",
        "auth:initdata:memory-target",
    ):
        assert await cache.get_json(key) is None, f"{key} should be cleared"

    for key in (
        f"ai_task:u{other_uid}:keep",
        f"quota:ai:{other_uid}:2026-05-18",
        "auth:initdata:memory-other",
    ):
        assert await cache.get_json(key) is not None, f"{key} should survive"


@pytest.mark.asyncio
async def test_clear_user_ai_data_escapes_ai_analysis_glob_metacharacters():
    import fnmatch

    from api.database import get_engine, get_session_factory
    from api.models import AIAuditLog, User
    from api.routers.ai_analysis import clear_user_ai_data
    from api.services.cache import MemoryCache

    target_uid = 6363
    cache = MemoryCache()
    target_key = "ai_analysis:v3:123:phone *:cat=None"
    sibling_key = "ai_analysis:v3:123:phone secret:cat=None"
    await cache.set_json(target_key, {"ad_id": 123, "summary": "target"})
    await cache.set_json(sibling_key, {"ad_id": 123, "summary": "sibling"})

    class _FakeRedis:
        def __init__(self, storage):
            self._storage = storage

        async def scan(self, cursor, match="*", count=100):
            del count
            return 0, [k for k in self._storage if fnmatch.fnmatch(k, match)]

        async def delete(self, *keys):
            for key in keys:
                self._storage.pop(key, None)
            return len(keys)

    cache._client = _FakeRedis(cache._storage)
    engine = get_engine()
    session_factory = get_session_factory(engine)
    try:
        async with session_factory() as session:
            user = User(telegram_user_id=target_uid, first_name="Glob")
            session.add(user)
            await session.flush()
            session.add(AIAuditLog(
                user_id=user.id,
                endpoint="analyze",
                ad_id="123",
                query="phone *",
                model="test-model",
            ))
            await session.commit()

        await clear_user_ai_data(target_uid, cache=cache, session_factory=session_factory)
    finally:
        await engine.dispose()

    assert await cache.get_json(target_key) is None
    assert await cache.get_json(sibling_key) is not None


@pytest.mark.asyncio
async def test_revoke_pd_processing_pauses_trackers(client):
    """C-08: revoking ``pd_processing`` (the broadest consent) must
    pause every active tracker for that user with
    pause_reason='consent_revoked'. Other consent types do not
    affect trackers — they're tested separately below.
    """
    from sqlalchemy import select

    from api.models import Tracker, User

    # Grant + create the user row, then add an active tracker.
    grant = await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )
    assert grant.status_code in (200, 201, 204)

    sf = app.state.session_factory
    async with sf() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(
            Tracker(user_id=user.id, query="iphone", strict_mode=False),
        )
        await session.commit()

    revoke = await client.delete("/api/v1/account/consent/pd_processing")
    assert revoke.status_code == 204

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is True
        assert tracker.pause_reason == "consent_revoked"
        assert tracker.paused_at is not None


@pytest.mark.asyncio
async def test_revoke_ai_analysis_does_not_pause_trackers(client):
    """C-08: ``ai_analysis`` is a narrower scope — only AI features.
    Revoking it should NOT pause the user's trackers, which are a
    plain price-tracking feature.
    """
    from sqlalchemy import select

    from api.models import Tracker, User

    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "ai_analysis", "version": "2026.2"},
    )

    sf = app.state.session_factory
    async with sf() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(Tracker(user_id=user.id, query="iphone", strict_mode=False))
        await session.commit()

    revoke = await client.delete("/api/v1/account/consent/ai_analysis")
    assert revoke.status_code == 204

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is False
        assert tracker.pause_reason is None


@pytest.mark.asyncio
async def test_revoke_pd_processing_keeps_already_paused_trackers_paused(client):
    """C-08: a tracker that the user already paused manually for some
    other reason must keep its existing pause_reason (not be
    overwritten with 'consent_revoked').
    """
    from sqlalchemy import select

    from api.models import Tracker, User

    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )

    sf = app.state.session_factory
    async with sf() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(Tracker(
            user_id=user.id,
            query="iphone",
            strict_mode=False,
            paused=True,
            paused_at=datetime.now(UTC),
            pause_reason="manually_paused_by_user",
        ))
        await session.commit()

    revoke = await client.delete("/api/v1/account/consent/pd_processing")
    assert revoke.status_code == 204

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is True
        assert tracker.pause_reason == "manually_paused_by_user"


@pytest.mark.asyncio
async def test_revoke_pd_processing_takes_over_bot_blocked_pause(client):
    """RV-SCH-03: /start lifts a 'bot_blocked' pause, so a pd_processing
    revoke must re-label it 'consent_revoked' — otherwise the next /start
    would resume trackers the user just withdrew consent for."""
    from sqlalchemy import select

    from api.models import TRACKER_PAUSE_REASON_BOT_BLOCKED, Tracker, User

    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.2"},
    )

    sf = app.state.session_factory
    async with sf() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(Tracker(
            user_id=user.id,
            query="iphone",
            strict_mode=False,
            paused=True,
            paused_at=datetime.now(UTC),
            pause_reason=TRACKER_PAUSE_REASON_BOT_BLOCKED,
        ))
        await session.commit()

    revoke = await client.delete("/api/v1/account/consent/pd_processing")
    assert revoke.status_code == 204

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is True
        assert tracker.pause_reason == "consent_revoked"


# ── RV-SEC-05 / RV-BE-16: account erasure ──────────────────────────────


def _signed_init_data(telegram_user_id: int, *, auth_date: int, first_name: str = "Erased") -> str:
    """initData signed with the test BOT_TOKEN the way Telegram signs it."""
    from api.config import get_settings

    params = {
        "auth_date": str(auth_date),
        "user": json.dumps(
            {"id": telegram_user_id, "first_name": first_name}, separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(
        b"WebAppData",
        get_settings().bot_token.get_secret_value().encode("utf-8"),
        hashlib.sha256,
    ).digest()
    params["hash"] = hmac.new(
        secret, data_check_string.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return urlencode(params)


async def _drain_provision_tasks() -> None:
    """Let the auto-provision middleware's fire-and-forget upserts finish."""
    pending = [
        task for task in asyncio.all_tasks() if task.get_name().startswith("provision_user_")
    ]
    if pending:
        await asyncio.wait(pending, timeout=5)


async def _user_rows(session_factory, telegram_user_id: int) -> int:
    from sqlalchemy import func, select

    from api.models import User

    async with session_factory() as session:
        return await session.scalar(
            select(func.count(User.id)).where(User.telegram_user_id == telegram_user_id)
        )


@pytest.fixture
async def erasure_app():
    """A fresh app (its own auto-provision TTL state) with real initData auth."""
    from api.config import get_settings
    from api.database import get_engine, get_session_factory
    from api.main import create_app
    from api.services.cache import MemoryCache

    fresh_app = create_app()
    engine = get_engine()
    fresh_app.state.session_factory = get_session_factory(engine)
    fresh_app.state.cache = MemoryCache()
    fresh_app.state.settings = get_settings()
    try:
        yield fresh_app
    finally:
        await engine.dispose()


def _erasure_client(target_app, **transport_kwargs) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=target_app, **transport_kwargs),
        base_url="http://test",
        headers={"origin": "http://localhost:8081", "x-requested-with": "XMLHttpRequest"},
    )


async def _seed_erasure_user(session_factory, telegram_user_id: int) -> None:
    from api.models import User

    async with session_factory() as session:
        session.add(User(telegram_user_id=telegram_user_id, first_name="Erased"))
        await session.commit()


@pytest.mark.asyncio
async def test_delete_account_is_not_undone_by_the_same_init_data(erasure_app):
    """RV-SEC-05: after DELETE /account the Mini App reloaded with the same
    initData (valid for up to 2 h) and its first request — or the
    auto-provision middleware right after the DELETE itself — re-created
    the users row. initData minted before the deletion now gets 410 and
    nothing is written; a reopened Mini App (newer auth_date) starts over.
    """
    tg_id = 777001
    session_factory = erasure_app.state.session_factory
    await _seed_erasure_user(session_factory, tg_id)
    stale = {"X-Telegram-Init-Data": _signed_init_data(tg_id, auth_date=int(time.time()) - 60)}

    async with _erasure_client(erasure_app) as client:
        resp = await client.request(
            "DELETE", "/api/v1/account", json={"confirmation": "erased"}, headers=stale,
        )
        assert resp.status_code == 204
        await _drain_provision_tasks()
        assert await _user_rows(session_factory, tg_id) == 0

        for method, path, body in (
            ("GET", "/api/v1/profile/me", None),
            ("POST", "/api/v1/trackers", {"query": "iphone 15"}),
            ("POST", "/api/v1/account/consent",
             {"consent_type": "pd_processing", "version": "2026.2"}),
        ):
            resp = await client.request(method, path, json=body, headers=stale)
            assert resp.status_code == 410, path
            assert resp.json()["detail"]["error"] == "account_deleted"
            assert "Аккаунт удалён" in resp.json()["detail"]["message"]
        await _drain_provision_tasks()
        assert await _user_rows(session_factory, tg_id) == 0

        deleted_at = float(await erasure_app.state.cache.get(f"account:deleted:{tg_id}"))
        fresh = {"X-Telegram-Init-Data": _signed_init_data(tg_id, auth_date=int(deleted_at) + 1)}
        resp = await client.get("/api/v1/profile/me", headers=fresh)
        assert resp.status_code == 200
        await _drain_provision_tasks()
    assert await _user_rows(session_factory, tg_id) == 1


@pytest.mark.asyncio
async def test_erasure_tombstone_outlives_init_data(erasure_app, monkeypatch):
    """RV-SEC-05: the tombstone must live at least as long as the initData
    it refuses (TELEGRAM_INIT_DATA_MAX_AGE), and must not keep the
    Telegram id forever when the age check is switched off."""
    from api.services import account_erasure

    ttls: list[int | None] = []
    original_set = erasure_app.state.cache.set

    async def _recording_set(key, value, ttl=None):
        if key.startswith("account:deleted:"):
            ttls.append(ttl)
        await original_set(key, value, ttl)

    monkeypatch.setattr(erasure_app.state.cache, "set", _recording_set)
    await _seed_erasure_user(erasure_app.state.session_factory, 777005)
    stale = {"X-Telegram-Init-Data": _signed_init_data(777005, auth_date=int(time.time()) - 5)}
    async with _erasure_client(erasure_app) as client:
        resp = await client.request(
            "DELETE", "/api/v1/account", json={"confirmation": "777005"}, headers=stale,
        )
    assert resp.status_code == 204
    assert ttls and ttls[0] >= 7200  # default TELEGRAM_INIT_DATA_MAX_AGE
    assert 0 < account_erasure.tombstone_ttl_seconds(0) <= 7 * 24 * 3600


@pytest.mark.asyncio
async def test_auto_provision_task_skips_init_data_older_than_tombstone(monkeypatch):
    """RV-SEC-05: a request that authenticated just before DELETE /account
    queues the fire-and-forget upsert; the task re-checks the tombstone
    instead of resurrecting the erased row. Fresh initData still provisions."""
    from api import main
    from api.services.account_erasure import mark_account_deleted
    from api.services.cache import MemoryCache

    fresh_app = main.create_app()
    fresh_app.state.session_factory = object()
    fresh_app.state.cache = MemoryCache()
    calls: list[int] = []

    async def fake_ensure_user_exists(sf, user_id: int, first_name: str) -> None:
        calls.append(user_id)

    monkeypatch.setattr(main, "ensure_user_exists", fake_ensure_user_exists)

    @fresh_app.get("/_erasure-provision/{auth_date}")
    async def _provision_probe(request: Request, auth_date: int):
        request.state.telegram_user = TelegramInitData(
            user_id=777002, first_name="Race", raw={"auth_date": str(auth_date)},
        )
        return {"ok": True}

    fresh_app.router.routes.insert(0, fresh_app.router.routes.pop())

    deleted_at = time.time()
    await mark_account_deleted(
        fresh_app.state.cache, 777002, deleted_at=deleted_at, ttl_seconds=60,
    )
    async with _erasure_client(fresh_app) as client:
        assert (await client.get(f"/_erasure-provision/{int(deleted_at) - 30}")).status_code == 200
        await _drain_provision_tasks()
        assert calls == []
        assert (await client.get(f"/_erasure-provision/{int(deleted_at) + 1}")).status_code == 200
        await _drain_provision_tasks()
    assert calls == [777002]


@pytest.mark.asyncio
async def test_auto_provision_skips_the_delete_account_request_itself(monkeypatch):
    """RV-SEC-05: the middleware provisions *after* the handler ran, so the
    DELETE /account request re-created the user it had just erased. The
    handler flags its request; this holds even when the tombstone could
    not be written (no tombstone in the cache here)."""
    from api import main
    from api.services.cache import MemoryCache

    fresh_app = main.create_app()
    fresh_app.state.session_factory = object()
    fresh_app.state.cache = MemoryCache()
    calls: list[int] = []

    async def fake_ensure_user_exists(sf, user_id: int, first_name: str) -> None:
        calls.append(user_id)

    monkeypatch.setattr(main, "ensure_user_exists", fake_ensure_user_exists)

    @fresh_app.get("/_erasure-delete-probe")
    async def _delete_probe(request: Request):
        request.state.telegram_user = TelegramInitData(
            user_id=777007, first_name="Gone", raw={"auth_date": str(int(time.time()) - 60)},
        )
        request.state.account_deleted = True
        return {"ok": True}

    fresh_app.router.routes.insert(0, fresh_app.router.routes.pop())

    async with _erasure_client(fresh_app) as client:
        assert (await client.get("/_erasure-delete-probe")).status_code == 200
        await _drain_provision_tasks()
    assert calls == []


@pytest.mark.asyncio
async def test_ensure_user_refuses_request_authenticated_before_deletion(erasure_app):
    """RV-SEC-05: get_telegram_user refuses pre-deletion initData, but a
    request that authenticated a moment before DELETE /account committed
    could still reach ensure_user afterwards and insert the row again."""
    from api.dependencies import get_telegram_user
    from api.services.account_erasure import mark_account_deleted
    from api.services.workflow_store import ensure_user

    tg_id = 777003
    session_factory = erasure_app.state.session_factory

    @erasure_app.post("/_erasure-race")
    async def _race(telegram_user: TelegramInitData = Depends(get_telegram_user)):
        # DELETE /account lands between this request's auth and its insert.
        await mark_account_deleted(
            erasure_app.state.cache, telegram_user.user_id,
            deleted_at=time.time(), ttl_seconds=60,
        )
        async with session_factory() as session:
            await ensure_user(session, telegram_user_id=telegram_user.user_id)
            await session.commit()
        return {"ok": True}

    erasure_app.router.routes.insert(0, erasure_app.router.routes.pop())

    stale = {"X-Telegram-Init-Data": _signed_init_data(tg_id, auth_date=int(time.time()) - 60)}
    async with _erasure_client(erasure_app) as client:
        resp = await client.post("/_erasure-race", headers=stale)
        await _drain_provision_tasks()
    assert resp.status_code == 410
    assert await _user_rows(session_factory, tg_id) == 0


@pytest.mark.asyncio
async def test_delete_account_proceeds_when_tombstone_cannot_be_written(erasure_app, caplog):
    """RV-SEC-05: the right to erasure must not depend on Redis. When the
    tombstone doesn't stick (RedisCache.set swallows errors) the data is
    still deleted and the degraded state is logged."""
    from api.services.cache import MemoryCache

    class _WriteLostCache(MemoryCache):
        async def set(self, key, value, ttl=None):
            return None

    erasure_app.state.cache = _WriteLostCache()
    tg_id = 777004
    await _seed_erasure_user(erasure_app.state.session_factory, tg_id)
    stale = {"X-Telegram-Init-Data": _signed_init_data(tg_id, auth_date=int(time.time()) - 60)}

    with caplog.at_level("WARNING", logger="api.routers.consent"):
        async with _erasure_client(erasure_app) as client:
            resp = await client.request(
                "DELETE", "/api/v1/account", json={"confirmation": "erased"}, headers=stale,
            )
            await _drain_provision_tasks()
    assert resp.status_code == 204
    assert await _user_rows(erasure_app.state.session_factory, tg_id) == 0
    assert "without an erasure tombstone" in caplog.text


@pytest.mark.asyncio
async def test_failed_erasure_drops_the_tombstone(erasure_app, monkeypatch):
    """RV-SEC-05: the tombstone is written before the DB delete; if the
    delete fails nothing was erased, so the user's still-valid session
    must keep working instead of getting 410 for two hours."""
    from api.routers import consent as consent_router

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(consent_router, "purge_user_data", _boom)
    tg_id = 777006
    await _seed_erasure_user(erasure_app.state.session_factory, tg_id)
    stale = {"X-Telegram-Init-Data": _signed_init_data(tg_id, auth_date=int(time.time()) - 60)}

    async with _erasure_client(erasure_app, raise_app_exceptions=False) as client:
        resp = await client.request(
            "DELETE", "/api/v1/account", json={"confirmation": "erased"}, headers=stale,
        )
        assert resp.status_code == 500
        assert await erasure_app.state.cache.get(f"account:deleted:{tg_id}") is None
        resp = await client.get("/api/v1/profile/me", headers=stale)
        await _drain_provision_tasks()
    assert resp.status_code == 200
    assert await _user_rows(erasure_app.state.session_factory, tg_id) == 1


async def _seed_account_graph(session, *, telegram_user_id: int, first_name: str):
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

    user = User(telegram_user_id=telegram_user_id, first_name=first_name)
    session.add(user)
    await session.flush()
    tracker = Tracker(user_id=user.id, query=f"iphone {telegram_user_id}")
    lead = LeadItem(
        user_id=user.id,
        ad_id=telegram_user_id,
        query="iphone",
        title="iPhone 13",
        link=f"https://www.kufar.by/item/{telegram_user_id}",
        price_byn=900,
    )
    session.add_all([tracker, lead])
    await session.flush()
    session.add_all([
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=telegram_user_id,
            query="iphone",
            event_type="new_listing",
            title="iPhone 13",
            link=f"https://www.kufar.by/item/{telegram_user_id}",
        ),
        LeadItemPriceSnapshot(lead_item_id=lead.id, price_byn=900),
        DealExpense(lead_id=lead.id, user_id=user.id, expense_type="delivery", amount_byn=10),
        LeadReminder(lead_id=lead.id, user_id=user.id, remind_at=datetime.now(UTC)),
        UserConsent(user_id=user.id, consent_type="pd_processing", version="2026.2"),
        AIAuditLog(user_id=user.id, endpoint="analyze", query="iphone", model="test-model"),
        Contact(user_id=user.id, phone=f"+375{telegram_user_id}"),
        SavedSearch(user_id=user.id, name="Phones", query="iphone"),
        TelegramNotificationDLQ(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            source="test",
            message="failed notification",
            error_kind="retryable",
        ),
    ])
    await session.flush()
    return user


async def _owned_row_counts(session, user_id: int) -> dict[str, int]:
    from sqlalchemy import func, select

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

    counts = {}
    for model in (
        Tracker, TrackerEvent, LeadItem, DealExpense, LeadReminder, UserConsent,
        AIAuditLog, Contact, SavedSearch, TelegramNotificationDLQ,
    ):
        counts[model.__tablename__] = await session.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )
    counts["lead_item_price_snapshots"] = await session.scalar(
        select(func.count())
        .select_from(LeadItemPriceSnapshot)
        .join(LeadItem, LeadItemPriceSnapshot.lead_item_id == LeadItem.id)
        .where(LeadItem.user_id == user_id)
    )
    counts["users"] = await session.scalar(
        select(func.count()).select_from(User).where(User.id == user_id)
    )
    return counts


@pytest.mark.asyncio
async def test_delete_account_bulk_deletes_the_whole_graph(client):
    """RV-BE-16: session.delete(user) walked the ORM cascade — SELECT of every
    tracker, event, lead, snapshot and up to a year of ai_audit_log, then
    row-by-row DELETEs. Erasure is now one bulk DELETE per table: no child
    row is loaded, every table the user owns ends up empty for them (also
    on SQLite, where FK cascades are off) and another user's data survives.

    RV-SEC-05: admin_audit_log rows about the user stay, minus anything that
    identifies them; rows the user wrote as an admin only lose the actor id.
    """
    from sqlalchemy import event, select

    from api.models import AdminAuditLog

    session_factory = app.state.session_factory
    async with session_factory() as session:
        target = await _seed_account_graph(
            session, telegram_user_id=_fake_telegram_user().user_id, first_name="ConsentTest",
        )
        other = await _seed_account_graph(session, telegram_user_id=555777, first_name="Other")
        about_target = AdminAuditLog(
            actor_user_id=other.id,
            target_user_id=target.id,
            action="user_status_updated",
            payload={
                "target_telegram_user_id": target.telegram_user_id,
                "old": {"status_code": "scout", "granted_at": None, "expires_at": None,
                        "note": "VIP, +375291234567"},
                "new": {"status_code": "flipper", "granted_at": "2026-10-01T00:00:00+00:00",
                        "expires_at": None, "note": "звонил сам"},
            },
            ip_address="10.0.0.1",
        )
        by_target_payload = {
            "target_telegram_user_id": other.telegram_user_id,
            "old": {"status_code": "bare_search", "granted_at": None, "expires_at": None,
                    "note": None},
            "new": {"status_code": "scout", "granted_at": "2026-10-02T00:00:00+00:00",
                    "expires_at": None, "note": "promo"},
        }
        by_target = AdminAuditLog(
            actor_user_id=target.id,
            target_user_id=other.id,
            action="user_status_updated",
            payload=by_target_payload,
        )
        session.add_all([about_target, by_target])
        await session.commit()
        target_id, other_id = target.id, other.id
        about_target_id, by_target_id = about_target.id, by_target.id
        other_before = await _owned_row_counts(session, other_id)
    assert all(other_before.values())

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    sync_engine = session_factory.kw["bind"].sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        resp = await client.request(
            "DELETE", "/api/v1/account", json={"confirmation": "consenttest"},
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)
    assert resp.status_code == 204

    owned = (
        "trackers", "tracker_events", "lead_items", "lead_item_price_snapshots",
        "deal_expenses", "lead_reminders", "user_consents", "ai_audit_log",
        "contacts", "saved_searches", "telegram_notification_dlq",
    )
    graph_loads = [
        sql for sql in statements
        if sql.lstrip().upper().startswith("SELECT")
        and any(re.search(rf"\bFROM {table}\b", sql) for table in owned)
    ]
    assert graph_loads == [], "erasure must not load the user's rows into the worker"

    async with session_factory() as session:
        assert all(
            count == 0 for count in (await _owned_row_counts(session, target_id)).values()
        )
        assert await _owned_row_counts(session, other_id) == other_before
        audit = {
            row.id: row for row in (await session.execute(select(AdminAuditLog))).scalars()
        }
    erased = audit[about_target_id]
    assert erased.target_user_id is None
    assert erased.actor_user_id == other_id
    assert erased.ip_address == "10.0.0.1"
    assert erased.payload == {
        "old": {"status_code": "scout", "granted_at": None, "expires_at": None},
        "new": {"status_code": "flipper", "granted_at": "2026-10-01T00:00:00+00:00",
                "expires_at": None},
        "target_erased": True,
    }
    assert "375291234567" not in json.dumps(erased.payload, ensure_ascii=False)
    assert audit[by_target_id].actor_user_id is None
    assert audit[by_target_id].target_user_id == other_id
    assert audit[by_target_id].payload == by_target_payload
