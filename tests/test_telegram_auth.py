from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from time import time

import pytest

from api.middleware.telegram_auth import TelegramInitData, verify_telegram_init_data

# WARNING: This is a FAKE test token. Never copy this pattern with a real
# bot token — real tokens MUST come from env vars only.
_TEST_BOT_TOKEN = "7123456789:AAFtesttoken"


def _make_init_data(user_id: int, bot_token: str, auth_date: int | None = None) -> str:
    data_dict = {
        "user": f'{{"id":{user_id},"first_name":"Test"}}',
        "auth_date": str(auth_date if auth_date is not None else int(time())),
        "query_id": "AAHtest",
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data_dict.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data_dict["hash"] = signature
    return urllib.parse.urlencode(data_dict)


def test_valid_init_data_returns_parsed_object() -> None:
    init_data = _make_init_data(user_id=123456, bot_token=_TEST_BOT_TOKEN)
    result = verify_telegram_init_data(init_data, _TEST_BOT_TOKEN)
    assert isinstance(result, TelegramInitData)
    assert result.user_id == 123456


def test_tampered_hash_raises_value_error() -> None:
    init_data = _make_init_data(user_id=123456, bot_token=_TEST_BOT_TOKEN)
    tampered = init_data.replace("query_id=AAHtest", "query_id=AAHtampered")
    with pytest.raises(ValueError, match="Invalid Telegram initData signature"):
        verify_telegram_init_data(tampered, _TEST_BOT_TOKEN)


def test_missing_hash_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing hash"):
        verify_telegram_init_data("user=%7B%22id%22%3A1%7D&auth_date=1700000000", _TEST_BOT_TOKEN)


def test_stale_init_data_raises_value_error() -> None:
    now_ts = int(time())
    stale = _make_init_data(
        user_id=123456,
        bot_token=_TEST_BOT_TOKEN,
        auth_date=now_ts - 86_401,
    )
    with pytest.raises(ValueError, match="too old"):
        verify_telegram_init_data(stale, _TEST_BOT_TOKEN, now_ts=now_ts)


def test_future_init_data_raises_value_error() -> None:
    now_ts = int(time())
    future = _make_init_data(
        user_id=123456,
        bot_token=_TEST_BOT_TOKEN,
        auth_date=now_ts + 120,
    )
    with pytest.raises(ValueError, match="in the future"):
        verify_telegram_init_data(future, _TEST_BOT_TOKEN, now_ts=now_ts)


def test_10min_old_init_data_passes_with_1h_max_age() -> None:
    """Telegram Mini App initData is generated once at launch and never
    refreshed.  A 5-minute max-age (300s) causes auth failures after
    the user spends a few minutes in the app.  With the default 1-hour
    window, a 10-minute-old initData should still be valid."""
    now_ts = int(time())
    init_data = _make_init_data(
        user_id=123456,
        bot_token=_TEST_BOT_TOKEN,
        auth_date=now_ts - 600,
    )
    result = verify_telegram_init_data(
        init_data, _TEST_BOT_TOKEN, max_age_seconds=3600, now_ts=now_ts
    )
    assert result.user_id == 123456
