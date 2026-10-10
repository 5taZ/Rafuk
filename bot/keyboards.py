from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

DEEP_LINK_PARAMS = ("query", "view", "strict")


def mini_app_keyboard(url: str) -> InlineKeyboardMarkup | None:
    if not url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Open Analytics",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def is_http_url(url: str | None) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def build_query_deep_link(mini_app_url: str, *, query: str, strict_mode: bool = False) -> str:
    """Mini App link that opens the tracking view for ``query``.

    Existing query parameters of ``mini_app_url`` are kept; ``query``, ``view`` and
    ``strict`` are always set by this function.
    """
    parts = urlsplit(mini_app_url)
    params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in DEEP_LINK_PARAMS
    ]
    params.extend(
        [
            ("query", query),
            ("view", "tracking"),
            ("strict", "1" if strict_mode else "0"),
        ]
    )
    return urlunsplit(parts._replace(query=urlencode(params)))


def tracker_alert_keyboard(
    mini_app_url: str,
    *,
    query: str,
    listing_url: str,
    event_id: int,
    strict_mode: bool = False,
) -> InlineKeyboardMarkup:
    first_row = []
    if mini_app_url.startswith("https://"):
        first_row.append(
            InlineKeyboardButton(
                text="Открыть запрос",
                web_app=WebAppInfo(
                    url=build_query_deep_link(mini_app_url, query=query, strict_mode=strict_mode)
                ),
            )
        )
    # Telegram rejects the whole message when a URL button carries an empty or
    # non-http link, so the listing button is only added for real web links.
    if is_http_url(listing_url):
        first_row.append(InlineKeyboardButton(text="Открыть лот", url=listing_url))
    second_row = [
        InlineKeyboardButton(text="В работу", callback_data=f"lead:{event_id}"),
        InlineKeyboardButton(text="Позже", callback_data=f"later:{event_id}"),
    ]
    rows = [first_row, second_row] if first_row else [second_row]
    return InlineKeyboardMarkup(inline_keyboard=rows)
