from __future__ import annotations

import hashlib
import hmac
import urllib.parse

import pytest

from api.middleware.telegram_auth import TelegramInitData, verify_telegram_init_data

BOT_TOKEN = "7123456789:AAFtesttoken"


def _make_init_data(user_id: int, bot_token: str) -> str:
    data_dict = {
        "user": f'{{"id":{user_id},"first_name":"Test"}}',
        "auth_date": "1700000000",
        "query_id": "AAHtest",
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data_dict.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data_dict["hash"] = signature
    return urllib.parse.urlencode(data_dict)


def test_valid_init_data_returns_parsed_object() -> None:
    init_data = _make_init_data(user_id=123456, bot_token=BOT_TOKEN)
    result = verify_telegram_init_data(init_data, BOT_TOKEN)
    assert isinstance(result, TelegramInitData)
    assert result.user_id == 123456


def test_tampered_hash_raises_value_error() -> None:
    init_data = _make_init_data(user_id=123456, bot_token=BOT_TOKEN)
    tampered = init_data.replace("auth_date=1700000000", "auth_date=9999999999")
    with pytest.raises(ValueError, match="Invalid Telegram initData signature"):
        verify_telegram_init_data(tampered, BOT_TOKEN)


def test_missing_hash_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing hash"):
        verify_telegram_init_data("user=%7B%22id%22%3A1%7D&auth_date=1700000000", BOT_TOKEN)
