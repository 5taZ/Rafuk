from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.commands import BOT_COMMANDS
from bot.handlers.start import _safe_start_param, cmd_app, cmd_help, cmd_start, cmd_start_deep


@pytest.mark.asyncio
async def test_cmd_help_answers() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_help(message)  # type: ignore[arg-type]
    message.answer.assert_awaited()
    text = message.answer.await_args.args[0]
    for command, description in BOT_COMMANDS:
        assert f"/{command}" in text
        assert description in text


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


def test_safe_start_param_allows_only_known_views() -> None:
    """PR-08: only the canonical view names go on the URL — every
    other input (including URL-shaped attacks) is dropped."""
    assert _safe_start_param("tracking") == "tracking"
    assert _safe_start_param("deals") == "deals"
    assert _safe_start_param("monitoring") == "monitoring"
    # Whitespace tolerance — bot users frequently paste with stray spaces.
    assert _safe_start_param("  tracking ") == "tracking"
    # Everything else is rejected.
    assert _safe_start_param(None) is None
    assert _safe_start_param("") is None
    assert _safe_start_param("trackers") is None  # frontend maps but bot rejects
    assert _safe_start_param("?evil=1") is None
    assert _safe_start_param("tracking#bad") is None
    assert _safe_start_param("tracking&extra=x") is None


@pytest.mark.asyncio
async def test_cmd_start_deep_strips_unknown_start_param(monkeypatch) -> None:
    """PR-08: a hostile /start ?evil#frag must NOT land in the URL.

    Capture the keyboard built by ``mini_app_keyboard`` and assert
    that ``start_param`` only ever appears when the value matches
    the allowlist — and is URL-encoded when it does.
    """
    captured_urls: list[str] = []

    def _fake_keyboard(url: str):
        captured_urls.append(url)
        # Return a sentinel so the handler skips the disabled-fallback branch.
        return SimpleNamespace(_marker="kb")

    from bot.handlers import start as start_mod

    monkeypatch.setattr(start_mod, "mini_app_keyboard", _fake_keyboard)

    async def _run(deep_arg: str | None) -> str:
        captured_urls.clear()
        message = SimpleNamespace(answer=AsyncMock())
        command = SimpleNamespace(args=deep_arg)
        await cmd_start_deep(message, command)  # type: ignore[arg-type]
        assert captured_urls, "mini_app_keyboard was not called"
        return captured_urls[-1]

    # Known param — encoded into the URL.
    url_ok = await _run("tracking")
    assert "start_param=tracking" in url_ok

    # Hostile payload — silently dropped, no start_param at all.
    url_evil = await _run("?evil=1#frag")
    assert "start_param" not in url_evil
    # And the URL still ends with the mini-app base, not something
    # the attacker stitched on.
    assert url_evil.startswith("https://")
