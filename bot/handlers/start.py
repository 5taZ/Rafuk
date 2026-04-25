from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from api.config import get_settings
from bot.keyboards import mini_app_keyboard

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    settings = get_settings()
    keyboard = mini_app_keyboard(settings.mini_app_url)
    text = (
        "Rafuk helps you inspect market prices, listings, and tracker alerts.\n"
        "Use /app to open the mini app or /help to see commands."
    )
    if keyboard is None:
        text += "\nMini app button is disabled locally because Telegram WebApp requires HTTPS."
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer("/app — открыть мини-апп\n/start — приветственное сообщение")


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    settings = get_settings()
    keyboard = mini_app_keyboard(settings.mini_app_url)
    if keyboard is None:
        await message.answer(
            "Mini app is disabled locally. Telegram WebApp buttons require HTTPS."
        )
        return
    await message.answer(
        "Open Rafuk mini app.",
        reply_markup=keyboard,
    )
