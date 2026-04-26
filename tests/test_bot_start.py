from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.start import cmd_app, cmd_help, cmd_start


@pytest.mark.asyncio
async def test_cmd_help_answers() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_help(message)  # type: ignore[arg-type]
    message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_start_and_app_include_keyboard() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_start(message)  # type: ignore[arg-type]
    await cmd_app(message)  # type: ignore[arg-type]
    assert message.answer.await_count == 2


@pytest.mark.asyncio
async def test_cmd_start_message_is_in_russian() -> None:
    """Bot greetings used to mix Russian + English ('Rafuk helps you...');
    the whole UX is Russian, so /start must answer in Russian too."""
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_start(message)  # type: ignore[arg-type]
    text = message.answer.await_args.args[0]
    # Cheap heuristic: must contain Cyrillic and must NOT contain the old
    # English phrasings that lived here before.
    assert any("\u0400" <= ch <= "\u04ff" for ch in text), text
    assert "helps you" not in text.lower()
    assert "open the mini app" not in text.lower()
