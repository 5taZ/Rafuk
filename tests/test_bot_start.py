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
