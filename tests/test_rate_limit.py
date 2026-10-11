from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from api.config import Settings, get_settings
from api.rate_limit import (
    DEFAULT_RATE_LIMIT,
    MAX_APPLICATION_LIMIT_PER_MINUTE,
    MIN_APPLICATION_LIMIT_PER_MINUTE,
    application_limit_per_minute,
    limiter,
    rate_limit_key,
)

BOT_TOKEN = "7123456789:AAFtesttoken"
EXPIRED_AUTH_DATE = 1


def _signed_init_data(
    user_id: int, *, auth_date: int | None = None, bot_token: str = BOT_TOKEN
) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    data = {"user": f'{{"id":{user_id},"first_name":"Test"}}', "auth_date": str(auth_date)}
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


def _request(headers: dict[str, str] | None = None, client_host: str = "203.0.113.7") -> Request:
    raw_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/listings",
        "headers": raw_headers,
        "client": (client_host, 12345),
        "query_string": b"",
    }
    return Request(scope)


def _settings(**overrides: object) -> Settings:
    base = {
        "bot_token": BOT_TOKEN,
        "database_url": "sqlite+aiosqlite:///./unused.db",
        "redis_url": "redis://localhost:6379/0",
        "api_base_url": "https://kufar-analytics.example.com",
        "mini_app_url": "https://kufar-analytics.example.com/app",
    }
    return Settings(_env_file=None, **{**base, **overrides})


def _per_route_default() -> int:
    return int(DEFAULT_RATE_LIMIT.split("/")[0])


def test_key_uses_telegram_user_for_fresh_init_data() -> None:
    request = _request({"X-Telegram-Init-Data": _signed_init_data(4242)})
    assert rate_limit_key(request) == "tg:4242"


def test_key_ignores_expired_init_data() -> None:
    # A leaked, long expired initData must not spend its owner's budget (r3-core-2).
    expired = _signed_init_data(4242, auth_date=EXPIRED_AUTH_DATE)
    assert rate_limit_key(_request({"X-Telegram-Init-Data": expired})) == "203.0.113.7"


def test_key_follows_init_data_max_age_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = _signed_init_data(4242, auth_date=EXPIRED_AUTH_DATE)
    monkeypatch.setenv("INIT_DATA_MAX_AGE_SECONDS", "0")
    get_settings.cache_clear()
    # 0 disables the age check for authentication, so keying follows it too.
    assert rate_limit_key(_request({"X-Telegram-Init-Data": expired})) == "tg:4242"


def test_key_falls_back_to_client_address_for_forged_init_data() -> None:
    forged = _signed_init_data(4242, bot_token="1:other-bot-token")
    assert rate_limit_key(_request({"X-Telegram-Init-Data": forged})) == "203.0.113.7"
    assert rate_limit_key(_request({"X-Telegram-Init-Data": "garbage"})) == "203.0.113.7"


def test_key_falls_back_to_client_address_without_header() -> None:
    assert rate_limit_key(_request(client_host="198.51.100.1")) == "198.51.100.1"


@pytest.mark.parametrize(
    ("delay", "expected"),
    [
        (0.5, 60),
        (1.0, MIN_APPLICATION_LIMIT_PER_MINUTE),
        (5.0, MIN_APPLICATION_LIMIT_PER_MINUTE),
        (0.25, MAX_APPLICATION_LIMIT_PER_MINUTE),
        (0.1, MAX_APPLICATION_LIMIT_PER_MINUTE),
        (0.0, MAX_APPLICATION_LIMIT_PER_MINUTE),
    ],
)
def test_application_limit_follows_kufar_pacer(delay: float, expected: int) -> None:
    assert application_limit_per_minute(_settings(kufar_request_delay=delay)) == expected


def test_application_limit_can_be_configured() -> None:
    settings = _settings(kufar_request_delay=0.5, api_rate_limit_per_minute=7)
    assert application_limit_per_minute(settings) == 7


