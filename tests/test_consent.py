"""Tests for consent and account management router."""

from __future__ import annotations

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
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"origin": "http://localhost:8081"},
    ) as c:
        yield c


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
        json={"consent_type": "ai_analysis", "version": "2026.1"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["granted"] is True
    assert data["consent_type"] == "ai_analysis"
    assert data["version"] == "2026.1"


@pytest.mark.asyncio
async def test_grant_consent_idempotent(client):
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.1"},
    )
    # Second grant with same version should return 201 (not error)
    resp = await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.1"},
    )
    assert resp.status_code == 201
    assert resp.json()["granted"] is True


@pytest.mark.asyncio
async def test_grant_consent_rejects_invalid_type(client):
    resp = await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "bogus", "version": "2026.1"},
    )
    assert resp.status_code == 400


# ── Consent status after grant ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_status_shows_granted_after_grant(client):
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "cross_border", "version": "2026.1"},
    )
    resp = await client.get("/api/v1/account/consent/cross_border")
    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is True
    assert data["version"] == "2026.1"
    assert data["granted_at"] is not None


# ── Revoke consent ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_consent(client):
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "ai_analysis", "version": "2026.1"},
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
        json={"consent_type": "ai_analysis", "version": "2026.1"},
    )
    resp = await client.get("/api/v1/account/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "profile" in data
    assert "consents" in data
    assert "exported_at" in data
    # Should have at least one consent
    assert len(data["consents"]) >= 1


# ── Account deletion ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_account(client):
    # Create consent first
    await client.post(
        "/api/v1/account/consent",
        json={"consent_type": "pd_processing", "version": "2026.1"},
    )
    resp = await client.delete("/api/v1/account")
    assert resp.status_code == 204
