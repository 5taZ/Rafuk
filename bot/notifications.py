from __future__ import annotations

import logging
from collections.abc import Sequence
from html import escape

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    ReplyParameters,
)

MAX_NOTIFICATION_PHOTOS = 10

logger = logging.getLogger(__name__)

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
    description: str | None = None,
    photo_urls: Sequence[str] = (),
) -> int | None:
    """Send the alert and return the first message ID if it was sent as an album."""
    formatted = _format_alert(message, reply_markup)
    if formatted is None:
        await bot.send_message(telegram_user_id, message, reply_markup=reply_markup)
        return None
    if description:
        formatted += f"\n\n{escape(description)}"

    photos = photo_urls[:MAX_NOTIFICATION_PHOTOS]
    if len(photos) > 1:
        media = [InputMediaPhoto(media=photos[0], caption=formatted, parse_mode=ParseMode.HTML)]
        media.extend(InputMediaPhoto(media=url) for url in photos[1:])
        try:
            album = await bot.send_media_group(telegram_user_id, media)
        except TelegramBadRequest as exc:
            logger.info("Telegram rejected the album. Trying the first photo: %s", exc.message)
        else:
            return album[0].message_id
    if photos:
        try:
            await bot.send_photo(
                telegram_user_id, photos[0], caption=formatted, parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            logger.info("Telegram rejected the listing photo. Sending text: %s", exc.message)
        else:
            return None
    await bot.send_message(
        telegram_user_id, formatted, reply_markup=reply_markup, parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    return None


async def send_album_actions(
    bot: Bot, telegram_user_id: int, album_message_id: int, *, reply_markup: object,
) -> None:
    await bot.send_message(
        telegram_user_id, "Действия с объявлением", reply_markup=reply_markup,
        disable_notification=True,
        reply_parameters=ReplyParameters(
            message_id=album_message_id, allow_sending_without_reply=True,
        ),
    )
