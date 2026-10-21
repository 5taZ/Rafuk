"""RV-OPS-10: every AI endpoint enforces the AI consent over HTTP.

The suite runs with AUTH_BYPASS=true (conftest), which also turns
``_check_ai_consent`` into a no-op, and the gate itself was only ever
tested by calling the function directly. An AI route that forgot the
check would have passed every test. Here the bypass is off for the
duration of the test, the user signs in with a real HMAC-signed
initData, consents are granted through ``POST /account/consent`` and
every consent-gated AI route is called over HTTP.

Adding an AI route means adding it to ``AI_CONSENT_GATED_ROUTES`` (with a
payload that passes validation) or, with a reason, to
``CONSENT_EXEMPT_AI_ROUTES`` — the inventory test fails until you do.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import itertools
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.config import get_settings
from api.services.consent_policy import CURRENT_POLICY_VERSION

# Minimal bodies that pass request validation, so the handler runs.
AI_CONSENT_GATED_ROUTES: dict[str, dict[str, object]] = {
    "/api/v1/ai/analyze": {"ad_id": 101, "query": "iphone 15"},
    "/api/v1/ai/negotiate": {
        "ad_id": 101,
        "asking_price_byn": 1000,
        "my_offer_byn": 800,
        "query": "iphone 15",
    },
    "/api/v1/ai/price-advice": {"query": "iphone 15", "current_price_byn": 1000},
    "/api/v1/ai/listing-assistant": {"title": "iPhone 15 128GB"},
}

# AI routes that send nothing to the provider: polling a task that was
# started behind the gate (revoke wipes those tasks), and a rating metric.
CONSENT_EXEMPT_AI_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/v1/ai/task/{task_id}"),
    ("POST", "/api/v1/ai/feedback"),
}

_REQUIRED_CONSENTS = ("ai_analysis", "cross_border", "pd_processing")
_telegram_ids = itertools.count(880_001)


def _signed_init_data(telegram_user_id: int) -> str:
    """initData signed exactly like Telegram does (WebAppData HMAC)."""
    bot_token = get_settings().bot_token.get_secret_value()
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHrvops10",
        "user": json.dumps({"id": telegram_user_id, "first_name": "Consent"}),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


def _uses_ai(route: APIRoute) -> bool:
    # Every AI handler starts with the provider availability guard, so a
    # module that imports it is an AI router even outside /api/v1/ai/.
    return "_check_ai_available" in inspect.unwrap(route.endpoint).__globals__


def test_every_ai_route_is_consent_gated_or_explicitly_exempt() -> None:
    from api.main import create_app

    ai_routes = {
        (method, route.path)
        for route in create_app().routes
        if isinstance(route, APIRoute)
        and (route.path.startswith("/api/v1/ai/") or _uses_ai(route))
        for method in route.methods
    }
    gated = {("POST", path) for path in AI_CONSENT_GATED_ROUTES}
    assert ai_routes == gated | CONSENT_EXEMPT_AI_ROUTES, (
        "AI route inventory changed — list the route in AI_CONSENT_GATED_ROUTES "
        "(it must call _check_ai_consent) or justify it in CONSENT_EXEMPT_AI_ROUTES"
    )


@pytest.fixture
def production_auth(monkeypatch: pytest.MonkeyPatch):
    """Real Telegram auth and a real consent gate for this test only; the
    suite-wide AUTH_BYPASS default is left alone (configure_env restores
    it and clears the settings cache before the next test)."""
    from api.routers import ai_analysis

    monkeypatch.setenv("AUTH_BYPASS", "false")
    get_settings.cache_clear()
    # The availability check runs before the consent gate; without a
    # configured provider every route would stop at 503.
    monkeypatch.setattr(
        ai_analysis,
        "get_ai_service",
        lambda: SimpleNamespace(
            available=True, analysis_model="stub", listing_assistant_model="stub",
        ),
    )
    assert get_settings().auth_bypass is False
    yield
    get_settings.cache_clear()


@pytest.fixture
def ai_client(production_auth):
    from api.main import create_app

    with TestClient(create_app()) as client:
        yield client


def _headers(telegram_user_id: int) -> dict[str, str]:
    return {"X-Telegram-Init-Data": _signed_init_data(telegram_user_id)}


def _grant(client: TestClient, telegram_user_id: int, *consent_types: str) -> None:
    for consent_type in consent_types:
        response = client.post(
            "/api/v1/account/consent",
            json={"consent_type": consent_type, "version": CURRENT_POLICY_VERSION},
            headers=_headers(telegram_user_id),
        )
        assert response.status_code == 201, response.text


def _error(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("error") if isinstance(detail, dict) else None


@pytest.mark.parametrize("path", sorted(AI_CONSENT_GATED_ROUTES))
def test_ai_route_requires_auth_without_bypass(ai_client: TestClient, path: str) -> None:
    response = ai_client.post(path, json=AI_CONSENT_GATED_ROUTES[path])
    assert response.status_code == 401, response.text


@pytest.mark.parametrize("path", sorted(AI_CONSENT_GATED_ROUTES))
def test_ai_route_rejects_user_without_consent(ai_client: TestClient, path: str) -> None:
    telegram_user_id = next(_telegram_ids)
    response = ai_client.post(
        path, json=AI_CONSENT_GATED_ROUTES[path], headers=_headers(telegram_user_id),
    )
    assert response.status_code == 403, response.text
    assert _error(response) == "consent_required"
    assert response.json()["detail"]["consent_type"] == "ai_analysis"


@pytest.mark.parametrize("path", sorted(AI_CONSENT_GATED_ROUTES))
@pytest.mark.parametrize("missing", _REQUIRED_CONSENTS)
def test_ai_route_rejects_partial_consent(
    ai_client: TestClient, path: str, missing: str,
) -> None:
    telegram_user_id = next(_telegram_ids)
    _grant(ai_client, telegram_user_id, *(c for c in _REQUIRED_CONSENTS if c != missing))

    response = ai_client.post(
        path, json=AI_CONSENT_GATED_ROUTES[path], headers=_headers(telegram_user_id),
    )
    assert response.status_code == 403, response.text
    assert _error(response) == "consent_required"
    assert response.json()["detail"]["consent_type"] == missing


@pytest.mark.parametrize("path", sorted(AI_CONSENT_GATED_ROUTES))
def test_ai_route_rejects_user_after_revoke(ai_client: TestClient, path: str) -> None:
    telegram_user_id = next(_telegram_ids)
    _grant(ai_client, telegram_user_id, *_REQUIRED_CONSENTS)
    revoke = ai_client.delete(
        "/api/v1/account/consent/ai_analysis", headers=_headers(telegram_user_id),
    )
    assert revoke.status_code == 204, revoke.text

    response = ai_client.post(
        path, json=AI_CONSENT_GATED_ROUTES[path], headers=_headers(telegram_user_id),
    )
    assert response.status_code == 403, response.text
    assert _error(response) == "consent_required"
    assert response.json()["detail"]["consent_type"] == "ai_analysis"


@pytest.mark.parametrize("path", sorted(AI_CONSENT_GATED_ROUTES))
def test_ai_route_passes_consent_gate_once_granted(ai_client: TestClient, path: str) -> None:
    """With all three consents the request gets past the consent gate. A
    fresh user is on ``bare_search`` (no AI quota), so the next gate —
    the status entitlement — answers, without any provider call."""
    telegram_user_id = next(_telegram_ids)
    _grant(ai_client, telegram_user_id, *_REQUIRED_CONSENTS)

    response = ai_client.post(
        path, json=AI_CONSENT_GATED_ROUTES[path], headers=_headers(telegram_user_id),
    )
    assert _error(response) != "consent_required", response.text
    assert response.status_code == 403, response.text
    assert _error(response) == "premium_required"
