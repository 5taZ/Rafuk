from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.parse
from dataclasses import dataclass
from time import time

# TG-09: auth_date comes from Telegram's clock; a host clock lagging by a
# few seconds used to 401 every request of a freshly opened Mini App.
FUTURE_AUTH_DATE_SKEW_SECONDS = 60
_HASH_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(slots=True)
class TelegramInitData:
    user_id: int
    first_name: str
    raw: dict[str, str]
    # SEC-07: set for the bot's X-Internal-Service-Token principal, which
    # may act for any user but must never inherit that user's admin role.
    via_service_token: bool = False


def verify_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 300,
    now_ts: int | None = None,
) -> TelegramInitData:
    if not init_data:
        raise ValueError("initData is empty")

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("initData missing hash")
    # SEC-11 / TG-10: compare_digest raises TypeError (→ 500) on non-ASCII str.
    if not _HASH_RE.fullmatch(received_hash):
        raise ValueError("initData hash malformed")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Invalid Telegram initData signature")

    auth_date_raw = parsed.get("auth_date")
    try:
        auth_date = int(auth_date_raw or "0")
    except (TypeError, ValueError) as exc:
        raise ValueError("initData auth_date is invalid") from exc

    now = int(now_ts if now_ts is not None else time())
    if auth_date <= 0:
        raise ValueError("initData auth_date is missing")
    if auth_date > now + FUTURE_AUTH_DATE_SKEW_SECONDS:
        raise ValueError("initData auth_date is in the future")
    if max_age_seconds > 0 and now - auth_date > max_age_seconds:
        raise ValueError("initData is too old")

    try:
        user_data = json.loads(parsed.get("user", "{}"))
        return TelegramInitData(
            user_id=int(user_data["id"]),
            first_name=str(user_data.get("first_name", "")),
            raw=parsed,
        )
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to parse initData user: {exc}") from exc
