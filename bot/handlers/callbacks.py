"""Callback handlers for inline keyboard buttons on tracker alerts."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from api.models import TrackerEvent, User
from bot.api_client import _api_post
from bot.database import get_bot_session_factory

router = Router(name="callbacks")


async def _resolve_listing_context(
    telegram_user_id: int, ad_id: int
) -> dict | None:
    """Look up the most recent TrackerEvent for this (user, ad_id) pair.

    BE-15: the inline `📌 В покупки` / `👁 Отслеживать` buttons embed only
    the ad_id in callback_data (Telegram caps it at 64 bytes, no room
    for query/title/link). Without enrichment, the bot would POST
    `query=""` and `link=""` to the API — both rejected by
    `LeadCreate`/`WatchlistCreate` validators with 422, leaving the user
    with a silent "Ошибка. Попробуйте позже." that they can't recover
    from. The scheduler already persists the alert payload as a
    `TrackerEvent` row for the audit log; re-using that row gives us
    the real query, title, link, price and thumbnail without needing
    to re-fetch from Kufar or stuff the data through callback_data.
    """
    session_factory = get_bot_session_factory()
    async with session_factory() as session:
        # BE-15: secondary sort by id to break ties — Postgres'
        # ``func.now()`` and SQLite's ``CURRENT_TIMESTAMP`` truncate to
        # microsecond / second precision respectively, and two alerts
        # for the same ad can land in the same created_at bucket
        # (e.g. a ``new_listing`` and ``price_drop`` queued back to
        # back). Without the id tie-breaker the user could get the
        # older row and a stale price.
        result = await session.execute(
            select(TrackerEvent)
            .join(User, User.id == TrackerEvent.user_id)
            .where(
                User.telegram_user_id == telegram_user_id,
                TrackerEvent.ad_id == ad_id,
            )
            .order_by(TrackerEvent.created_at.desc(), TrackerEvent.id.desc())
            .limit(1)
        )
        event = result.scalar_one_or_none()
        if event is None:
            return None
        return {
            "query": event.query,
            "title": event.title,
            "link": event.link,
            "price_byn": float(event.price_byn) if event.price_byn is not None else None,
            "thumbnail": event.thumbnail,
        }


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
    ctx = await _resolve_listing_context(telegram_user_id, ad_id)
    if ctx is None:
        # BE-15: TrackerEvent rows expire after the retention window
        # (see DEEP_DIVE / DB-H3 cleanup). If the user taps the button
        # weeks later, we can't reconstruct the listing — fall back to
        # asking them to open the app instead of writing a half-empty
        # row that the API would reject anyway.
        await callback.answer(
            "Не удалось найти данные объявления. Откройте мини-апп.",
            show_alert=True,
        )
        return

    payload = {
        "ad_id": ad_id,
        "query": ctx["query"],
        "title": ctx["title"],
        "link": ctx["link"],
        "price_byn": ctx["price_byn"],
        "thumbnail": ctx["thumbnail"],
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
    ctx = await _resolve_listing_context(telegram_user_id, ad_id)
    if ctx is None:
        # BE-15: same fallback as cb_add_to_leads — the watchlist API
        # also requires title/link, and the scheduler is the only
        # source of truth for the original alert payload.
        await callback.answer(
            "Не удалось найти данные объявления. Откройте мини-апп.",
            show_alert=True,
        )
        return

    payload = {
        "ad_id": ad_id,
        "query": ctx["query"],
        "title": ctx["title"],
        "link": ctx["link"],
        "price_byn": ctx["price_byn"],
        "thumbnail": ctx["thumbnail"],
    }
    result = await _api_post("/api/v1/watchlist", telegram_user_id, json_body=payload)

    if result is None:
        await callback.answer("⚠️ Ошибка. Попробуйте позже.", show_alert=True)
    elif result.get("conflict"):
        await callback.answer("Уже в покупках", show_alert=False)
    else:
        await callback.answer("⭐ Добавлено в избранное", show_alert=False)
