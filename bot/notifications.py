from __future__ import annotations

from html import escape

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

# Pending outbox rows can still carry the old three-line alert format.
_ALERT_HEADINGS = {
    "Новое объявление": "Новое объявление",
    "Цена снизилась": "Цена снизилась",
    "Порог цены": "Порог цены",
    "Скидка от медианы": "Скидка от медианы",
    "Цена снова растёт": "Цена снова растёт",
    "🔔 Новый лот": "Новое объявление",
    "📉 Цена снизилась": "Цена снизилась",
    "🎯 Порог цены": "Порог цены",
    "📉 Скидка от медианы": "Скидка от медианы",
    "📈 Цена снова растёт": "Цена снова растёт",
}


def _format_alert(message: str, reply_markup: object | None) -> str | None:
    lines = message.splitlines()
    if len(lines) == 2 and isinstance(reply_markup, InlineKeyboardMarkup) and any(
        (button.callback_data or "").startswith("add_lead:")
        for row in reply_markup.inline_keyboard for button in row
    ):
        lines.insert(0, "Новое объявление")
    if len(lines) < 3 or lines[0] not in _ALERT_HEADINGS:
        return None
    heading, title, price, *details = lines
    price, _, old_detail = price.partition(" · ")
    if old_detail:
        details.insert(0, old_detail)
    formatted = [escape(_ALERT_HEADINGS[heading]), f"<b>{escape(title)}</b>", "",
                 f"<b>{escape(price)}</b>"]
    for detail in details:
        if detail.startswith("Было "):
            old_price, separator, change = detail[5:].partition(" · ")
            formatted.append(f"Было <s>{escape(old_price)}</s>{separator}{escape(change)}")
        else:
            formatted.append(escape(detail))
    return "\n".join(formatted)


async def send_notification(
    bot: Bot,
    telegram_user_id: int,
    message: str,
    *,
    reply_markup: object | None,
) -> None:
    formatted = _format_alert(message, reply_markup)
    if formatted is None:
        await bot.send_message(telegram_user_id, message, reply_markup=reply_markup)
        return
    await bot.send_message(
        telegram_user_id, formatted, reply_markup=reply_markup, parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
