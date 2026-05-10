"""Shared HTTP client for bot-to-API communication."""
from __future__ import annotations

import logging
import httpx
from api.config import get_settings
from bot.auth import build_init_data_header

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _get_http_client(base_url: str) -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _api_get(path: str, telegram_user_id: int, *, params: dict | None = None) -> dict | None:
    """Make an authenticated GET request to the internal API."""
    settings = get_settings()
    bot_token = settings.bot_token.get_secret_value()
    init_data = build_init_data_header(telegram_user_id, bot_token)
    headers = {"X-Telegram-Init-Data": init_data}
    base_url = settings.api_base_url
    client = _get_http_client(base_url)
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
) -> dict | None:
    """Make an authenticated POST request to the internal API."""
    settings = get_settings()
    bot_token = settings.bot_token.get_secret_value()
    init_data = build_init_data_header(telegram_user_id, bot_token)
    headers = {"X-Telegram-Init-Data": init_data}
    base_url = settings.api_base_url
    client = _get_http_client(base_url)
    try:
        resp = await client.post(path, json=json_body, headers=headers)
        if resp.status_code == 409:
            return {"conflict": True}
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.exception("API call failed: POST %s", path)
        return None
