"""Bot side of the required channel subscription (api/services/channel_subscription.py).

``SubscriptionGateMiddleware`` sits on the start and callback routers: an
unsubscribed user gets the welcome with the channel link instead of the
handler's answer. The «Я подписался» button is handled in
``bot/handlers/start.py``, outside the gated routers.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from api.config import get_settings
from api.services.account_status import is_admin_user
from api.services.channel_subscription import channel_url, is_channel_subscriber, required_channel
from bot.keyboards import subscription_keyboard

# Only a view-name shape fits into callback_data; start.py applies the real
# allowlist when the button is pressed.
_START_PARAM_SHAPE = re.compile(r"[a-z_]{1,32}")


def welcome_text(first_name: str | None, channel: str) -> str:
    name = (first_name or "").strip()
    greeting = f"👋 Привет, {name}!" if name else "👋 Привет!"
    return (
        f"{greeting}\n\n"
        "Rafuk помогает зарабатывать на перепродаже с Kufar:\n"
        "• показывает реальные цены рынка по любому запросу;\n"
        "• находит лоты дешевле рынка;\n"
        "• следит за новыми объявлениями и присылает уведомления;\n"
        "• ведёт ваши сделки и считает прибыль.\n\n"
        f"Бот доступен подписчикам канала @{channel}. Подпишитесь и нажмите «Я подписался»."
    )


def not_subscribed_alert(channel: str) -> str:
    return f"Подписка на @{channel} не найдена. Подпишитесь на канал и нажмите кнопку ещё раз."


async def check_subscription(user_id: int) -> tuple[bool, str]:
    """(subscribed, channel). Asks Telegram every time — no cached answer
    may hold back someone who has just subscribed."""
    settings = get_settings()
    channel = required_channel(settings)
    if not channel or is_admin_user(user_id, settings):
        return True, channel
    subscribed = await is_channel_subscriber(
        None, bot_token=settings.bot_token.get_secret_value(), channel=channel,
        user_id=user_id, use_cache=False,
    )
    return subscribed, channel


class SubscriptionGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        subscribed, channel = await check_subscription(user.id)
        if subscribed:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer(not_subscribed_alert(channel), show_alert=True)
        elif isinstance(event, Message):
            command = data.get("command")
            args = (getattr(command, "args", None) or "").strip()
            start_param = args if _START_PARAM_SHAPE.fullmatch(args) else None
            await event.answer(
                welcome_text(user.first_name, channel),
                reply_markup=subscription_keyboard(channel_url(channel), start_param),
            )
        return None
