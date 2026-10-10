from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api.services.kufar_client import KufarAPIError
from bot.handlers import price
from bot.middlewares import THROTTLED_TEXT, ThrottlingMiddleware


class FakeKufarClient:
    """Stands in for KufarClient: records calls and serves canned ads."""

    ads: list[dict[str, Any]] = []
    error: Exception | None = None
    calls: list[tuple[str, dict[str, Any]]] = []
    closed = 0

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        type(self).calls.append(("search", kwargs))
        if self.error is not None:
            raise self.error
        return {"ads": list(self.ads), "total": 5000}

    async def search_all_ads(self, **kwargs: Any) -> dict[str, Any]:
        type(self).calls.append(("search_all_ads", kwargs))
        if self.error is not None:
            raise self.error
        return {"ads": list(self.ads), "total": 5000, "fetched_count": len(self.ads)}

    async def aclose(self) -> None:
        type(self).closed += 1


@pytest.fixture
def fake_kufar(monkeypatch: pytest.MonkeyPatch, sample_ads) -> type[FakeKufarClient]:
    FakeKufarClient.ads = sample_ads
    FakeKufarClient.error = None
    FakeKufarClient.calls = []
    FakeKufarClient.closed = 0
    monkeypatch.setattr(price, "KufarClient", FakeKufarClient)
    return FakeKufarClient


def _message() -> SimpleNamespace:
    return SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=1))


def _reply_text(message: SimpleNamespace) -> str:
    message.answer.assert_awaited_once()
    return message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_fetch_price_stats_uses_byn_pipeline(fake_kufar) -> None:
    stats = await price.fetch_price_stats("iphone")

    assert fake_kufar.calls == [("search_all_ads", {"query": "iphone", "currency": "BYN"})]
    assert fake_kufar.closed == 1
    # Six ads on the market, four with a usable price (zero and anomaly are dropped).
    assert stats["market_total"] == 6
    assert stats["analyzed_count"] == 4
    assert stats["min"] == 1800.0
    assert stats["max"] == 2500.0
    assert stats["median"] == 2100.0
    assert stats["currency"] == "BYN"


@pytest.mark.asyncio
async def test_cmd_price_replies_with_byn_stats(fake_kufar) -> None:
    message = _message()
    await price.cmd_price(message, SimpleNamespace(args=" iphone "))  # type: ignore[arg-type]

    assert _reply_text(message) == (
        "iphone\n"
        "Market total: 6\n"
        "Analyzed: 4\n"
        "Mean: 2 125 BYN\n"
        "Median: 2 100 BYN\n"
        "Range: 1 800 BYN - 2 500 BYN"
    )


@pytest.mark.asyncio
async def test_cmd_price_without_priced_ads(fake_kufar) -> None:
    fake_kufar.ads = [{"ad_id": 1, "subject": "iPhone", "price_byn": 0}]
    message = _message()
    await price.cmd_price(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]

    assert _reply_text(message) == "No priced listings found for iphone.\nMarket total: 1"


@pytest.mark.asyncio
async def test_cmd_price_usage_and_query_length(fake_kufar) -> None:
    message = _message()
    await price.cmd_price(message, SimpleNamespace(args=None))  # type: ignore[arg-type]
    assert _reply_text(message) == "Usage: /price <query>"

    message = _message()
    await price.cmd_price(message, SimpleNamespace(args="x" * 256))  # type: ignore[arg-type]
    assert _reply_text(message) == "Query is too long (max 255 characters)."
    assert fake_kufar.calls == []


@pytest.mark.asyncio
async def test_cmd_price_kufar_error(fake_kufar) -> None:
    fake_kufar.error = KufarAPIError("down")
    message = _message()
    await price.cmd_price(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]

    assert _reply_text(message) == "Price lookup is temporarily unavailable."
    assert fake_kufar.closed == 1


