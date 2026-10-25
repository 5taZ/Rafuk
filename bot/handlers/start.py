from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy import select, update

from api.config import get_settings
from api.models import TRACKER_PAUSE_REASON_BOT_BLOCKED, Tracker, User
from bot.commands import BOT_HELP_TEXT
from bot.database import get_bot_session_factory
from bot.keyboards import mini_app_keyboard

logger = logging.getLogger(__name__)

router = Router(name="start")


async def _resume_bot_blocked_trackers(message: Message) -> None:
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
            internal_id = (
                select(User.id)
                .where(User.telegram_user_id == from_user.id)
                .scalar_subquery()
            )
            result = await session.execute(
                update(Tracker)
                .where(
                    Tracker.user_id == internal_id,
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
    settings = get_settings()
    # PR-08: ``command.args`` is whatever the user typed after
    # ``/start`` (or what landed in a t.me/<bot>?start=... link). The
    # previous shape concatenated it verbatim into the Mini-App URL,
    # so a hostile payload with ``#`` / ``?`` / ``&`` could rewrite
    # the resulting URL's fragment / query. Restrict to the known
    # set of view names; URL-encode whatever survives belt-and-
    # suspenders (the allowlist already guarantees safe characters,
    # but the encode keeps the call honest if the list ever grows).
    deep_param = _safe_start_param(command.args)
    view, label = _START_PARAM_VIEWS.get(deep_param, (None, "мини-апп"))
    url = settings.mini_app_url
    if view and url.startswith("https://"):
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}start_param={quote(view, safe='')}"
    keyboard = mini_app_keyboard(url)
    if keyboard is None:
        await message.answer(
            "Rafuk — аналитика Kufar.\n"
            "Мини-апп отключён локально — Telegram WebApp требует HTTPS."
        )
        return
    await message.answer(f"Открыть «{label}» в Rafuk.", reply_markup=keyboard)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await _resume_bot_blocked_trackers(message)
    settings = get_settings()
    keyboard = mini_app_keyboard(settings.mini_app_url)
    text = (
        "Rafuk помогает анализировать цены, объявления и следить за рынком "
        "на kufar.by.\n"
        "Открой мини-апп через /app или посмотри список команд: /help"
    )
    if keyboard is None:
        text += (
            "\nКнопка мини-аппа отключена локально — Telegram WebApp требует HTTPS."
        )
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(BOT_HELP_TEXT)


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    settings = get_settings()
    keyboard = mini_app_keyboard(settings.mini_app_url)
    if keyboard is None:
        await message.answer(
            "Мини-апп отключён локально. Кнопки Telegram WebApp работают только "
            "поверх HTTPS."
        )
        return
    await message.answer(
        "Открыть мини-апп Rafuk.",
        reply_markup=keyboard,
    )
