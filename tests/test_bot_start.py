from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.start import _safe_start_param, cmd_start, cmd_start_deep


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
async def test_cmd_start_resumes_bot_blocked_trackers_only_within_role_limit(monkeypatch) -> None:
    from sqlalchemy import select

    from api.database import get_engine, get_session_factory
    from api.models import TRACKER_PAUSE_REASON_BOT_BLOCKED, Tracker, User

    factory = get_session_factory(get_engine())
    monkeypatch.setattr("bot.handlers.start.get_bot_session_factory", lambda: factory)
    async with factory() as session:
        session.add(User(id=7001, telegram_user_id=7001, first_name="Тест"))
        await session.flush()
        session.add_all([
            Tracker(user_id=7001, query="running"),
            *(Tracker(
                user_id=7001, query=f"blocked {i}", paused=True,
                pause_reason=TRACKER_PAUSE_REASON_BOT_BLOCKED,
            ) for i in range(4)),
        ])
        await session.commit()

    message = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=7001))
    await cmd_start(message)  # type: ignore[arg-type]
    await cmd_start(message)  # type: ignore[arg-type]
    async with factory() as session:
        rows = (await session.execute(select(Tracker).order_by(Tracker.id))).scalars().all()
    assert [row.paused for row in rows] == [False, False, False, True, True]


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
    """TG-03: web_app buttons in /start replies fail in groups."""
    from aiogram.dispatcher.event.bases import UNHANDLED

    result = await dispatcher.propagate_event(
        update_type="message", event=_chat_message(chat_type, "/start"), bot=AsyncMock(),
    )

    assert result is UNHANDLED


@pytest.mark.asyncio
async def test_dispatcher_still_answers_private_chats(dispatcher) -> None:
    from aiogram.dispatcher.event.bases import UNHANDLED

    bot = AsyncMock()
    message = _chat_message("private", "/start").as_(bot)
    result = await dispatcher.propagate_event(
        update_type="message", event=message, bot=bot,
    )

    assert result is not UNHANDLED
    bot.assert_awaited_once()
    reply = bot.await_args.args[0]
    assert reply.reply_markup.inline_keyboard[0][0].web_app.url.startswith("https://")


@pytest.mark.asyncio
async def test_help_answers_with_the_start_greeting(dispatcher) -> None:
    """Older greetings in users' chats still mention /help."""
    bot = AsyncMock()
    message = _chat_message("private", "/help").as_(bot)
    await dispatcher.propagate_event(update_type="message", event=message, bot=bot)

    bot.assert_awaited_once()
    reply = bot.await_args.args[0]
    assert reply.text.startswith("Rafuk — цены, объявления и автопоиск на Kufar.")
    assert reply.reply_markup.inline_keyboard[0][0].web_app.url.startswith("https://")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/app", "/deals", "/profit", "/stats iphone"])
async def test_dispatcher_ignores_retired_commands(dispatcher, command: str) -> None:
    from aiogram.dispatcher.event.bases import UNHANDLED

    bot = AsyncMock()
    message = _chat_message("private", command).as_(bot)
    result = await dispatcher.propagate_event(
        update_type="message", event=message, bot=bot,
    )

    assert result is UNHANDLED
    bot.assert_not_awaited()


@pytest.mark.asyncio
async def test_configure_bot_menu_scopes_commands_to_private_chats() -> None:
    """TG-03: the command menu used to be set for the default scope, so
    groups listed /deals and /profit too."""
    from aiogram.types import BotCommandScopeAllPrivateChats

    from bot.main import configure_bot_menu

    bot = AsyncMock()
    await configure_bot_menu(bot, "https://rafuk.test/")

    bot.delete_my_commands.assert_awaited_once_with()
    commands = bot.set_my_commands.await_args.args[0]
    assert [c.command for c in commands] == ["start"]
    scope = bot.set_my_commands.await_args.kwargs["scope"]
    assert isinstance(scope, BotCommandScopeAllPrivateChats)


@pytest.mark.asyncio
async def test_configure_bot_menu_refreshes_the_default_launch_button() -> None:
    from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo

    from bot.main import configure_bot_menu

    bot = AsyncMock()
    for url in ("https://previous.rafuk.test/", "https://current.rafuk.test/"):
        await configure_bot_menu(bot, url)
        bot.set_chat_menu_button.assert_awaited_with(
            menu_button=MenuButtonWebApp(text="Открыть Rafuk", web_app=WebAppInfo(url=url)),
        )

    await configure_bot_menu(bot, "http://localhost:8080/")
    bot.set_chat_menu_button.assert_awaited_with(menu_button=MenuButtonDefault())


# ── Required channel subscription ──────────────────────────────────────────


@pytest.fixture
def channel_gate(monkeypatch):
    """REQUIRED_CHANNEL on, Telegram's getChatMember answered by ``members``."""
    from api.config import get_settings

    monkeypatch.setenv("REQUIRED_CHANNEL", "@RafuksToResell")
    get_settings.cache_clear()
    members: dict[int, bool] = {}
    asked: list[int] = []

    async def fake_fetch(bot_token, channel, user_id):
        assert channel == "RafuksToResell"
        asked.append(user_id)
        return members.get(user_id, False)

    monkeypatch.setattr("api.services.channel_subscription.fetch_channel_membership", fake_fetch)
    yield SimpleNamespace(members=members, asked=asked)
    get_settings.cache_clear()


