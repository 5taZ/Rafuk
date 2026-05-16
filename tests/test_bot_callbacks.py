"""Tests for inline callback handlers (📌 В покупки / ⭐ В Избранное).

BE-15: the previous implementation hard-coded `query=""` and `link=""` in
the API payload, which `LeadCreate`/`WatchlistCreate` rejected with a
422 — the buttons silently no-op'd from the user's perspective. The fix
resolves the listing context from the most recent `TrackerEvent` row
that the scheduler persisted when it sent the alert. These tests pin
that behaviour and document the fallback path when the event has been
purged.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.database import get_engine, get_session_factory
from api.models import Tracker, TrackerEvent
from bot.handlers import callbacks as cb_module

sys.path.insert(0, str(Path(__file__).parent))
from conftest import make_user

TELEGRAM_USER_ID = 778899


def _make_callback(data: str, telegram_user_id: int = TELEGRAM_USER_ID) -> SimpleNamespace:
    """Tiny stand-in for aiogram CallbackQuery — only the fields the
    handlers actually read. AsyncMock for ``answer`` so we can assert
    the toast text the user sees."""
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=telegram_user_id),
        answer=AsyncMock(),
    )


async def _seed_tracker_event(
    *,
    ad_id: int,
    query: str = "iphone 15",
    title: str = "iPhone 15 256GB",
    link: str = "https://www.kufar.by/item/4242",
    price_byn: float | None = 1999.0,
    thumbnail: str | None = "https://cdn.kufar.by/4242.jpg",
) -> int:
    """Seed a User + Tracker + TrackerEvent. Returns the user.id so
    callers can sanity-check the FK chain if they need to.

    We deliberately avoid the API factory here — these tests exercise
    the callback's DB lookup path directly, so we bypass the routers.
    """
    engine = get_engine()
    factory = get_session_factory(engine)
    async with factory() as session:
        user = make_user(telegram_user_id=TELEGRAM_USER_ID, first_name="Тест")
        session.add(user)
        await session.flush()
        tracker = Tracker(user_id=user.id, query=query, strict_mode=False)
        session.add(tracker)
        await session.flush()
        session.add(
            TrackerEvent(
                tracker_id=tracker.id,
                user_id=user.id,
                ad_id=ad_id,
                query=query,
                strict_mode=False,
                event_type="new_listing",
                title=title,
                link=link,
                price_byn=price_byn,
                thumbnail=thumbnail,
            )
        )
        await session.commit()
        return user.id


@pytest.fixture
def patch_bot_session(monkeypatch):
    """Route the callback's ``get_bot_session_factory`` at the test
    engine. The bot module caches its own engine singleton; pointing
    the factory at the test engine sidesteps that cache so each test
    sees the fixture-managed schema (created/dropped by the
    ``create_test_tables`` autouse fixture)."""
    engine = get_engine()
    factory = get_session_factory(engine)
    monkeypatch.setattr(
        cb_module, "get_bot_session_factory", lambda: factory
    )
    return factory


@pytest.fixture
def captured_api_post(monkeypatch):
    """Replace ``_api_post`` so we can assert on the payload the
    handler builds, without standing up the FastAPI app or aiohttp
    client. Default response mirrors the success path (201 + dict)."""
    captured: dict = {}

    async def fake_post(path, telegram_user_id, *, json_body=None):
        captured["path"] = path
        captured["telegram_user_id"] = telegram_user_id
        captured["json_body"] = json_body
        return captured.get("_response", {"id": 1})

    monkeypatch.setattr(cb_module, "_api_post", fake_post)
    return captured


@pytest.mark.asyncio
async def test_add_lead_resolves_query_and_link_from_tracker_event(
    patch_bot_session, captured_api_post
) -> None:
    """The button used to send `query=""`, `link=""` — the API rejected
    that with 422 and the user saw a generic error toast. After the
    fix, the handler hydrates from the most recent matching
    TrackerEvent row."""
    ad_id = 4242
    await _seed_tracker_event(ad_id=ad_id)
    callback = _make_callback(f"add_lead:{ad_id}")

    await cb_module.cb_add_to_leads(callback)

    body = captured_api_post["json_body"]
    assert captured_api_post["path"] == "/api/v1/leads"
    assert body["ad_id"] == ad_id
    assert body["query"] == "iphone 15"
    assert body["title"] == "iPhone 15 256GB"
    assert body["link"] == "https://www.kufar.by/item/4242"
    assert body["price_byn"] == 1999.0
    assert body["thumbnail"] == "https://cdn.kufar.by/4242.jpg"
    assert body["status"] == "new"
    assert body["source"] == "bot_callback"
    callback.answer.assert_awaited_once()
    args, kwargs = callback.answer.await_args
    text = args[0] if args else kwargs.get("text", "")
    assert "покупки" in text.lower()


@pytest.mark.asyncio
async def test_add_watch_resolves_query_and_link_from_tracker_event(
    patch_bot_session, captured_api_post
) -> None:
    """Watchlist button mirrors the leads fix — same enrichment path,
    different endpoint and no `status`/`source` (the watchlist router
    fills those server-side)."""
    ad_id = 5151
    await _seed_tracker_event(
        ad_id=ad_id,
        query="ноутбук thinkpad",
        title="ThinkPad X1 Carbon Gen 9",
        link="https://www.kufar.by/item/5151",
        price_byn=3500.0,
    )
    callback = _make_callback(f"add_watch:{ad_id}")

    await cb_module.cb_add_to_watchlist(callback)

    body = captured_api_post["json_body"]
    assert captured_api_post["path"] == "/api/v1/watchlist"
    assert body["ad_id"] == ad_id
    assert body["query"] == "ноутбук thinkpad"
    assert body["title"] == "ThinkPad X1 Carbon Gen 9"
    assert body["link"] == "https://www.kufar.by/item/5151"
    assert body["price_byn"] == 3500.0
    # watchlist payload doesn't carry status/source — the router pins
    # status='watching' and source='watchlist' itself.
    assert "status" not in body
    assert "source" not in body
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_lead_picks_most_recent_event_for_ad(
    patch_bot_session, captured_api_post
) -> None:
    """Multiple alerts for the same ad (e.g. a new_listing followed by
    a price_drop) must hydrate from the *latest* event so the price
    matches what the user just saw."""
    ad_id = 7777
    user_id = await _seed_tracker_event(
        ad_id=ad_id, price_byn=2000.0, title="Camera v1"
    )
    # Add a newer event with an updated price.
    factory = patch_bot_session
    async with factory() as session:
        tracker = Tracker(user_id=user_id, query="camera", strict_mode=False)
        session.add(tracker)
        await session.flush()
        session.add(
            TrackerEvent(
                tracker_id=tracker.id,
                user_id=user_id,
                ad_id=ad_id,
                query="camera",
                strict_mode=False,
                event_type="price_drop",
                title="Camera v2 (newer)",
                link="https://www.kufar.by/item/7777",
                price_byn=1700.0,
                thumbnail=None,
            )
        )
        await session.commit()

    callback = _make_callback(f"add_lead:{ad_id}")
    await cb_module.cb_add_to_leads(callback)

    body = captured_api_post["json_body"]
    assert body["title"] == "Camera v2 (newer)"
    assert body["price_byn"] == 1700.0


@pytest.mark.asyncio
async def test_add_lead_falls_back_when_event_missing(
    patch_bot_session, captured_api_post
) -> None:
    """If the TrackerEvent has been purged (retention cleanup) the
    handler can't fabricate query/link out of thin air — it must tell
    the user to open the mini-app instead of POSTing a row that the
    API will reject with 422."""
    callback = _make_callback("add_lead:9999")

    await cb_module.cb_add_to_leads(callback)

    # No API call was made.
    assert "json_body" not in captured_api_post
    callback.answer.assert_awaited_once()
    args, kwargs = callback.answer.await_args
    text = args[0] if args else kwargs.get("text", "")
    assert "Откройте мини-апп" in text
    # show_alert=True so the user actually notices it (not just a
    # ghost toast at the top of the chat).
    assert kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_add_watch_falls_back_when_event_missing(
    patch_bot_session, captured_api_post
) -> None:
    """Same fallback path for watchlist — exercise it explicitly so a
    future refactor can't quietly lose the alert."""
    callback = _make_callback("add_watch:9999")

    await cb_module.cb_add_to_watchlist(callback)

    assert "json_body" not in captured_api_post
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_lead_invalid_ad_id_rejected(
    patch_bot_session, captured_api_post
) -> None:
    """The split-on-colon path can produce a non-integer if a
    callback_data string ever changes — make sure we don't blow up,
    and that we don't hit the DB / API."""
    callback = _make_callback("add_lead:not-a-number")

    await cb_module.cb_add_to_leads(callback)

    assert "json_body" not in captured_api_post
    callback.answer.assert_awaited_once_with(
        "Неверный ID объявления", show_alert=True
    )


