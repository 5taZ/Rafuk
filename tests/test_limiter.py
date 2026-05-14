from __future__ import annotations

import redis
from redis.exceptions import AuthenticationError
from starlette.requests import Request

from api.limiter import _rate_limit_key, _redis_reachable
from api.middleware.telegram_auth import TelegramInitData


def _make_request(
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    client_host: str = "203.0.113.10",
) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers or [],
        "client": (client_host, 50000),
    }
    return Request(scope)


def test_rate_limit_key_uses_verified_telegram_user_from_request_state() -> None:
    request = _make_request()
    request.state.telegram_user = TelegramInitData(user_id=42, first_name="T", raw={})
    assert _rate_limit_key(request) == "tg:42"


def test_rate_limit_key_ignores_unverified_telegram_header_and_falls_back_to_ip() -> None:
    request = _make_request(
        headers=[
            (
                b"x-telegram-init-data",
                b"user=%7B%22id%22%3A999999%7D&auth_date=1700000000&hash=fake",
            )
        ]
    )
    assert _rate_limit_key(request) == "203.0.113.10"


def test_rate_limit_key_ignores_spoofed_cf_header_from_direct_client() -> None:
    request = _make_request(
        headers=[(b"cf-connecting-ip", b"198.51.100.77")],
        client_host="8.8.8.8",
    )
    assert _rate_limit_key(request) == "8.8.8.8"


def test_rate_limit_key_uses_cf_header_from_trusted_proxy() -> None:
    request = _make_request(
        headers=[(b"cf-connecting-ip", b"198.51.100.77")],
        client_host="173.245.48.1",
    )
    assert _rate_limit_key(request) == "198.51.100.77"


def test_redis_reachable_requires_successful_ping(monkeypatch) -> None:
    calls = []

    class FakeRedis:
        def ping(self) -> bool:
            calls.append("ping")
            return True

        def close(self) -> None:
            calls.append("close")

    def fake_from_url(url: str, **kwargs):
        calls.append((url, kwargs))
        return FakeRedis()

    _redis_reachable.cache_clear()
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))

    assert _redis_reachable("redis://:secret@redis:6379/0") is True
    assert calls[0][0] == "redis://:secret@redis:6379/0"
    assert calls[1:] == ["ping", "close"]


def test_redis_reachable_rejects_auth_failure_after_tcp_success(monkeypatch) -> None:
    calls = []

    class FakeRedis:
        def ping(self) -> bool:
            calls.append("ping")
            raise AuthenticationError("invalid username-password pair")

        def close(self) -> None:
            calls.append("close")

    _redis_reachable.cache_clear()
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *_a, **_k: FakeRedis()))

    assert _redis_reachable("redis://:wrong@redis:6379/0") is False
    assert calls == ["ping", "close"]
