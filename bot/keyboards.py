from __future__ import annotations

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
