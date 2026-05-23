from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.main import app
from api.middleware.telegram_auth import TelegramInitData
from api.models import User
from api.services.cache import MemoryCache

_PROFILE_USER_ID = 555001


def _fake_profile_user() -> TelegramInitData:
    return TelegramInitData(
        user_id=_PROFILE_USER_ID,
        first_name="Fallback",
        raw={
            "user": json.dumps(
                {"id": _PROFILE_USER_ID, "first_name": "Staz", "username": "staz"}
            )
        },
    )


@pytest.fixture
async def profile_client():
    from api.config import get_settings
    from api.database import get_engine, get_session_factory
    from api.dependencies import get_telegram_user

    engine = get_engine()
    cache = MemoryCache()
    app.state.session_factory = get_session_factory(engine)
    app.state.cache = cache
    app.state.settings = get_settings()
    app.dependency_overrides[get_telegram_user] = _fake_profile_user
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app.state.session_factory, cache
    finally:
        app.dependency_overrides.pop(get_telegram_user, None)
        for attr in ("session_factory", "cache", "settings"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
        await engine.dispose()


@pytest.mark.asyncio
async def test_profile_me_creates_new_user_with_bare_search(profile_client) -> None:
    client, session_factory, _cache = profile_client

    resp = await client.get("/api/v1/profile/me")

    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["telegram_user_id"] == _PROFILE_USER_ID
    assert data["user"]["first_name"] == "Staz"
    assert data["user"]["username"] == "staz"
    assert data["status"]["code"] == "bare_search"
    assert data["limits"]["ai"] == {
        "used": 0,
        "limit": 0,
        "remaining": 0,
        "resets_at": data["limits"]["ai"]["resets_at"],
    }
    assert data["permissions"] == {
        "can_use_ai": False,
        "can_use_assistant": False,
        "is_admin": False,
    }

    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == _PROFILE_USER_ID))
        ).scalar_one()
    assert user.first_name == "Staz"
    assert user.username == "staz"


@pytest.mark.asyncio
async def test_profile_me_marks_allowlisted_admin(profile_client) -> None:
    from api.dependencies import get_settings_dependency

    client, _session_factory, _cache = profile_client
    app.dependency_overrides[get_settings_dependency] = lambda: SimpleNamespace(
        admin_telegram_user_ids=f"1,{_PROFILE_USER_ID},bad"
    )
    try:
        resp = await client.get("/api/v1/profile/me")
    finally:
        app.dependency_overrides.pop(get_settings_dependency, None)

    assert resp.status_code == 200
    assert resp.json()["permissions"]["is_admin"] is True


@pytest.mark.asyncio
async def test_profile_me_hides_expired_status_metadata(profile_client) -> None:
    client, session_factory, _cache = profile_client
    expired_at = datetime.now(UTC) - timedelta(days=1)
    granted_at = expired_at - timedelta(days=7)

    async with session_factory() as session:
        user = User(
            telegram_user_id=_PROFILE_USER_ID,
            first_name="Expired",
            account_status_code="scout",
            status_granted_at=granted_at,
            status_expires_at=expired_at,
        )
        session.add(user)
        await session.commit()

    resp = await client.get("/api/v1/profile/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"]["code"] == "bare_search"
    assert body["status"]["granted_at"] is None
    assert body["status"]["expires_at"] is None
    assert body["permissions"]["can_use_ai"] is False


@pytest.mark.asyncio
async def test_quota_snapshot_clamps_used_to_limit() -> None:
    from api.services.account_status import QUOTA_BUCKET_AI, quota_key, quota_snapshot

    cache = MemoryCache()
    now = datetime.fromisoformat("2026-05-18T12:00:00+03:00")
    await cache.set(quota_key(QUOTA_BUCKET_AI, 42, now=now), "15")

    snapshot = await quota_snapshot(
        cache,
        bucket=QUOTA_BUCKET_AI,
        telegram_user_id=42,
        limit=10,
        now=now,
    )

    assert snapshot.used == 10
    assert snapshot.limit == 10
    assert snapshot.remaining == 0
    assert snapshot.resets_at.isoformat() == "2026-05-19T00:00:00+03:00"


@pytest.mark.asyncio
async def test_consume_quota_allows_first_and_rejects_second() -> None:
    from api.services.account_status import QUOTA_BUCKET_AI, consume_quota

    cache = MemoryCache()
    now = datetime.fromisoformat("2026-05-18T12:00:00+03:00")

    first = await consume_quota(
        cache,
        bucket=QUOTA_BUCKET_AI,
        telegram_user_id=42,
        limit=1,
        now=now,
    )
    assert first.used == 1
    assert first.remaining == 0

    with pytest.raises(HTTPException) as exc:
        await consume_quota(
            cache,
            bucket=QUOTA_BUCKET_AI,
            telegram_user_id=42,
            limit=1,
            now=now,
        )
    assert exc.value.status_code == 429
    assert exc.value.detail["error"] == "quota_exceeded"
    assert exc.value.detail["used"] == 1
    assert exc.value.detail["limit"] == 1


@pytest.mark.asyncio
async def test_zero_quota_returns_premium_required_without_incrementing() -> None:
    from api.services.account_status import QUOTA_BUCKET_ASSISTANT, consume_quota, quota_snapshot

    cache = MemoryCache()
    now = datetime.fromisoformat("2026-05-18T12:00:00+03:00")

    with pytest.raises(HTTPException) as exc:
        await consume_quota(
            cache,
            bucket=QUOTA_BUCKET_ASSISTANT,
            telegram_user_id=42,
            limit=0,
            now=now,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "premium_required"
    snapshot = await quota_snapshot(
        cache,
        bucket=QUOTA_BUCKET_ASSISTANT,
        telegram_user_id=42,
        limit=0,
        now=now,
    )
    assert snapshot.used == 0


@pytest.mark.asyncio
async def test_quota_buckets_are_independent() -> None:
    from api.services.account_status import (
        QUOTA_BUCKET_AI,
        QUOTA_BUCKET_ASSISTANT,
        consume_quota,
        quota_snapshot,
    )

    cache = MemoryCache()
    now = datetime.fromisoformat("2026-05-18T12:00:00+03:00")
    await consume_quota(
        cache,
        bucket=QUOTA_BUCKET_ASSISTANT,
        telegram_user_id=42,
        limit=3,
        now=now,
    )

    ai_snapshot = await quota_snapshot(
        cache,
        bucket=QUOTA_BUCKET_AI,
        telegram_user_id=42,
        limit=10,
        now=now,
    )
    assistant_snapshot = await quota_snapshot(
        cache,
        bucket=QUOTA_BUCKET_ASSISTANT,
        telegram_user_id=42,
        limit=3,
        now=now,
    )

    assert ai_snapshot.used == 0
    assert ai_snapshot.remaining == 10
    assert assistant_snapshot.used == 1
    assert assistant_snapshot.remaining == 2


@pytest.mark.asyncio
async def test_profile_me_throttles_last_seen_at(profile_client) -> None:
    """BE-DEEP-4: second GET within 60s does not update last_seen_at."""
    client, session_factory, _cache = profile_client

    resp1 = await client.get("/api/v1/profile/me")
    assert resp1.status_code == 200

    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == _PROFILE_USER_ID))
        ).scalar_one()
        first_seen = user.last_seen_at

    resp2 = await client.get("/api/v1/profile/me")
    assert resp2.status_code == 200

    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == _PROFILE_USER_ID))
        ).scalar_one()
        second_seen = user.last_seen_at

    assert first_seen == second_seen
