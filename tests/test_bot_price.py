from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api.services.kufar_client import KufarAPIError
from api.services.query_pipeline import DatasetCache, configure_dataset_cache
from bot.handlers import price
from bot.middlewares import BUSY_TEXT, THROTTLED_TEXT, USER_BUSY_TEXT, ThrottlingMiddleware


class FakeKufarClient:
    """Stands in for KufarClient: records calls and serves canned ads."""

    ads: list[dict[str, Any]] = []
    # Kufar's reported total (None: the number of served ads) and the pagination cut-off flag.
    total: int | None = None
    truncated = False
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
        return {
            "ads": list(self.ads),
            "total": len(self.ads) if self.total is None else self.total,
            "fetched_count": len(self.ads),
            "truncated": self.truncated,
        }

    async def aclose(self) -> None:
        type(self).closed += 1


@pytest.fixture
def fake_kufar(monkeypatch: pytest.MonkeyPatch, sample_ads) -> type[FakeKufarClient]:
    FakeKufarClient.ads = sample_ads
    FakeKufarClient.total = None
    FakeKufarClient.truncated = False
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
    assert stats["fetched_count"] == 6
    assert stats["truncated"] is False


@pytest.mark.asyncio
async def test_cmd_price_shows_kufar_total_and_truncation(fake_kufar) -> None:
    # A popular query: pagination stopped early, Kufar reports far more ads than were fetched.
    fake_kufar.total = 20000
    fake_kufar.truncated = True
    message = _message()
    await price.cmd_price(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]

    assert _reply_text(message) == (
        "iphone\n"
        "Market total: 20000\n"
        "Analyzed: 4\n"
        "Mean: 2 125 BYN\n"
        "Median: 2 100 BYN\n"
        "Range: 1 800 BYN - 2 500 BYN\n"
        "Note: only the latest 6 listings were analyzed."
    )


@pytest.mark.asyncio
async def test_fetch_price_stats_reads_dataset_truncated_flag(fake_kufar, monkeypatch) -> None:
    """The pipeline's QueryDataset.truncated (shared contract) is honored as well."""
    original = price.load_query_dataset

    async def load_with_flag(**kwargs: Any):
        dataset = await original(**kwargs)
        dataset.response.pop("truncated", None)
        return SimpleNamespace(
            total_results=dataset.total_results,
            truncated=True,
            response=dataset.response,
            ads=dataset.ads,
            unidentified_ads=dataset.unidentified_ads,
            price_stats=dataset.price_stats,
        )

    monkeypatch.setattr(price, "load_query_dataset", load_with_flag)
    stats = await price.fetch_price_stats("iphone")
    assert stats["truncated"] is True
    assert stats["market_total"] == 6


@pytest.mark.asyncio
async def test_market_total_never_below_fetched_sample(fake_kufar) -> None:
    fake_kufar.total = 2
    stats = await price.fetch_price_stats("iphone")
    assert stats["market_total"] == 6


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
async def test_repeated_top_reuses_the_dataset_cache(fake_kufar) -> None:
    configure_dataset_cache(DatasetCache())
    try:
        first = await price.fetch_listings("iphone 13")
        second = await price.fetch_listings("iphone 13")
        await price.fetch_price_stats("iphone 13")
        await price.fetch_listings("iphone 14")
    finally:
        configure_dataset_cache(None)

    assert first == second
    assert len(first) == price.TOP_LIMIT
    # Two /top cost one Kufar page; /price paginates separately (a different cache entry),
    # and another query gets its own page.
    assert [(name, kwargs["query"]) for name, kwargs in fake_kufar.calls] == [
        ("search", "iphone 13"),
        ("search_all_ads", "iphone 13"),
        ("search", "iphone 14"),
    ]
    assert fake_kufar.closed == 3


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


def _user_message(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=user_id))


