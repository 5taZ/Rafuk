"""Required Telegram channel subscription, shared by the API and the bot.

Rafuk is open only to subscribers of ``settings.required_channel``. The bot
asks for the subscription on /start; the API refuses the Mini App with 403
``subscription_required`` because the chat menu button opens the Mini App
without going through the bot.

Telegram answers ``getChatMember`` for a channel only when the bot is an
administrator there. When Telegram cannot answer (outage, bot not an admin,
wrong channel) the gate fails open and logs an error: a misconfiguration
must not lock every user out of the app.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException, status

from api.services.account_status import is_admin_user

logger = logging.getLogger(__name__)

_MEMBER_STATUSES = frozenset({"creator", "administrator", "member"})
# Only positive answers are cached: someone who has just subscribed must get
# in on the next request. Unsubscribing takes effect within this window, so
# it only covers the burst of requests a Mini App launch makes — at 10 min
# a user who left the channel kept using the app.
_MEMBER_CACHE_TTL_SECONDS = 60
_TELEGRAM_TIMEOUT_SECONDS = 5.0


def required_channel(settings: Any) -> str:
    """Channel username without ``@`` / ``t.me/``; empty when the gate is off."""
    raw = str(getattr(settings, "required_channel", "") or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
    return raw.strip("/")


def channel_url(channel: str) -> str:
    return f"https://t.me/{channel}"


def is_member_status(member_status: str | None, is_member: bool | None = None) -> bool:
    # A "restricted" subscriber is still in the channel when is_member is set.
    return member_status in _MEMBER_STATUSES or (member_status == "restricted" and bool(is_member))


async def fetch_channel_membership(bot_token: str, channel: str, user_id: int) -> bool | None:
    """Ask Telegram; ``None`` when it cannot tell either way."""
    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{bot_token}/getChatMember",
                params={"chat_id": f"@{channel}", "user_id": int(user_id)},
            )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # The exception text may carry the request URL, i.e. the bot token.
        logger.warning("Channel subscription check unavailable: %s", type(exc).__name__)
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("ok"):
        result = payload.get("result") or {}
        return is_member_status(result.get("status"), result.get("is_member"))
    description = str(payload.get("description") or "")
    lowered = description.lower()
    if "user not found" in lowered or "participant_id_invalid" in lowered:
        return False
    logger.error(
        "Channel subscription check failed for @%s: %s — is the bot an admin of the channel?",
        channel, description or payload.get("error_code"),
    )
    return None


async def is_channel_subscriber(
    cache: Any, *, bot_token: str, channel: str, user_id: int, use_cache: bool = True,
) -> bool:
    key = f"chansub:{channel.lower()}:{int(user_id)}"
    if use_cache and cache is not None:
        try:
            if await cache.get(key) == "1":
                return True
        except Exception:  # noqa: BLE001 — a Redis blip falls back to Telegram
            logger.debug("Subscription cache read failed", exc_info=True)
    member = await fetch_channel_membership(bot_token, channel, user_id)
    if member is None:
        return True
    if member and cache is not None:
        try:
            await cache.set(key, "1", ttl=_MEMBER_CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.debug("Subscription cache write failed", exc_info=True)
    return member


def subscription_required_error(channel: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "subscription_required",
            "channel": f"@{channel}",
            "channel_url": channel_url(channel),
            "message": f"Подпишитесь на канал @{channel}, чтобы пользоваться Rafuk.",
        },
    )


async def require_channel_subscription(cache: Any, settings: Any, user_id: int) -> None:
    channel = required_channel(settings)
    if not channel or is_admin_user(user_id, settings):
        return
    if await is_channel_subscriber(
        cache, bot_token=settings.bot_token.get_secret_value(), channel=channel, user_id=user_id,
    ):
        return
    raise subscription_required_error(channel)
