"""Tests for consent and account management router."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
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
    assert resp.status_code == 400


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


# ── Account export ──────────────────────────────────────────────────────


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
                query="iphone",
                result_summary="ok",
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
        f"auth:blacklist:{target_uid}",
        "auth:initdata:targetdigest",
    ):
        assert await cache.get_json(key) is None, f"{key} should be cleared"

    # Sibling user's entries must still be there.
    for key in (
        f"ai_task:u{other_uid}:keep",
        f"ai_listing:u{other_uid}:keep",
        f"ai_rate:{other_uid}:default",
        "auth:initdata:otherdigest",
    ):
        assert await cache.get_json(key) is not None, f"{key} should survive"
