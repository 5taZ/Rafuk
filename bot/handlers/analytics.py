"""Bot commands for analytics: /deals, /profit, /stats."""

from __future__ import annotations

import logging
from html import escape as html_escape

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from api.config import get_settings
from bot.api_client import get_http_client
from bot.auth import build_init_data_header

logger = logging.getLogger(__name__)

router = Router(name="analytics")


def _fmt_byn(value: float | None) -> str:
    """Format a BYN amount with thousands separator."""
    if value is None:
        return "—"
    return f"{value:,.0f}"


async def _api_get(path: str, telegram_user_id: int, *, params: dict | None = None) -> dict | None:
    """Make an authenticated GET request to the internal API."""
    settings = get_settings()
    bot_token = settings.bot_token.get_secret_value()
    init_data = build_init_data_header(telegram_user_id, bot_token)
    headers = {"X-Telegram-Init-Data": init_data}
    base_url = settings.api_base_url

    # Reuse the process-wide locked client so we don't create a second
    # connection pool here — the previous handler-local _shared_client
    # was a duplicate of the one in bot/api_client.py and never closed
    # (BE-H11/H12).
    client = await get_http_client(base_url)
    try:
        resp = await client.get(path, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.exception("API call failed: GET %s", path)
        return None


@router.message(Command("deals"))
async def cmd_deals(message: Message) -> None:
    """Active deals summary."""
    data = await _api_get("/api/v1/analytics/leads", message.from_user.id, params={"days": 30})
    if data is None:
        await message.answer("⚠️ Не удалось загрузить аналитику. Попробуйте позже.")
        return

    funnel = data.get("funnel", [])
    status_map = {stage["status"]: stage["count"] for stage in funnel}
    in_progress = status_map.get("in_progress", 0)
    bought = status_map.get("bought", 0)
    sold = status_map.get("sold", 0)
    researching = status_map.get("researching", 0)
    new = status_map.get("new", 0)

    cost = data.get("total_cost_byn", 0)
    profit = data.get("total_profit_byn", 0)
    roi = data.get("average_roi_percent", 0)

    profit_sign = "+" if profit >= 0 else ""
    roi_str = f" ({roi:.0f}%)" if cost > 0 else ""

    text = (
        f"📊 <b>Активные сделки:</b>\n"
        f"В работе — {in_progress + researching} | Куплено — {bought} | "
        f"На продаже — {sold}\n"
        f"Новые — {new}\n"
        f"Вложено: {_fmt_byn(cost)} BYN | Прибыль: {profit_sign}{_fmt_byn(profit)} BYN{roi_str}"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("profit"))
async def cmd_profit(message: Message) -> None:
    """Profit for the month."""
    data = await _api_get("/api/v1/analytics/leads", message.from_user.id, params={"days": 30})
    if data is None:
        await message.answer("⚠️ Не удалось загрузить аналитику. Попробуйте позже.")
        return

    cost = data.get("total_cost_byn", 0)
    revenue = data.get("total_revenue_byn", 0)
    profit = data.get("total_profit_byn", 0)
    roi = data.get("average_roi_percent", 0)
    win_rate = data.get("win_rate_percent", 0)
    pursued = data.get("pursued_leads", 0)
    sold = data.get("sold_leads", 0)
    total = data.get("total_leads", 0)

    profit_sign = "+" if profit >= 0 else ""

    text = (
        f"📊 <b>За 30 дней:</b>\n"
        f"Вложено: {_fmt_byn(cost)} BYN │ Выручка: {_fmt_byn(revenue)} BYN\n"
        f"Прибыль: {profit_sign}{_fmt_byn(profit)} BYN (ROI: {roi:.0f}%)\n"
        f"Win rate: {win_rate:.0f}% | Сделок: {sold}/{pursued} (всего {total})"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Market statistics for a query."""
    query_text = message.text or ""
    # Remove the /stats command and leading whitespace
    parts = query_text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Использование: <code>/stats запрос</code>\n"
            "Пример: /stats iphone 15",
            parse_mode="HTML",
        )
        return

    search_query = parts[1].strip()
    data = await _api_get(
        "/api/v1/price-stats",
        message.from_user.id,
        params={"query": search_query, "currency": "BYN"},
    )
    if data is None:
        await message.answer("⚠️ Не удалось загрузить статистику. Попробуйте позже.")
        return

    median = data.get("median", 0)
    total = data.get("total_results", 0)
    count = data.get("count", 0)
    q1 = data.get("q1", 0)
    q3 = data.get("q3", 0)

    text = (
        f"📱 <b>{html_escape(search_query)}</b>: "
        f"Медиана {_fmt_byn(median)} BYN │ На рынке {total} │ "
        f"Дешевле {_fmt_byn(q1)} BYN\n"
        f"Диапазон: {_fmt_byn(q1)} – {_fmt_byn(q3)} BYN | Выборка: {count}"
    )
    await message.answer(text, parse_mode="HTML")
