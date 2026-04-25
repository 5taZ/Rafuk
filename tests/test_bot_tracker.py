from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api.database import get_engine, get_session_factory
from api.models import Base, LeadItem, TrackerEvent, User
from bot.handlers.tracker import handle_tracker_action
from bot.keyboards import tracker_alert_keyboard


def test_tracker_alert_keyboard_contains_actions() -> None:
    keyboard = tracker_alert_keyboard(
        "https://kufar-analytics.example.com/app",
        query="iphone 15 128",
        listing_url="https://www.kufar.by/item/101",
        event_id=42,
    )

    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Открыть запрос"
    assert "query=iphone+15+128" in keyboard.inline_keyboard[0][0].web_app.url
    assert keyboard.inline_keyboard[1][0].callback_data == "lead:42"
    assert keyboard.inline_keyboard[1][1].callback_data == "later:42"


@pytest.mark.asyncio
async def test_tracker_callback_creates_lead() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    # Create user with telegram_user_id so ensure_user can find it
    telegram_user_id = 123456
    async with session_factory() as session:
        user = User(telegram_user_id=telegram_user_id, first_name="Test", username="testuser")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        internal_user_id = user.id

    async with session_factory() as session:
        event = TrackerEvent(
            tracker_id=1,
            user_id=internal_user_id,
            ad_id=101,
            query="iphone 15 128",
            strict_mode=False,
            event_type="new_listing",
            title="iPhone 15 128GB",
            link="https://www.kufar.by/item/101",
            price_byn=1800,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        event_id = event.id

    callback = SimpleNamespace(
        data=f"lead:{event_id}",
        from_user=SimpleNamespace(id=telegram_user_id, first_name="Test", username="testuser"),
        answer=AsyncMock(),
    )
    await handle_tracker_action(callback)  # type: ignore[arg-type]

    async with session_factory() as session:
        lead = await session.scalar(
            select(LeadItem).where(LeadItem.user_id == internal_user_id, LeadItem.ad_id == 101)
        )
        assert lead is not None
        assert lead.status == "researching"
        assert lead.source == "telegram_alert"

    callback.answer.assert_awaited_once()
    await engine.dispose()
