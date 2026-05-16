from __future__ import annotations

from urllib.parse import quote_plus

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def mini_app_keyboard(url: str) -> InlineKeyboardMarkup | None:
    if not url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть мини-апп",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def tracker_alert_keyboard(
    mini_app_url: str,
    *,
    query: str,
    listing_url: str | None,
) -> InlineKeyboardMarkup | None:
    """Build the inline keyboard attached to tracker alert messages.

    Two link-buttons only — "Open mini-app for this query" and the raw
    Kufar listing. The previous ``В работу`` / ``Позже`` callback
    buttons were removed: nobody uses the inline workflow (everyone
    goes through the mini app), and they wrote leads with surprising
    statuses (`lead:` → researching, `later:` → new) that didn't match
    the button labels.
    """
    query_url = f"{mini_app_url}?query={quote_plus(query)}&view=trackers"
    row: list[InlineKeyboardButton] = []
    if mini_app_url.startswith("https://"):
        row.append(
            InlineKeyboardButton(
                text="Открыть запрос",
                web_app=WebAppInfo(url=query_url),
            )
        )
    # Telegram requires a non-empty https URL — skip the "Open listing"
    # button when we don't have one rather than crashing the alert.
    if listing_url and listing_url.startswith(("http://", "https://")):
        row.append(InlineKeyboardButton(text="Открыть лот", url=listing_url))
    if not row:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[row])


def lead_reminder_keyboard(
    mini_app_url: str,
) -> InlineKeyboardMarkup | None:
    """Build the inline keyboard for lead reminder messages.

    Single WebApp button opening the deals view. The scheduler calls
    this instead of constructing aiogram types directly — keeps the
    architectural layering clean (scheduler → bot/keyboards, not
    scheduler → aiogram.types).
    """
    if not mini_app_url or not mini_app_url.startswith("https://"):
        return None
    deal_url = f"{mini_app_url}?view=deals"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📌 Открыть сделку",
                    web_app=WebAppInfo(url=deal_url),
                )
            ]
        ]
    )


def enhanced_alert_keyboard(
    *,
    ad_id: int,
    listing_url: str | None,
) -> InlineKeyboardMarkup:
    """Build an enriched inline keyboard for new listing / price drop alerts.

    Three action buttons:
      - 📌 В покупки  — callback ``add_lead:<ad_id>`` to create a lead
      - ⭐ В Избранное — callback ``add_watch:<ad_id>`` to add to watchlist
      - 🔗 Открыть    — URL button linking to the Kufar listing
    """
    row1: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="📌 В покупки", callback_data=f"add_lead:{ad_id}"),
        InlineKeyboardButton(text="⭐ В Избранное", callback_data=f"add_watch:{ad_id}"),
    ]
    row2: list[InlineKeyboardButton] = []
    if listing_url and listing_url.startswith(("http://", "https://")):
        row2.append(InlineKeyboardButton(text="🔗 Открыть", url=listing_url))
    keyboard = [row1]
    if row2:
        keyboard.append(row2)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
