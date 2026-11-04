from __future__ import annotations

from aiogram import Bot

# Pending outbox rows can still carry the old three-line alert format.
_LEGACY_HEADINGS = {
    "🔔 Новый лот": "",
    "📉 Цена снизилась": "Цена снизилась",
    "🎯 Порог цены": "Порог цены",
    "📉 Скидка от медианы": "Скидка от медианы",
    "📈 Цена снова растёт": "Цена снова растёт",
}


async def send_notification(
    bot: Bot,
    telegram_user_id: int,
    message: str,
    *,
    reply_markup: object | None,
) -> None:
    heading, _, body = message.partition("\n")
    if "\n" in body and heading in _LEGACY_HEADINGS:
        label = _LEGACY_HEADINGS[heading]
        message = f"{label}\n{body}" if label else body
    await bot.send_message(telegram_user_id, message, reply_markup=reply_markup)
