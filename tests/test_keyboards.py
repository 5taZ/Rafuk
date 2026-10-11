from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from bot.keyboards import (
    build_query_deep_link,
    is_http_url,
    mini_app_keyboard,
    tracker_alert_keyboard,
)

MINI_APP_URL = "https://kufar-analytics.example.com/app"


def _params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_deep_link_opens_tracking_view_with_strict_flag() -> None:
    url = build_query_deep_link(MINI_APP_URL, query="iphone 15 & pro", strict_mode=True)

    assert url.startswith(MINI_APP_URL + "?")
    assert _params(url) == {"query": ["iphone 15 & pro"], "view": ["tracking"], "strict": ["1"]}
    assert _params(build_query_deep_link(MINI_APP_URL, query="x"))["strict"] == ["0"]


def test_deep_link_preserves_existing_query_string_and_fragment() -> None:
    url = build_query_deep_link(
        "https://example.com/app?lang=ru&view=overview&query=old#top",
        query="iphone",
        strict_mode=False,
    )

    parts = urlsplit(url)
    assert parts.path == "/app"
    assert parts.fragment == "top"
    assert _params(url) == {
        "lang": ["ru"],
        "query": ["iphone"],
        "view": ["tracking"],
        "strict": ["0"],
    }


def test_tracker_alert_keyboard_full_layout() -> None:
    keyboard = tracker_alert_keyboard(
        MINI_APP_URL,
        query="iphone 15 128",
        listing_url="https://www.kufar.by/item/101",
        event_id=42,
        strict_mode=True,
    )

    first_row, second_row = keyboard.inline_keyboard
    assert first_row[0].text == "Открыть запрос"
    assert _params(first_row[0].web_app.url) == {
        "query": ["iphone 15 128"],
        "view": ["tracking"],
        "strict": ["1"],
    }
    assert first_row[1].text == "Открыть лот"
    assert first_row[1].url == "https://www.kufar.by/item/101"
    assert [button.callback_data for button in second_row] == ["lead:42", "later:42"]


def test_tracker_alert_keyboard_defaults_to_broad_search() -> None:
    keyboard = tracker_alert_keyboard(
        MINI_APP_URL,
        query="iphone",
        listing_url="https://www.kufar.by/item/1",
        event_id=1,
    )

    assert _params(keyboard.inline_keyboard[0][0].web_app.url)["strict"] == ["0"]


@pytest.mark.parametrize("listing_url", ["", "   ", "www.kufar.by/item/1", "javascript:alert(1)"])
def test_tracker_alert_keyboard_skips_invalid_listing_link(listing_url: str) -> None:
    keyboard = tracker_alert_keyboard(
        MINI_APP_URL,
        query="iphone",
        listing_url=listing_url,
        event_id=7,
    )

    first_row = keyboard.inline_keyboard[0]
    assert [button.text for button in first_row] == ["Открыть запрос"]
    assert all(button.url is None for row in keyboard.inline_keyboard for button in row)


def test_tracker_alert_keyboard_without_https_mini_app_and_link() -> None:
    keyboard = tracker_alert_keyboard(
        "http://localhost:8000/app",
        query="iphone",
        listing_url="",
        event_id=7,
    )

    # No empty first row: only the action buttons remain.
    assert len(keyboard.inline_keyboard) == 1
    assert [button.callback_data for button in keyboard.inline_keyboard[0]] == [
        "lead:7",
        "later:7",
    ]


def test_mini_app_keyboard_requires_https() -> None:
    keyboard = mini_app_keyboard(MINI_APP_URL)
    assert keyboard is not None
    assert keyboard.inline_keyboard[0][0].web_app.url == MINI_APP_URL
    assert mini_app_keyboard("http://localhost:8000/app") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.kufar.by/item/1", True),
        ("http://www.kufar.by/item/1", True),
        ("", False),
        (None, False),
        ("https://", False),
        ("ftp://www.kufar.by/item/1", False),
        ("/item/1", False),
    ],
)
def test_is_http_url(url: str | None, expected: bool) -> None:
    assert is_http_url(url) is expected