@pytest.mark.asyncio
async def test_add_lead_only_finds_events_for_the_clicking_user(
    patch_bot_session, captured_api_post
) -> None:
    """IDOR-style guard: TrackerEvent for user A must not hydrate a
    callback fired by user B even though they share the same ad_id.
    The handler scopes by telegram_user_id → User.id → TrackerEvent."""
    ad_id = 6060
    await _seed_tracker_event(ad_id=ad_id)  # belongs to TELEGRAM_USER_ID
    other_callback = _make_callback(
        f"add_lead:{ad_id}", telegram_user_id=999111
    )

    await cb_module.cb_add_to_leads(other_callback)

    # No event for user 999111 → fallback path, no API call.
    assert "json_body" not in captured_api_post
    other_callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_lead_conflict_keeps_user_informed(
    patch_bot_session, captured_api_post
) -> None:
    """If the lead already exists, the API returns 409 → ``_api_post``
    converts that to ``{"conflict": True}``. Make sure the handler
    surfaces the right text."""
    ad_id = 8181
    await _seed_tracker_event(ad_id=ad_id)
    captured_api_post["_response"] = {"conflict": True}
    callback = _make_callback(f"add_lead:{ad_id}")

    await cb_module.cb_add_to_leads(callback)

    args, kwargs = callback.answer.await_args
    text = args[0] if args else kwargs.get("text", "")
    assert "Уже в покупках" in text