def _callback(data: str, bot):
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat, Message, User

    return CallbackQuery(
        id="cb1", from_user=User(id=42, is_bot=False, first_name="Тест"),
        chat_instance="ci", data=data,
        message=Message(
            message_id=5, date=datetime.now(UTC), chat=Chat(id=42, type="private"), text="welcome",
        ).as_(bot),
    ).as_(bot)


def _calls(bot, method_name: str) -> list:
    return [
        call.args[0] for call in bot.await_args_list
        if type(call.args[0]).__name__ == method_name
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/start", "/start tracking", "/help"])
async def test_unsubscribed_user_gets_welcome_with_channel_link(
    dispatcher, channel_gate, text: str,
) -> None:
    bot = AsyncMock()
    await dispatcher.propagate_event(
        update_type="message", event=_chat_message("private", text).as_(bot), bot=bot,
    )

    (reply,) = _calls(bot, "SendMessage")
    assert reply.text.startswith("👋 Привет, Тест!")
    assert "@RafuksToResell" in reply.text
    subscribe, check = (row[0] for row in reply.reply_markup.inline_keyboard)
    assert subscribe.url == "https://t.me/RafuksToResell"
    assert check.callback_data == ("sub_check:tracking" if "tracking" in text else "sub_check")
    assert channel_gate.asked == [42]


@pytest.mark.asyncio
async def test_subscribed_user_gets_the_mini_app(dispatcher, channel_gate) -> None:
    channel_gate.members[42] = True
    bot = AsyncMock()
    await dispatcher.propagate_event(
        update_type="message", event=_chat_message("private", "/start").as_(bot), bot=bot,
    )

    (reply,) = _calls(bot, "SendMessage")
    assert reply.text.startswith("Rafuk — цены")
    assert reply.reply_markup.inline_keyboard[0][0].web_app.url.startswith("https://")


@pytest.mark.asyncio
async def test_admin_skips_the_channel_check(dispatcher, channel_gate, monkeypatch) -> None:
    from api.config import get_settings

    monkeypatch.setenv("ADMIN_TELEGRAM_USER_IDS", "42")
    get_settings.cache_clear()
    bot = AsyncMock()
    await dispatcher.propagate_event(
        update_type="message", event=_chat_message("private", "/start").as_(bot), bot=bot,
    )

    assert _calls(bot, "SendMessage")[0].text.startswith("Rafuk — цены")
    assert channel_gate.asked == []


@pytest.mark.asyncio
async def test_alert_buttons_are_gated_too(dispatcher, channel_gate) -> None:
    bot = AsyncMock()
    await dispatcher.propagate_event(
        update_type="callback_query", event=_callback("add_lead:123", bot), bot=bot,
    )

    (answer,) = _calls(bot, "AnswerCallbackQuery")
    assert answer.show_alert is True
    assert "@RafuksToResell" in answer.text
    assert not _calls(bot, "SendMessage")


@pytest.mark.asyncio
async def test_check_button_without_subscription_keeps_the_welcome(
    dispatcher, channel_gate,
) -> None:
    bot = AsyncMock()
    await dispatcher.propagate_event(
        update_type="callback_query", event=_callback("sub_check", bot), bot=bot,
    )

    (answer,) = _calls(bot, "AnswerCallbackQuery")
    assert answer.show_alert is True
    assert "не найдена" in answer.text
    assert not _calls(bot, "EditMessageText")


@pytest.mark.asyncio
async def test_check_button_after_subscribing_opens_the_requested_view(
    dispatcher, channel_gate,
) -> None:
    channel_gate.members[42] = True
    bot = AsyncMock()
    await dispatcher.propagate_event(
        update_type="callback_query", event=_callback("sub_check:tracking", bot), bot=bot,
    )

    assert _calls(bot, "AnswerCallbackQuery")[0].text == "Подписка подтверждена ✅"
    (edited,) = _calls(bot, "EditMessageText")
    assert edited.text.startswith("✅ Подписка подтверждена")
    assert "«Автопоиск»" in edited.text
    assert "start_param=tracking" in edited.reply_markup.inline_keyboard[0][0].web_app.url


@pytest.mark.asyncio
async def test_check_button_rejects_unknown_view(dispatcher, channel_gate) -> None:
    channel_gate.members[42] = True
    bot = AsyncMock()
    await dispatcher.propagate_event(
        update_type="callback_query", event=_callback("sub_check:evil", bot), bot=bot,
    )

    (edited,) = _calls(bot, "EditMessageText")
    assert "Rafuk — цены" in edited.text
    assert "start_param" not in edited.reply_markup.inline_keyboard[0][0].web_app.url


@pytest.mark.asyncio
async def test_startup_check_reports_missing_channel_admin_rights(monkeypatch, caplog) -> None:
    from bot import main as bot_main

    async def no_access(bot_token, channel, user_id):
        assert user_id == 7123456789
        return None

    monkeypatch.setattr(bot_main, "fetch_channel_membership", no_access)
    with caplog.at_level("ERROR", logger="bot.main"):
        result = await bot_main.check_required_channel_access("7123456789:AAF", "RafuksToResell")
    assert result is None
    assert "administrator" in caplog.text
    assert await bot_main.check_required_channel_access("7123456789:AAF", "") is None
