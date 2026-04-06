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
                    text="Open Analytics",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def tracker_alert_keyboard(
    mini_app_url: str,
    *,
    query: str,
    listing_url: str,
    event_id: int,
) -> InlineKeyboardMarkup:
    query_url = f"{mini_app_url}?query={quote_plus(query)}&view=trackers"
    first_row = []
    if mini_app_url.startswith("https://"):
        first_row.append(
            InlineKeyboardButton(
                text="Открыть запрос",
                web_app=WebAppInfo(url=query_url),
            )
        )
    first_row.append(InlineKeyboardButton(text="Открыть лот", url=listing_url))
    second_row = [
        InlineKeyboardButton(text="В работу", callback_data=f"lead:{event_id}"),
        InlineKeyboardButton(text="Позже", callback_data=f"later:{event_id}"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[first_row, second_row])