@pytest.mark.asyncio
async def test_add_watch_success_answers_with_favourites_text(
    patch_bot_session, captured_api_post
) -> None:
    """After the rename, success answer is '⭐ Добавлено в избранное',
    not the legacy '👁 Добавлено в отслеживание'."""
    ad_id = 6262
    await _seed_tracker_event(ad_id=ad_id)
    callback = _make_callback(f"add_watch:{ad_id}")

    await cb_module.cb_add_to_watchlist(callback)

    args, kwargs = callback.answer.await_args
    text = args[0] if args else kwargs.get("text", "")
    assert "избранное" in text
    assert "отслеживание" not in text


@pytest.mark.asyncio
async def test_add_watch_conflict_says_already_in_favourites(
    patch_bot_session, captured_api_post
) -> None:
    """Watchlist conflict response must say 'Уже в избранном', not
    'Уже в покупках' — the latter was the original copy bug surfaced
    during the bot-thumbnail audit."""
    ad_id = 6363
    await _seed_tracker_event(ad_id=ad_id)
    captured_api_post["_response"] = {"conflict": True}
    callback = _make_callback(f"add_watch:{ad_id}")

    await cb_module.cb_add_to_watchlist(callback)

    args, kwargs = callback.answer.await_args
    text = args[0] if args else kwargs.get("text", "")
    assert "избранном" in text
    assert "покупках" not in text
