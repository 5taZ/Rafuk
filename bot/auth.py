"""Shared authentication utilities for bot handlers."""

from __future__ import annotations

import hashlib
import hmac
import json
from time import time
from urllib.parse import urlencode


def build_init_data_header(telegram_user_id: int, bot_token: str) -> str:
    """Construct a valid X-Telegram-Init-Data header for internal API calls.

    The bot shares the same BOT_TOKEN used for HMAC verification, so it
    can produce a cryptographically-valid initData string that the API
    middleware will accept.
    """
    now_ts = int(time())
    user_json = json.dumps({"id": telegram_user_id, "first_name": ""}, separators=(",", ":"))
    params = {
        "user": user_json,
        "auth_date": str(now_ts),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    params["hash"] = computed_hash
    return urlencode(params)
