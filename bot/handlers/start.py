from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from api.config import get_settings
from bot.commands import BOT_HELP_TEXT
from bot.keyboards import mini_app_keyboard

router = Router(name="start")


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep(message: Message, command: CommandStart) -> None:
    """Handle /start with a deep link parameter (e.g. /start tracking)."""
    settings = get_settings()
    deep_param = command.args
    # Build URL with start_param so the Mini App can auto-switch view
    url = settings.mini_app_url
    if deep_param and url.startswith("https://"):
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}start_param={deep_param}"
    keyboard = mini_app_keyboard(url)
    if keyboard is None:
        await message.answer(
            "Rafuk — аналитика Kufar.\n"
            "Мини-апп отключён локально — Telegram WebApp требует HTTPS."
        )
        return
    view_labels = {"tracking": "Автопоиск", "deals": "Сделки", "monitoring": "Избранное"}
    label = view_labels.get(deep_param, deep_param or "мини-апп")
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
