"""Shared HTTP client for bot-to-API communication.

A single httpx.AsyncClient is reused across the entire bot process so
that TCP+TLS handshakes are amortised across requests and connection
pools are bounded.

Concurrency model
-----------------
``get_http_client`` is async and holds ``_client_lock`` while inspecting
or replacing ``_client``. Without the lock two callbacks fired by the
event loop in the same tick could both observe ``_client is None`` and
race into creating *two* httpx.AsyncClient instances — duplicate
connection pools that would never be closed (BE-H11). The lock is held
only for the tiny check-and-create section.

Lifecycle (BE-H12)
------------------
``close_http_client`` MUST be called from bot shutdown so the pool's
keep-alive sockets release cleanly. The previous handler-local clients
in ``bot/handlers/analytics.py`` and ``bot/handlers/callbacks.py`` had
no close path; they're now redirected to this module.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from api.config import get_settings
from bot.auth import build_init_data_header

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_warned_about_legacy_initdata_path = False
# Lazy-init so the lock binds to whichever event loop the bot actually
# runs in (avoids RuntimeError("got Future ... attached to a different
# loop") in tests that spin up multiple loops).
_client_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_http_client(base_url: str | None = None) -> httpx.AsyncClient:
    """Return the process-wide httpx.AsyncClient, creating it if needed.

    The ``base_url`` argument is honoured only on first creation —
    callers that need a different host should not share this client.
    """
    global _client
    if _client is not None and not _client.is_closed:
        return _client
    async with _get_lock():
        # Double-check after acquiring the lock; another coroutine may
        # have created the client while we were waiting.
        if _client is not None and not _client.is_closed:
            return _client
        resolved_base = base_url or get_settings().api_base_url
        # `limits` keeps the pool bounded — bot is mostly idle but a
        # callback storm could otherwise open hundreds of sockets.
        _client = httpx.AsyncClient(
            base_url=resolved_base,
            timeout=30.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        return _client


async def close_http_client() -> None:
    """Close the shared client; safe to call multiple times."""
    global _client
    async with _get_lock():
        if _client is not None and not _client.is_closed:
            try:
                await _client.aclose()
            except Exception:  # noqa: BLE001 — shutdown path
                logger.warning("Failed to close shared httpx client", exc_info=True)
        _client = None


def build_api_headers(telegram_user_id: int, *, mutating: bool = False) -> dict[str, str]:
    """Build authenticated bot→API headers.

    OPUS-12/Wave 96: prefer the dedicated internal service token for
    every bot→API call. The legacy fallback still forges initData with
    BOT_TOKEN for deployments that have not set INTERNAL_SERVICE_TOKEN
    yet, but it is now centralised here instead of duplicated across
    handlers.
    """
    global _warned_about_legacy_initdata_path
    settings = get_settings()
    if settings.internal_service_token is not None:
        headers = {
            "X-Internal-Service-Token": settings.internal_service_token.get_secret_value(),
            "X-Acting-Telegram-User-Id": str(telegram_user_id),
        }
    else:
        if not _warned_about_legacy_initdata_path:
            logger.warning(
                "INTERNAL_SERVICE_TOKEN not configured — bot is forging initData "
                "with BOT_TOKEN. Set the env var to switch to the dedicated "
                "service-token path."
            )
            _warned_about_legacy_initdata_path = True
        bot_token = settings.bot_token.get_secret_value()
        headers = {"X-Telegram-Init-Data": build_init_data_header(telegram_user_id, bot_token)}

    if mutating:
        headers.update(
            {
                "Origin": settings.api_base_url,
                "X-Requested-With": "XMLHttpRequest",
            }
        )
    return headers


async def _api_get(
    path: str,
    telegram_user_id: int,
    *,
    params: dict | None = None,
) -> dict[str, Any] | None:
    """Make an authenticated GET request to the internal API."""
    settings = get_settings()
    headers = build_api_headers(telegram_user_id)
    client = await get_http_client(settings.api_base_url)
    try:
        resp = await client.get(path, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.exception("API call failed: GET %s", path)
        return None


async def _api_post(
    path: str,
    telegram_user_id: int,
    *,
    json_body: dict | None = None,
) -> dict[str, Any] | None:
    """Make an authenticated POST request to the internal API."""
    settings = get_settings()
    headers = build_api_headers(telegram_user_id, mutating=True)
    client = await get_http_client(settings.api_base_url)
    try:
        resp = await client.post(path, json=json_body, headers=headers)
        if resp.status_code == 409:
            return {"conflict": True}
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.exception("API call failed: POST %s", path)
        return None
