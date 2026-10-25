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
async def test_cmd_start_resumes_only_bot_blocked_trackers() -> None:
    """RV-SCH-03: the scheduler pauses (no longer soft-deletes) trackers
    when Telegram refuses delivery; /start means the chat is reachable
    again, so exactly those pauses are lifted."""
    from sqlalchemy import select

    from api.database import get_engine, get_session_factory
    from api.models import TRACKER_PAUSE_REASON_BOT_BLOCKED, Tracker, User

    factory = get_session_factory(get_engine())
    async with factory() as session:
        session.add(User(id=7001, telegram_user_id=7001, first_name="Тест"))
        await session.flush()
        session.add_all([
            Tracker(
                user_id=7001, query="blocked", paused=True,
                pause_reason=TRACKER_PAUSE_REASON_BOT_BLOCKED,
            ),
            Tracker(user_id=7001, query="consent", paused=True, pause_reason="consent_revoked"),
            Tracker(user_id=7001, query="deleted", active=False),
        ])
        await session.commit()

    message = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=7001))
    await cmd_start(message)  # type: ignore[arg-type]

    async with factory() as session:
        state = {
            t.query: (t.active, t.paused, t.pause_reason)
            for t in (await session.execute(select(Tracker))).scalars()
        }
    assert state == {
        "blocked": (True, False, None),
        "consent": (True, True, "consent_revoked"),
        "deleted": (False, False, None),
    }
    message.answer.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_cmd_start_deep_opens_existing_views(monkeypatch) -> None:
    """TG-05 / SCH-15: ``/start monitoring`` promised «Избранное» but passed
    a view the Mini App does not have, so it opened the overview. The
    favourites tab lives in the deals view."""
    from bot.handlers import start as start_mod

    captured: list[str] = []
    monkeypatch.setattr(
        start_mod, "mini_app_keyboard", lambda url: captured.append(url) or object(),
    )

    for arg, view, label in (
        ("monitoring", "deals", "Избранное"),
        ("deals", "deals", "Сделки"),
        ("tracking", "tracking", "Автопоиск"),
    ):
        message = SimpleNamespace(answer=AsyncMock())
        await cmd_start_deep(message, SimpleNamespace(args=arg))  # type: ignore[arg-type]
        assert captured[-1].endswith(f"start_param={view}")
        assert label in message.answer.await_args.args[0]


def _chat_message(chat_type: str, text: str):
    from datetime import UTC, datetime

    from aiogram.types import Chat, Message, User

    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100 if chat_type != "private" else 42, type=chat_type),
        from_user=User(id=42, is_bot=False, first_name="Тест"),
        text=text,
    )


@pytest.fixture(scope="module")
def dispatcher():
    # The routers are module singletons and attach to one dispatcher only.
    from bot.main import build_dispatcher

    return build_dispatcher()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
async def test_dispatcher_ignores_commands_outside_private_chats(
    dispatcher, chat_type: str,
) -> None:
    """TG-03: web_app buttons fail in groups, and /profit answered there
    with the caller's finances for everyone to read."""
    from aiogram.dispatcher.event.bases import UNHANDLED

    result = await dispatcher.propagate_event(
        update_type="message", event=_chat_message(chat_type, "/profit"), bot=AsyncMock(),
    )

    assert result is UNHANDLED


@pytest.mark.asyncio
async def test_dispatcher_still_answers_private_chats(dispatcher) -> None:
    from aiogram.dispatcher.event.bases import UNHANDLED

    bot = AsyncMock()
    message = _chat_message("private", "/help").as_(bot)
    result = await dispatcher.propagate_event(
        update_type="message", event=message, bot=bot,
    )

    assert result is not UNHANDLED
    bot.assert_awaited()


@pytest.mark.asyncio
async def test_register_commands_scopes_the_menu_to_private_chats() -> None:
    """TG-03: the command menu used to be set for the default scope, so
    groups listed /deals and /profit too."""
    from aiogram.types import BotCommandScopeAllPrivateChats

    from bot.main import register_commands

    bot = AsyncMock()
    await register_commands(bot)

    bot.delete_my_commands.assert_awaited_once_with()
    commands = bot.set_my_commands.await_args.args[0]
    assert [c.command for c in commands] == [command for command, _ in BOT_COMMANDS]
    scope = bot.set_my_commands.await_args.kwargs["scope"]
    assert isinstance(scope, BotCommandScopeAllPrivateChats)
