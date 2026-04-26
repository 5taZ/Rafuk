from __future__ import annotations

from bot.keyboards import tracker_alert_keyboard


def test_tracker_alert_keyboard_has_query_and_listing_buttons() -> None:
    """Tracker alerts only carry two link buttons: open the mini-app for
    the saved query, and open the raw Kufar listing. The previous
    ``В работу`` / ``Позже`` callback buttons were removed because users
    operate through the mini-app and never used the inline shortcut."""
    keyboard = tracker_alert_keyboard(
        "https://kufar-analytics.example.com/app",
        query="iphone 15 128",
        listing_url="https://www.kufar.by/item/101",
    )

    assert keyboard is not None
    # Single row, two buttons. No callback_data anywhere — both are link
    # buttons (web_app + url).
    assert len(keyboard.inline_keyboard) == 1
    row = keyboard.inline_keyboard[0]
    assert len(row) == 2
    assert row[0].text == "Открыть запрос"
    assert "query=iphone+15+128" in row[0].web_app.url
    assert row[1].text == "Открыть лот"
    assert row[1].url == "https://www.kufar.by/item/101"
    assert all(button.callback_data is None for button in row)


def test_tracker_alert_keyboard_returns_none_when_nothing_to_link() -> None:
    """Local dev where mini_app_url is http:// and the listing has no
    URL — the function should return None so notify_user sends a plain
    message instead of failing on an empty inline_keyboard."""
    assert (
        tracker_alert_keyboard("http://localhost:8081", query="iphone", listing_url=None)
        is None
    )
