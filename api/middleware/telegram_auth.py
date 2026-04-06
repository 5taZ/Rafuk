from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass


@dataclass(slots=True)
class TelegramInitData:
    user_id: int
    first_name: str
    raw: dict[str, str]


def verify_telegram_init_data(init_data: str, bot_token: str) -> TelegramInitData:
    if not init_data:
        raise ValueError("initData is empty")

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("initData missing hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Invalid Telegram initData signature")

    auth_date_raw = parsed.get("auth_date")
    if not auth_date_raw:
        raise ValueError("initData missing auth_date")
    if time.time() - int(auth_date_raw) > 300:
        raise ValueError("Telegram initData expired")

    try:
        user_data = json.loads(parsed.get("user", "{}"))
        return TelegramInitData(
            user_id=int(user_data["id"]),
            first_name=str(user_data.get("first_name", "")),
            raw=parsed,
        )
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to parse initData user: {exc}") from exc
