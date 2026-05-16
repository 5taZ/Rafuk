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



def test_enhanced_alert_keyboard_uses_favourites_label() -> None:
    """The watchlist button text was renamed from '👁 Отслеживать' to
    '⭐ В Избранное'. callback_data still uses ``add_watch:<ad_id>`` so
    the existing handler keeps routing it correctly."""
    from bot.keyboards import enhanced_alert_keyboard

    keyboard = enhanced_alert_keyboard(
        ad_id=4242,
        listing_url="https://www.kufar.by/item/4242",
    )

    # First row: lead + favourites callbacks.
    row1 = keyboard.inline_keyboard[0]
    assert row1[0].text == "📌 В покупки"
    assert row1[0].callback_data == "add_lead:4242"
    assert row1[1].text == "⭐ В Избранное"
    assert row1[1].callback_data == "add_watch:4242"

    # Second row: link to Kufar.
    row2 = keyboard.inline_keyboard[1]
    assert row2[0].text == "🔗 Открыть"
    assert row2[0].url == "https://www.kufar.by/item/4242"


def test_enhanced_alert_keyboard_omits_link_row_when_no_url() -> None:
    """If listing_url is missing the URL button is dropped — but the two
    callback buttons must still be present."""
    from bot.keyboards import enhanced_alert_keyboard

    keyboard = enhanced_alert_keyboard(ad_id=4242, listing_url=None)

    assert len(keyboard.inline_keyboard) == 1
    row = keyboard.inline_keyboard[0]
    assert [b.callback_data for b in row] == ["add_lead:4242", "add_watch:4242"]
