from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import MessageEntity

logger = logging.getLogger(__name__)

# Animated variants from Telegram's RestrictedEmoji set, matched by fallback emoji.
_ANIMATED_ICONS = {
    "🔔": "5242628160297641831",
    "📉": "5361748661640372834",
    "🎯": "5350460637182993292",
    "📈": "5373001317042101552",
}


async def send_notification(
    bot: Bot,
    telegram_user_id: int,
    message: str,
    *,
    reply_markup: object | None,
) -> None:
    emoji, _, heading = message.partition("\n")[0].partition(" ")
    if heading and (emoji_id := _ANIMATED_ICONS.get(emoji)):
        length = len(emoji.encode("utf-16-le")) // 2
        entities = [
            MessageEntity(type="custom_emoji", offset=0, length=length, custom_emoji_id=emoji_id),
            MessageEntity(
                type="bold", offset=length + 1, length=len(heading.encode("utf-16-le")) // 2,
            ),
        ]
        try:
            await bot.send_message(
                telegram_user_id, message, reply_markup=reply_markup, entities=entities,
            )
            return
        except TelegramBadRequest as exc:
            if not any(term in exc.message.lower() for term in ("emoji", "entit", "premium")):
                raise
            logger.info("Telegram rejected alert formatting, retrying as plain text")

    await bot.send_message(telegram_user_id, message, reply_markup=reply_markup)
