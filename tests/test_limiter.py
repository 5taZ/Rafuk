from __future__ import annotations

from starlette.requests import Request

from api.limiter import _rate_limit_key
from api.middleware.telegram_auth import TelegramInitData


def _make_request(*, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers or [],
        "client": ("203.0.113.10", 50000),
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
