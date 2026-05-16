from __future__ import annotations

from urllib.parse import quote

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from api.config import get_settings
from bot.commands import BOT_HELP_TEXT
from bot.keyboards import mini_app_keyboard

router = Router(name="start")


# PR-08: deep-link start_param allowlist. Mirrors the ``viewMap`` in
# ``frontend/js/app_actions.js applyLaunchParams``. Anything outside
# this set is dropped from the URL (the Mini App still opens, just
# without a forced view switch) so a hostile ``/start ?evil#frag``
# can't smuggle URL characters into the t.me link.
_ALLOWED_START_PARAMS = ("tracking", "deals", "monitoring")
_START_PARAM_LABELS = {
    "tracking": "Автопоиск",
    "deals": "Сделки",
    "monitoring": "Избранное",
}


def _safe_start_param(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned in _ALLOWED_START_PARAMS:
        return cleaned
    return None


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep(message: Message, command: CommandStart) -> None:
    """Handle /start with a deep link parameter (e.g. /start tracking)."""
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
    url = settings.mini_app_url
    if deep_param and url.startswith("https://"):
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}start_param={quote(deep_param, safe='')}"
    keyboard = mini_app_keyboard(url)
    if keyboard is None:
        await message.answer(
            "Rafuk — аналитика Kufar.\n"
            "Мини-апп отключён локально — Telegram WebApp требует HTTPS."
        )
        return
    label = _START_PARAM_LABELS.get(deep_param, "мини-апп")
    await message.answer(f"Открыть «{label}» в Rafuk.", reply_markup=keyboard)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
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