class _BlockingHandler:
    """A handler that stays in flight until `release` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started: list[int] = []

    async def __call__(self, event: Any, data: dict[str, Any]) -> str:
        self.started.append(event.from_user.id)
        await self.release.wait()
        return "handled"


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_throttling_allows_one_command_in_flight_per_user() -> None:
    clock = _Clock()
    middleware = ThrottlingMiddleware(rate_seconds=3.0, max_concurrent=2, clock=clock)
    handler = _BlockingHandler()

    running = asyncio.create_task(middleware(handler, _user_message(1), {}))
    await _settle()
    assert handler.started == [1]

    # Outside the rate window, but the first command is still running: refused, not queued.
    clock.now += 5.0
    second = _user_message(1)
    assert await middleware(handler, second, {}) is None
    second.answer.assert_awaited_once_with(USER_BUSY_TEXT)
    assert handler.started == [1]

    handler.release.set()
    assert await running == "handled"
    clock.now += 5.0
    assert await middleware(handler, _user_message(1), {}) == "handled"


@pytest.mark.asyncio
async def test_throttling_answers_busy_instead_of_queueing() -> None:
    middleware = ThrottlingMiddleware(rate_seconds=3.0, max_concurrent=1, clock=_Clock())
    handler = _BlockingHandler()

    running = asyncio.create_task(middleware(handler, _user_message(1), {}))
    await _settle()

    other = _user_message(2)
    assert await middleware(handler, other, {}) is None
    other.answer.assert_awaited_once_with(BUSY_TEXT)
    assert handler.started == [1]

    handler.release.set()
    assert await running == "handled"
    # The slot is free again.
    assert await middleware(handler, _user_message(3), {}) == "handled"


@pytest.mark.asyncio
async def test_throttling_bounded_waiting_queue() -> None:
    middleware = ThrottlingMiddleware(
        rate_seconds=3.0, max_concurrent=1, max_waiting=1, clock=_Clock()
    )
    handler = _BlockingHandler()

    running = asyncio.create_task(middleware(handler, _user_message(1), {}))
    await _settle()
    waiting = asyncio.create_task(middleware(handler, _user_message(2), {}))
    await _settle()
    assert handler.started == [1]

    third = _user_message(3)
    assert await middleware(handler, third, {}) is None
    third.answer.assert_awaited_once_with(BUSY_TEXT)

    handler.release.set()
    assert await running == "handled"
    assert await waiting == "handled"
    assert handler.started == [1, 2]


@pytest.mark.asyncio
async def test_throttling_skips_events_it_does_not_apply_to() -> None:
    middleware = ThrottlingMiddleware(
        rate_seconds=3.0,
        max_concurrent=1,
        applies_to=lambda _event, data: data.get("expensive", True),
        clock=_Clock(),
    )
    handler = AsyncMock(return_value="handled")

    assert await middleware(handler, _message(), {"expensive": False}) == "handled"
    # The cheap command used up neither the window nor a slot.
    assert await middleware(handler, _message(), {}) == "handled"
    assert handler.await_count == 2


@pytest.mark.asyncio
async def test_price_usage_hint_does_not_consume_the_window(fake_kufar) -> None:
    middleware = next(
        middleware
        for middleware in price.router.message.middleware
        if isinstance(middleware, ThrottlingMiddleware)
    )

    async def handler(event: Any, data: dict[str, Any]) -> None:
        await price.cmd_price(event, data["command"])

    usage = _message()
    await middleware(handler, usage, {"command": SimpleNamespace(args=None)})
    assert _reply_text(usage) == "Usage: /price <query>"

    too_long = _message()
    await middleware(handler, too_long, {"command": SimpleNamespace(args="x" * 256)})
    assert _reply_text(too_long) == "Query is too long (max 255 characters)."

    # The corrected command right after the hints is served, not throttled.
    corrected = _message()
    await middleware(handler, corrected, {"command": SimpleNamespace(args="iphone")})
    assert _reply_text(corrected).startswith("iphone\nMarket total: 6")

    # A real lookup does use up the window.
    again = _message()
    await middleware(handler, again, {"command": SimpleNamespace(args="iphone")})
    assert _reply_text(again) == THROTTLED_TEXT
