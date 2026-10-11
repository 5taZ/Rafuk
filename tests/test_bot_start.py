from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardMarkup

from bot.handlers.start import cmd_app, cmd_help, cmd_start
from bot.main import ERROR_TEXT, on_error

MINI_APP_URL = "https://kufar-analytics.example.com/app"


def _assert_mini_app_markup(markup: object) -> None:
    assert isinstance(markup, InlineKeyboardMarkup)
    button = markup.inline_keyboard[0][0]
    assert button.text == "Open Analytics"
    assert button.web_app is not None
    assert button.web_app.url == MINI_APP_URL


@pytest.mark.asyncio
async def test_cmd_help_lists_commands() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_help(message)  # type: ignore[arg-type]

    text = message.answer.await_args.args[0]
    for command in ("/app", "/price", "/top", "/track", "/tracks", "/untrack"):
        assert command in text


@pytest.mark.asyncio
async def test_cmd_start_and_app_include_keyboard() -> None:
    # No sender: /start skips the user registration and answers anyway.
    message = SimpleNamespace(from_user=None, answer=AsyncMock())
    await cmd_start(message)  # type: ignore[arg-type]
    await cmd_app(message)  # type: ignore[arg-type]

    assert message.answer.await_count == 2
    for call in message.answer.await_args_list:
        _assert_mini_app_markup(call.kwargs["reply_markup"])
    assert "HTTPS" not in message.answer.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_cmd_start_and_app_without_https(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.config import get_settings

    monkeypatch.setenv("MINI_APP_URL", "http://localhost:8000/app")
    get_settings.cache_clear()
    # No sender: /start skips the user registration and answers anyway.
    message = SimpleNamespace(from_user=None, answer=AsyncMock())
    await cmd_start(message)  # type: ignore[arg-type]
    await cmd_app(message)  # type: ignore[arg-type]

    start_call, app_call = message.answer.await_args_list
    assert start_call.kwargs["reply_markup"] is None
    assert "HTTPS" in start_call.args[0]
    assert "reply_markup" not in app_call.kwargs
    assert "HTTPS" in app_call.args[0]


@pytest.mark.asyncio
async def test_error_handler_replies_to_message() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    event = SimpleNamespace(
        update=SimpleNamespace(update_id=1, message=message, callback_query=None),
        exception=ValueError("boom"),
    )

    assert await on_error(event) is True  # type: ignore[arg-type]
    message.answer.assert_awaited_once_with(ERROR_TEXT)


@pytest.mark.asyncio
async def test_error_handler_answers_callback() -> None:
    callback = SimpleNamespace(answer=AsyncMock())
    event = SimpleNamespace(
        update=SimpleNamespace(update_id=2, message=None, callback_query=callback),
        exception=ValueError("boom"),
    )

    assert await on_error(event) is True  # type: ignore[arg-type]
    callback.answer.assert_awaited_once_with(ERROR_TEXT, show_alert=True)


@pytest.mark.asyncio
async def test_error_handler_survives_failed_notification() -> None:
    message = SimpleNamespace(answer=AsyncMock(side_effect=RuntimeError("telegram down")))
    event = SimpleNamespace(
        update=SimpleNamespace(update_id=3, message=message, callback_query=None),
        exception=ValueError("boom"),
    )

    assert await on_error(event) is True  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_bot_engine_singleton_reset() -> None:
    from bot import database

    await database.init_bot_engine()
    engine = database.get_bot_engine()
    assert database.get_bot_engine() is engine

    await database.reset_bot_engine()
    assert database._engine is None
    fresh = database.get_bot_engine()
    assert fresh is not engine
    await database.close_bot_engine()
    assert database._engine is None
