from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse

import pytest
from fastapi import HTTPException

from api.middleware.telegram_auth import (
    DEFAULT_INIT_DATA_MAX_AGE_SECONDS,
    TelegramInitData,
    verify_telegram_init_data,
)

BOT_TOKEN = "7123456789:AAFtesttoken"
NOW = 1_800_000_000


def _make_init_data(
    user_id: int,
    bot_token: str,
    *,
    auth_date: int | None = NOW,
    username: str | None = None,
) -> str:
    user = f'{{"id":{user_id},"first_name":"Test"'
    if username:
        user += f',"username":"{username}"'
    data_dict = {"user": user + "}", "query_id": "AAHtest"}
    if auth_date is not None:
        data_dict["auth_date"] = str(auth_date)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data_dict.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data_dict["hash"] = signature
    return urllib.parse.urlencode(data_dict)


def test_valid_init_data_returns_parsed_object() -> None:
    init_data = _make_init_data(user_id=123456, bot_token=BOT_TOKEN, username="reseller")
    result = verify_telegram_init_data(init_data, BOT_TOKEN, now=NOW + 60)
    assert isinstance(result, TelegramInitData)
    assert result.user_id == 123456
    assert result.first_name == "Test"
    assert result.username == "reseller"
    assert result.auth_date == NOW


def test_fresh_init_data_uses_current_time_by_default() -> None:
    init_data = _make_init_data(123, BOT_TOKEN, auth_date=int(time.time()) - 10)
    assert verify_telegram_init_data(init_data, BOT_TOKEN).user_id == 123


def test_tampered_hash_raises_value_error() -> None:
    init_data = _make_init_data(user_id=123456, bot_token=BOT_TOKEN)
    tampered = init_data.replace(f"auth_date={NOW}", "auth_date=9999999999")
    with pytest.raises(ValueError, match="Invalid Telegram initData signature"):
        verify_telegram_init_data(tampered, BOT_TOKEN, now=NOW)


def test_missing_hash_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing hash"):
        verify_telegram_init_data("user=%7B%22id%22%3A1%7D&auth_date=1700000000", BOT_TOKEN)


def test_missing_auth_date_is_rejected() -> None:
    init_data = _make_init_data(123456, BOT_TOKEN, auth_date=None)
    with pytest.raises(ValueError, match="missing auth_date"):
        verify_telegram_init_data(init_data, BOT_TOKEN, now=NOW)


def test_expired_init_data_is_rejected() -> None:
    init_data = _make_init_data(123456, BOT_TOKEN, auth_date=NOW)
    now = NOW + DEFAULT_INIT_DATA_MAX_AGE_SECONDS + 1
    with pytest.raises(ValueError, match="expired"):
        verify_telegram_init_data(init_data, BOT_TOKEN, now=now)


def test_init_data_at_max_age_is_accepted() -> None:
    init_data = _make_init_data(123456, BOT_TOKEN, auth_date=NOW)
    result = verify_telegram_init_data(init_data, BOT_TOKEN, max_age_seconds=3600, now=NOW + 3600)
    assert result.user_id == 123456


def test_zero_max_age_disables_age_check() -> None:
    init_data = _make_init_data(123456, BOT_TOKEN, auth_date=1)
    result = verify_telegram_init_data(init_data, BOT_TOKEN, max_age_seconds=0, now=NOW)
    assert result.user_id == 123456


def test_future_auth_date_is_rejected() -> None:
    init_data = _make_init_data(123456, BOT_TOKEN, auth_date=NOW + 61)
    with pytest.raises(ValueError, match="future"):
        verify_telegram_init_data(init_data, BOT_TOKEN, now=NOW)


def test_small_clock_skew_is_tolerated() -> None:
    init_data = _make_init_data(123456, BOT_TOKEN, auth_date=NOW + 30)
    assert verify_telegram_init_data(init_data, BOT_TOKEN, now=NOW).user_id == 123456


def test_non_numeric_auth_date_is_rejected() -> None:
    data_dict = {"user": '{"id":1,"first_name":"Test"}', "auth_date": "yesterday"}
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data_dict.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data_dict["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    with pytest.raises(ValueError, match="not an integer"):
        verify_telegram_init_data(urllib.parse.urlencode(data_dict), BOT_TOKEN, now=NOW)


def test_dependency_rejects_expired_init_data_with_russian_detail() -> None:
    from api.dependencies import get_telegram_user

    expired = _make_init_data(
        123456, BOT_TOKEN, auth_date=int(time.time()) - DEFAULT_INIT_DATA_MAX_AGE_SECONDS - 10
    )
    with pytest.raises(HTTPException) as exc_info:
        get_telegram_user(expired)
    assert exc_info.value.status_code == 401
    assert "Telegram" in exc_info.value.detail
    assert "expired" not in exc_info.value.detail


def test_dependency_honours_max_age_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.config import get_settings
    from api.dependencies import get_telegram_user

    monkeypatch.setenv("INIT_DATA_MAX_AGE_SECONDS", "0")
    get_settings.cache_clear()
    try:
        ancient = _make_init_data(123456, BOT_TOKEN, auth_date=1)
        assert get_telegram_user(ancient).user_id == 123456
    finally:
        get_settings.cache_clear()


def test_dependency_requires_header() -> None:
    from api.dependencies import get_telegram_user

    with pytest.raises(HTTPException) as exc_info:
        get_telegram_user(None)
    assert exc_info.value.status_code == 401