def test_application_limit_is_below_per_route_default_at_default_pacing() -> None:
    assert application_limit_per_minute() < _per_route_default()


def test_price_stats_uses_shared_limiter_with_stricter_limit() -> None:
    from api import main
    from api.routers import price_stats

    assert price_stats.limiter is limiter
    assert not hasattr(main, "Limiter")
    route_limits = limiter._route_limits["api.routers.price_stats.get_price_stats"]
    assert [str(item.limit) for item in route_limits] == ["30 per 1 minute"]


def test_application_budget_is_shared_across_routes() -> None:
    """One key cannot multiply its budget by spreading requests over routes (r3-core-1)."""
    from api.main import create_app

    budget = application_limit_per_minute()
    user_headers = {"X-Telegram-Init-Data": _signed_init_data(777101)}
    other_headers = {"X-Telegram-Init-Data": _signed_init_data(777102)}
    client = TestClient(create_app())

    # Requests without the required query fail validation (422) without touching Kufar,
    # but they still count against the budget.
    paths = ("/api/v1/listings", "/api/v1/segments", "/api/v1/geography")
    for index in range(budget):
        response = client.get(paths[index % len(paths)], headers=user_headers)
        assert response.status_code == 422

    limited = client.get("/api/v1/price-history", headers=user_headers)
    assert limited.status_code == 429
    assert limited.json()["detail"].startswith("Слишком много запросов")

    # Another Telegram user behind the same proxy address keeps its own budget.
    assert client.get("/api/v1/listings", headers=other_headers).status_code == 422


def test_application_budget_covers_routes_with_own_limit() -> None:
    from api.main import create_app

    budget = application_limit_per_minute()
    headers = {"X-Telegram-Init-Data": _signed_init_data(777103)}
    client = TestClient(create_app())
    for _ in range(budget):
        client.get("/api/v1/listings", headers=headers)

    # /price-stats has its own @limiter.limit, which slowapi's middleware skips.
    assert client.get("/api/v1/price-stats", headers=headers).status_code == 429


def test_expired_init_data_does_not_spend_owner_budget() -> None:
    from api.main import create_app

    budget = application_limit_per_minute()
    expired = {"X-Telegram-Init-Data": _signed_init_data(777104, auth_date=EXPIRED_AUTH_DATE)}
    fresh = {"X-Telegram-Init-Data": _signed_init_data(777104)}
    client = TestClient(create_app())
    for _ in range(budget + 1):
        client.get("/api/v1/listings", headers=expired)

    assert client.get("/api/v1/listings", headers=expired).status_code == 429
    assert client.get("/api/v1/listings", headers=fresh).status_code == 422


def test_per_route_default_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import create_app

    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "1000")
    get_settings.cache_clear()
    allowed = _per_route_default()
    headers = {"X-Telegram-Init-Data": _signed_init_data(777105)}
    client = TestClient(create_app())
    for _ in range(allowed):
        assert client.get("/api/v1/listings", headers=headers).status_code == 422

    assert client.get("/api/v1/listings", headers=headers).status_code == 429
    # The per-route bucket is per path: another route still answers.
    assert client.get("/api/v1/segments", headers=headers).status_code == 422


def test_rate_limited_response_keeps_cors_headers() -> None:
    from api.main import create_app

    budget = application_limit_per_minute()
    headers = {
        "X-Telegram-Init-Data": _signed_init_data(777106),
        "Origin": "https://kufar-analytics.example.com",
    }
    client = TestClient(create_app())
    for _ in range(budget):
        client.get("/api/v1/segments", headers=headers)

    limited = client.get("/api/v1/segments", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == "https://kufar-analytics.example.com"


def test_requests_outside_api_prefix_skip_application_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.main import create_app

    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    client = TestClient(create_app())
    assert {client.get("/healthz").status_code for _ in range(5)} == {200}
    assert client.get("/api/v1/listings").status_code == 422
    assert client.get("/api/v1/listings").status_code == 429
