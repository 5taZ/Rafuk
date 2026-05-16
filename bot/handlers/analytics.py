"""Bot commands for analytics: /deals, /profit, /stats."""

from __future__ import annotations

from html import escape as html_escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.api_client import _api_get

router = Router(name="analytics")

# PR-16: cap user-supplied query text echoed back into the /stats
# reply. Telegram message bodies have a 4096-char ceiling and
# the boilerplate (median + range + counts + label fragments) eats
# ~150 chars, so 120 chars of free text is plenty for a normal
# /stats invocation. Anything past the cap is truncated with a
# horizontal-ellipsis marker so the user can tell their query was
# clipped without the message-send failing.
_STATS_QUERY_DISPLAY_LIMIT = 120


def _truncate_for_telegram(value: str, max_chars: int = _STATS_QUERY_DISPLAY_LIMIT) -> str:
    if len(value) <= max_chars:
        return value
    # ``…`` is one char, so ``max_chars - 1`` of the prefix survives.
    return value[: max_chars - 1] + "…"


def _fmt_byn(value: float | None) -> str:
    """Format a BYN amount with thousands separator."""
    if value is None:
        return "—"
    return f"{value:,.0f}"


@router.message(Command("deals"))
async def cmd_deals(message: Message) -> None:
    """Active deals summary."""
    data = await _api_get("/api/v1/analytics/leads", message.from_user.id)
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
    """Profit and ROI summary."""
    data = await _api_get("/api/v1/analytics/leads", message.from_user.id)
    if data is None:
        await message.answer("⚠️ Не удалось загрузить аналитику. Попробуйте позже.")
        return

    cost = data.get("total_cost_byn", 0)
    revenue = data.get("total_revenue_byn", 0)
    profit = data.get("total_profit_byn", 0)
    roi = data.get("average_roi_percent", 0)
    sold = data.get("sold_leads", 0)

    profit_sign = "+" if profit >= 0 else ""

    text = (
        f"📊 <b>Финансы:</b>\n"
        f"Вложено: {_fmt_byn(cost)} BYN │ Выручка: {_fmt_byn(revenue)} BYN\n"
        f"Прибыль: {profit_sign}{_fmt_byn(profit)} BYN │ ROI: {roi:.0f}%\n"
        f"Продаж: {sold}"
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

    # PR-16: cap the echoed query text. The unbounded version
    # would exceed Telegram's 4096-char message ceiling on a long
    # /stats payload and the send would fail with a noisy stack
    # trace (BadRequest: MESSAGE_TOO_LONG). The truncation marker
    # lets the user see their query was clipped.
    display_query = _truncate_for_telegram(search_query)
    text = (
        f"📱 <b>{html_escape(display_query)}</b>: "
        f"Медиана {_fmt_byn(median)} BYN │ На рынке {total} │ "
        f"Дешевле {_fmt_byn(q1)} BYN\n"
        f"Диапазон: {_fmt_byn(q1)} – {_fmt_byn(q3)} BYN | Выборка: {count}"
    )
    await message.answer(text, parse_mode="HTML")
