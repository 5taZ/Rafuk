from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass

DEFAULT_INIT_DATA_MAX_AGE_SECONDS = 86400
# Tolerated clock skew between Telegram and this server for auth_date values from the future.
MAX_AUTH_DATE_SKEW_SECONDS = 60


@dataclass(slots=True)
class TelegramInitData:
    user_id: int
    first_name: str
    raw: dict[str, str]
    username: str | None = None
    auth_date: int | None = None


def verify_telegram_signature(init_data: str, bot_token: str) -> dict[str, str]:
    """Check the initData HMAC and return the signed fields (without ``hash``)."""
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
    return parsed


def _check_auth_date(raw_auth_date: str | None, max_age_seconds: int, now: float) -> int:
    if not raw_auth_date:
        raise ValueError("initData missing auth_date")
    try:
        auth_date = int(raw_auth_date)
    except ValueError as exc:
        raise ValueError("initData auth_date is not an integer") from exc

    if auth_date - now > MAX_AUTH_DATE_SKEW_SECONDS:
        raise ValueError("initData auth_date is in the future")
    if max_age_seconds > 0 and now - auth_date > max_age_seconds:
        raise ValueError("initData is expired")
    return auth_date


def verify_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = DEFAULT_INIT_DATA_MAX_AGE_SECONDS,
    now: float | None = None,
) -> TelegramInitData:
    """Validate Telegram Mini App initData and return the authenticated user.

    ``auth_date`` is mandatory: initData older than ``max_age_seconds`` (0 disables the age
    check) or dated more than a minute into the future is rejected.
    """
    parsed = verify_telegram_signature(init_data, bot_token)
    auth_date = _check_auth_date(
        parsed.get("auth_date"),
        max_age_seconds,
        time.time() if now is None else now,
    )

    try:
        user_data = json.loads(parsed.get("user", "{}"))
        username = user_data.get("username")
        return TelegramInitData(
            user_id=int(user_data["id"]),
            first_name=str(user_data.get("first_name", "")),
            raw=parsed,
            username=str(username) if username else None,
            auth_date=auth_date,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Failed to parse initData user: {exc}") from exc