@pytest.mark.asyncio
async def test_cmd_price_unexpected_error_reaches_global_handler(fake_kufar) -> None:
    fake_kufar.error = RuntimeError("boom")
    message = _message()
    with pytest.raises(RuntimeError):
        await price.cmd_price(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_listings_builds_short_list_from_ads(fake_kufar) -> None:
    fake_kufar.ads = [
        {"subject": "iPhone 15", "price_byn": 200000, "ad_link": "https://www.kufar.by/item/1"},
        {"subject": "   ", "price_byn": 100000, "ad_link": "https://www.kufar.by/item/2"},
        {"subject": "iPhone 15 Pro", "price_byn": 0, "ad_link": ""},
        {"subject": "iPhone 15 Max", "price_byn": "bad", "ad_link": "javascript:alert(1)"},
    ]
    listings = await price.fetch_listings("iphone")

    assert fake_kufar.calls == [
        ("search", {"query": "iphone", "currency": "BYN", "size": price.TOP_FETCH_SIZE})
    ]
    assert fake_kufar.closed == 1
    assert listings == [
        {"title": "iPhone 15", "price_byn": 2000.0, "link": "https://www.kufar.by/item/1"},
        {"title": "iPhone 15 Pro", "price_byn": None, "link": None},
        {"title": "iPhone 15 Max", "price_byn": None, "link": None},
    ]


@pytest.mark.asyncio
async def test_cmd_top_replies_with_titles_prices_and_links(fake_kufar) -> None:
    message = _message()
    await price.cmd_top(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]

    assert _reply_text(message) == (
        "Top listings for iphone:\n"
        "- iPhone 15: 2 000 BYN\n"
        "  https://www.kufar.by/item/1\n"
        "- iPhone 15 Pro: 2 200 BYN\n"
        "  https://www.kufar.by/item/2\n"
        "- iPhone 15 Pro Max: 2 500 BYN\n"
        "  https://www.kufar.by/item/3\n"
        "- iPhone 15 Used: 1 800 BYN\n"
        "  https://www.kufar.by/item/4\n"
        "- Broken listing: no price\n"
        "  https://www.kufar.by/item/5"
    )


@pytest.mark.asyncio
async def test_cmd_top_empty_and_error(fake_kufar) -> None:
    fake_kufar.ads = []
    message = _message()
    await price.cmd_top(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]
    assert _reply_text(message) == "No listings found for iphone."

    fake_kufar.error = KufarAPIError("down")
    message = _message()
    await price.cmd_top(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]
    assert _reply_text(message) == "Listings lookup is temporarily unavailable."


@pytest.mark.asyncio
async def test_cmd_top_reply_fits_telegram_limit(fake_kufar) -> None:
    fake_kufar.ads = [
        {
            "subject": "iPhone " + "x" * 500,
            "price_byn": 100000,
            "ad_link": "https://www.kufar.by/item/" + "9" * 2000,
        }
        for _ in range(5)
    ]
    message = _message()
    await price.cmd_top(message, SimpleNamespace(args="q" * 255))  # type: ignore[arg-type]

    text = _reply_text(message)
    assert len(text) <= price.MAX_MESSAGE_LENGTH
    assert f"- iPhone {'x' * (price.MAX_TITLE_LENGTH - 8)}…: 1 000 BYN" in text


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_throttling_middleware_limits_each_user() -> None:
    clock = _Clock()
    middleware = ThrottlingMiddleware(rate_seconds=3.0, max_concurrent=1, clock=clock)
    handler = AsyncMock(return_value="handled")
    first = _message()
    other_user = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=2))

    assert await middleware(handler, first, {}) == "handled"
    assert await middleware(handler, other_user, {}) == "handled"

    clock.now += 1.0
    throttled = _message()
    assert await middleware(handler, throttled, {}) is None
    throttled.answer.assert_awaited_once_with(THROTTLED_TEXT)

    # Only one warning per window, further spam is dropped silently.
    clock.now += 1.0
    silent = _message()
    assert await middleware(handler, silent, {}) is None
    silent.answer.assert_not_awaited()

    clock.now += 1.5
    assert await middleware(handler, _message(), {}) == "handled"
    assert handler.await_count == 3


def test_price_router_is_throttled() -> None:
    assert any(
        isinstance(middleware, ThrottlingMiddleware)
        for middleware in price.router.message.middleware
    )
