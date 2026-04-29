"""Callback handlers for inline keyboard buttons on tracker alerts."""

from __future__ import annotations

import logging

import httpx
from aiogram import F, Router
from aiogram.types import CallbackQuery

from api.config import get_settings
from bot.auth import build_init_data_header

logger = logging.getLogger(__name__)

router = Router(name="callbacks")

# Shared httpx client — avoids creating a new TCP+TLS connection per API call.
_shared_client: httpx.AsyncClient | None = None


def _get_http_client(base_url: str) -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
    return _shared_client


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


@router.callback_query(F.data.startswith("add_lead:"))
async def cb_add_to_leads(callback: CallbackQuery) -> None:
    """Add a listing to the user's leads (deal pipeline)."""
    ad_id_str = callback.data.split(":", 1)[1]
    try:
        ad_id = int(ad_id_str)
    except (ValueError, IndexError):
        await callback.answer("Неверный ID объявления", show_alert=True)
        return

    telegram_user_id = callback.from_user.id
    payload = {
        "ad_id": ad_id,
        "query": "",
        "title": "Лот из уведомления",
        "link": "",
        "status": "new",
        "source": "bot_callback",
    }
    result = await _api_post("/api/v1/leads", telegram_user_id, json_body=payload)

    if result is None:
        await callback.answer("⚠️ Ошибка. Попробуйте позже.", show_alert=True)
    elif result.get("conflict"):
        await callback.answer("Уже в покупках", show_alert=False)
    else:
        await callback.answer("📌 Добавлено в покупки", show_alert=False)


@router.callback_query(F.data.startswith("add_watch:"))
async def cb_add_to_watchlist(callback: CallbackQuery) -> None:
    """Add a listing to the user's watchlist."""
    ad_id_str = callback.data.split(":", 1)[1]
    try:
        ad_id = int(ad_id_str)
    except (ValueError, IndexError):
        await callback.answer("Неверный ID объявления", show_alert=True)
        return

    telegram_user_id = callback.from_user.id
    payload = {
        "ad_id": ad_id,
        "query": "",
        "title": "Лот из уведомления",
        "link": "",
    }
    result = await _api_post("/api/v1/watchlist", telegram_user_id, json_body=payload)

    if result is None:
        await callback.answer("⚠️ Ошибка. Попробуйте позже.", show_alert=True)
    elif result.get("conflict"):
        await callback.answer("Уже в покупках", show_alert=False)
    else:
        await callback.answer("👁 Добавлено в отслеживание", show_alert=False)
