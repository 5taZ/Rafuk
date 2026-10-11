from __future__ import annotations

import hashlib
import hmac
import urllib.parse

from fastapi.testclient import TestClient
from starlette.requests import Request

from api.rate_limit import DEFAULT_RATE_LIMIT, limiter, rate_limit_key

BOT_TOKEN = "7123456789:AAFtesttoken"


def _signed_init_data(user_id: int, *, auth_date: int = 1, bot_token: str = BOT_TOKEN) -> str:
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


def test_key_uses_telegram_user_for_signed_init_data() -> None:
    # auth_date=1 is long expired: the age only matters for authentication, not for keying.
    request = _request({"X-Telegram-Init-Data": _signed_init_data(4242)})
    assert rate_limit_key(request) == "tg:4242"


def test_key_falls_back_to_client_address_for_forged_init_data() -> None:
    forged = _signed_init_data(4242, bot_token="1:other-bot-token")
    assert rate_limit_key(_request({"X-Telegram-Init-Data": forged})) == "203.0.113.7"
    assert rate_limit_key(_request({"X-Telegram-Init-Data": "garbage"})) == "203.0.113.7"


def test_key_falls_back_to_client_address_without_header() -> None:
    assert rate_limit_key(_request(client_host="198.51.100.1")) == "198.51.100.1"


def test_price_stats_uses_shared_limiter_with_stricter_limit() -> None:
    from api import main
    from api.routers import price_stats

    assert price_stats.limiter is limiter
    assert not hasattr(main, "Limiter")
    route_limits = limiter._route_limits["api.routers.price_stats.get_price_stats"]
    assert [str(item.limit) for item in route_limits] == ["30 per 1 minute"]


def test_listings_is_limited_by_default_per_telegram_user() -> None:
    from api.main import create_app

    app = create_app()
    allowed = int(DEFAULT_RATE_LIMIT.split("/")[0])
    user_headers = {"X-Telegram-Init-Data": _signed_init_data(777001)}
    other_headers = {"X-Telegram-Init-Data": _signed_init_data(777002)}

    client = TestClient(app)
    # Requests without the required query fail validation (422) without touching Kufar,
    # but they still count against the limit.
    for _ in range(allowed):
        assert client.get("/api/v1/listings", headers=user_headers).status_code == 422

    limited = client.get("/api/v1/listings", headers=user_headers)
    assert limited.status_code == 429
    assert limited.json()["detail"].startswith("Слишком много запросов")

    # Another Telegram user behind the same proxy address keeps its own budget.
    assert client.get("/api/v1/listings", headers=other_headers).status_code == 422


def test_rate_limited_response_keeps_cors_headers() -> None:
    from api.main import create_app

    app = create_app()
    allowed = int(DEFAULT_RATE_LIMIT.split("/")[0])
    headers = {
        "X-Telegram-Init-Data": _signed_init_data(777003),
        "Origin": "https://kufar-analytics.example.com",
    }
    client = TestClient(app)
    for _ in range(allowed):
        client.get("/api/v1/segments", headers=headers)

    limited = client.get("/api/v1/segments", headers=headers)
    assert limited.status_code == 429
    assert (
        limited.headers["access-control-allow-origin"] == "https://kufar-analytics.example.com"
    )
