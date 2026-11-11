from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select, update

from api.config import get_settings
from api.models import TRACKER_PAUSE_REASON_BOT_BLOCKED, Tracker, User
from api.services.tracker_limits import get_tracker_capacity
from bot.database import get_bot_session_factory
from bot.keyboards import SUBSCRIPTION_CHECK_PREFIX, mini_app_keyboard
from bot.subscription import SubscriptionGateMiddleware, check_subscription, not_subscribed_alert

logger = logging.getLogger(__name__)

router = Router(name="start")
router.message.middleware(SubscriptionGateMiddleware())
# «Я подписался» must stay reachable for those the gate above stops.
subscription_router = Router(name="subscription")


async def _resume_bot_blocked_trackers(message: Message | CallbackQuery) -> None:
    """RV-SCH-03: /start means the chat is reachable again — lift the
    pause the scheduler put on this user's trackers when Telegram
    refused delivery (bot blocked / Start never pressed). Best-effort:
    a DB hiccup must not cost the user the greeting.
    """
    from_user = getattr(message, "from_user", None)
    if from_user is None:
        return
    try:
        session_factory = get_bot_session_factory()
        async with session_factory() as session:
            internal_id = await session.scalar(
                select(User.id).where(User.telegram_user_id == from_user.id)
            )
            if internal_id is None:
                return
            used, limit = await get_tracker_capacity(session, internal_id)
            resumable = select(Tracker.id).where(
                Tracker.user_id == internal_id,
                Tracker.active.is_(True),
                Tracker.paused.is_(True),
                Tracker.pause_reason == TRACKER_PAUSE_REASON_BOT_BLOCKED,
            ).order_by(Tracker.created_at, Tracker.id).limit(max(0, limit - used))
            tracker_ids = (await session.execute(resumable)).scalars().all()
            result = await session.execute(
                update(Tracker)
                .where(
                    Tracker.id.in_(tracker_ids),
                    Tracker.active.is_(True),
                    Tracker.paused.is_(True),
                    Tracker.pause_reason == TRACKER_PAUSE_REASON_BOT_BLOCKED,
                )
                .values(
                    paused=False,
                    paused_at=None,
                    pause_reason=None,
                    updated_at=datetime.now(UTC),
                )
            )
            resumed = result.rowcount or 0
            await session.commit()
    except Exception:  # noqa: BLE001 — never block /start on this
        logger.warning(
            "Failed to resume bot-blocked trackers for user %s",
            from_user.id,
            exc_info=True,
        )
        return
    if resumed:
        logger.info(
            "Resumed %d bot-blocked tracker(s) for user %s on /start",
            resumed,
            from_user.id,
        )


# PR-08: deep-link start_param allowlist. Anything outside it is dropped
# from the URL (the Mini App still opens, just without a forced view
# switch) so a hostile ``/start ?evil#frag`` can't smuggle URL characters
# into the t.me link. TG-05 / SCH-15: each maps to (Mini App view, label);
# the views are the ones ``frontend/js/app_actions.js applyLaunchParams``
# can open. "monitoring" (older links) is the «Избранное» tab of the deals
# view — passed through as-is it opened the overview.
_START_PARAM_VIEWS = {
    "tracking": ("tracking", "Автопоиск"),
    "deals": ("deals", "Сделки"),
    "monitoring": ("deals", "Избранное"),
}


def _safe_start_param(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned in _START_PARAM_VIEWS:
        return cleaned
    return None


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep(message: Message, command: CommandObject) -> None:
    """Handle /start with a deep link parameter (e.g. /start tracking)."""
    await _resume_bot_blocked_trackers(message)
    # PR-08: ``command.args`` is whatever the user typed after
    # ``/start`` (or what landed in a t.me/<bot>?start=... link). The
    # previous shape concatenated it verbatim into the Mini-App URL,
    # so a hostile payload with ``#`` / ``?`` / ``&`` could rewrite
    # the resulting URL's fragment / query. Restrict to the known
    # set of view names; URL-encode whatever survives belt-and-
    # suspenders (the allowlist already guarantees safe characters,
    # but the encode keeps the call honest if the list ever grows).
    text, keyboard = _deep_link_reply(_safe_start_param(command.args))
    await message.answer(text, reply_markup=keyboard)


def _deep_link_reply(deep_param: str | None) -> tuple[str, InlineKeyboardMarkup | None]:
    settings = get_settings()
    view, label = _START_PARAM_VIEWS.get(deep_param, (None, "мини-апп"))
    url = settings.mini_app_url
    if view and url.startswith("https://"):
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}start_param={quote(view, safe='')}"
    keyboard = mini_app_keyboard(url)
    if keyboard is None:
        return (
            "Rafuk — аналитика Kufar.\n"
            "Мини-апп отключён локально — Telegram WebApp требует HTTPS."
        ), None
    return f"Открыть «{label}» в Rafuk.", keyboard


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await _resume_bot_blocked_trackers(message)
    await _send_greeting(message)


# Older greetings, still in users' chats, point at /help; it gets the same
# answer as /start instead of silence. The other retired commands stay off.
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await _send_greeting(message)


async def _send_greeting(message: Message) -> None:
    text, keyboard = _greeting()
    await message.answer(text, reply_markup=keyboard)


def _greeting() -> tuple[str, InlineKeyboardMarkup | None]:
    keyboard = mini_app_keyboard(get_settings().mini_app_url)
    text = "Rafuk — цены, объявления и автопоиск на Kufar."
    if keyboard is None:
        text += (
            "\nКнопка мини-аппа отключена локально — Telegram WebApp требует HTTPS."
        )
    return text, keyboard


@subscription_router.callback_query(
    (F.data == SUBSCRIPTION_CHECK_PREFIX) | F.data.startswith(f"{SUBSCRIPTION_CHECK_PREFIX}:")
)
async def cb_check_subscription(callback: CallbackQuery) -> None:
    subscribed, channel = await check_subscription(callback.from_user.id)
    if not subscribed:
        await callback.answer(not_subscribed_alert(channel), show_alert=True)
        return
    await callback.answer("Подписка подтверждена ✅")
    await _resume_bot_blocked_trackers(callback)
    deep_param = _safe_start_param(callback.data.partition(":")[2])
    text, keyboard = _deep_link_reply(deep_param) if deep_param else _greeting()
    text = f"✅ Подписка подтверждена — добро пожаловать!\n\n{text}"
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=keyboard)
            return
        except TelegramBadRequest:
            logger.debug("Subscription welcome could not be edited", exc_info=True)
    if callback.bot is not None:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=keyboard)
