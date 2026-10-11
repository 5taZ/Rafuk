from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.services import kufar_client
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.query_pipeline import (
    DatasetCache,
    configure_dataset_cache,
    dataset_cache_key,
    get_dataset_cache,
    load_query_dataset,
    normalize_ads,
)

SAMPLE_RESPONSE = {
    "ads": [
        {
            "ad_id": 12345,
            "subject": "iPhone 15 Pro 256GB",
            "price_byn": 399900,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/12345",
            "list_time": "2025-04-01T10:00:00",
            "category": 1010,
            "region_id": 6,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
                {"p": "seller_type", "v": "Частное лицо"},
            ],
        }
    ],
    "pagination": {"pages": [{"token": "next_cursor_token", "label": 2}]},
}


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.kufar_request_delay = 0.0
    settings.kufar_parallel_semaphore = 3
    settings.kufar_timeout = 15.0
    return settings


@pytest.fixture
def backoff_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry backoffs instead of sleeping, so retry tests take milliseconds."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def skip_pacer_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr(kufar_client, "_backoff_sleep", fake_sleep)
    # A 429 also pauses the shared pacer; skip that wait too.
    monkeypatch.setattr(kufar_client, "_pacer_sleep", skip_pacer_sleep)
    return sleeps


@pytest.fixture
def ok_response() -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = SAMPLE_RESPONSE
    response.raise_for_status = MagicMock()
    return response


def make_client(
    settings: MagicMock,
    handler: Callable[[httpx.Request], Any],
) -> KufarClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return KufarClient(settings, http_client=http_client)


def page(ad_ids: list[int], *, next_token: str | None = None, total: int | None = None) -> dict:
    pages: list[dict[str, Any]] = [{"label": "self", "num": 1, "token": None}]
    if next_token:
        pages.append({"label": "next", "num": 2, "token": next_token})
    payload: dict[str, Any] = {
        "ads": [{"ad_id": ad_id} for ad_id in ad_ids],
        "pagination": {"pages": pages},
    }
    if total is not None:
        payload["total"] = total
    return payload


@pytest.mark.asyncio
async def test_search_returns_ads(ok_response: MagicMock, mock_settings: MagicMock) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        client = KufarClient(mock_settings)
        result = await client.search(query="iPhone 15")
    assert "ads" in result
    assert len(result["ads"]) == 1
    assert result["ads"][0]["ad_id"] == 12345


@pytest.mark.asyncio
async def test_search_sends_correct_user_agent(
    ok_response: MagicMock,
    mock_settings: MagicMock,
) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response) as mock:
        await KufarClient(mock_settings).search(query="test")
    ua = mock.call_args.kwargs.get("headers", {}).get("User-Agent", "")
    assert "KufarAnalytics" in ua


@pytest.mark.asyncio
async def test_search_sends_correct_params(
    ok_response: MagicMock,
    mock_settings: MagicMock,
) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response) as mock:
        await KufarClient(mock_settings).search(
            query="iPhone",
            size=100,
            currency="USD",
            region=6,
        )
    params = mock.call_args.kwargs.get("params", {})
    assert params["query"] == "iPhone"
    assert params["size"] == 100
    # Prices are always requested in BYN; the currency argument is kept for compatibility.
    assert params["cur"] == "BYN"
    assert params["rgn"] == 6


@pytest.mark.asyncio
async def test_search_raises_kufar_api_error_after_retries(
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="unavailable")

    with pytest.raises(KufarAPIError, match="after 3 attempts"):
        await make_client(mock_settings, handler).search(query="test")
    assert calls == 3
    assert backoff_sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_search_retries_on_failure_then_succeeds(
    ok_response: MagicMock,
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    bad_response = MagicMock(spec=httpx.Response)
    bad_response.status_code = 502
    bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error",
        request=MagicMock(),
        response=bad_response,
    )
    call_count = 0

    async def side_effect(*args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        nonlocal call_count
        call_count += 1
        return bad_response if call_count < 3 else ok_response

    with patch("httpx.AsyncClient.get", side_effect=side_effect):
        result = await KufarClient(mock_settings).search(query="test")
    assert call_count == 3
    assert "ads" in result
    assert len(backoff_sleeps) == 2


@pytest.mark.asyncio
async def test_search_retries_network_errors(
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection refused", request=request)
        if calls == 2:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=page([1]))

    result = await make_client(mock_settings, handler).search(query="test")
    assert calls == 3
    assert result["ads"] == [{"ad_id": 1}]
    assert backoff_sleeps == [1.0, 2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 403, 404])
async def test_search_does_not_retry_client_errors(
    status_code: int,
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, text="nope")

    with pytest.raises(KufarAPIError, match=f"HTTP {status_code}"):
        await make_client(mock_settings, handler).search(query="test")
    assert calls == 1
    assert backoff_sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retry_after", "expected_sleep"),
    [("5", 5.0), ("3600", kufar_client.MAX_RETRY_AFTER_SECONDS), ("garbage", 1.0)],
)
async def test_search_honors_retry_after_on_429(
    retry_after: str,
    expected_sleep: float,
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200, json=page([7]))

    result = await make_client(mock_settings, handler).search(query="test")
    assert calls == 2
    assert result["ads"] == [{"ad_id": 7}]
    assert backoff_sleeps == [expected_sleep]


@pytest.mark.asyncio
async def test_search_wraps_invalid_json(
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="<html>captcha</html>")

    with pytest.raises(KufarAPIError, match="non-JSON"):
        await make_client(mock_settings, handler).search(query="test")
    assert calls == 1
    assert backoff_sleeps == []


@pytest.mark.asyncio
async def test_search_parses_next_cursor(ok_response: MagicMock, mock_settings: MagicMock) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        result = await KufarClient(mock_settings).search(query="test")
    assert KufarClient.extract_next_cursor(result) == "next_cursor_token"


def test_extract_next_cursor_prefers_next_label() -> None:
    response = {
        "pagination": {
            "pages": [
                {"label": "prev", "token": "prev-token"},
                {"label": "self", "token": None},
                {"label": "next", "token": "next-token"},
                {"label": "page", "token": "page-3"},
            ]
        }
    }
    assert KufarClient.extract_next_cursor(response) == "next-token"


def test_extract_next_cursor_stops_on_labelled_last_page() -> None:
    response = {
        "pagination": {
            "pages": [
                {"label": "prev", "token": "prev-token"},
                {"label": "self", "token": None},
            ]
        }
    }
    assert KufarClient.extract_next_cursor(response) is None


@pytest.mark.parametrize(
    "response",
    [{}, {"pagination": None}, {"pagination": {"pages": "x"}}, {"pagination": {"pages": []}}],
)
def test_extract_next_cursor_handles_malformed_pagination(response: dict) -> None:
    assert KufarClient.extract_next_cursor(response) is None


@pytest.mark.asyncio
async def test_search_all_ads_collects_all_pages(mock_settings: MagicMock) -> None:
    first = MagicMock(spec=httpx.Response)
    first.raise_for_status = MagicMock()
    first.json.return_value = {
        "ads": [{"ad_id": 1}, {"ad_id": 2}],
        "pagination": {"pages": [{"token": "cursor-2"}]},
        "total": 3,
    }
    second = MagicMock(spec=httpx.Response)
    second.raise_for_status = MagicMock()
    second.json.return_value = {
        "ads": [{"ad_id": 3}],
        "pagination": {"pages": []},
        "total": 3,
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=[first, second]):
        result = await KufarClient(mock_settings).search_all_ads(query="test")

    assert result["total"] == 3
    assert result["fetched_count"] == 3
    assert result["truncated"] is False
    assert [ad["ad_id"] for ad in result["ads"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_search_all_ads_follows_next_label_and_deduplicates(
    mock_settings: MagicMock,
) -> None:
    pages = {
        None: page([1, 2], next_token="c2"),
        "c2": page([2, 3], next_token="c3"),
        "c3": page([4]),
    }
    seen_cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        seen_cursors.append(cursor)
        assert request.url.params["cur"] == "BYN"
        return httpx.Response(200, json=pages[cursor])

    result = await make_client(mock_settings, handler).search_all_ads(query="test", currency="USD")

    assert seen_cursors == [None, "c2", "c3"]
    assert [ad["ad_id"] for ad in result["ads"]] == [1, 2, 3, 4]
    # A missing total is not replaced by the first page length.
    assert result["total"] is None
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_search_all_ads_stops_on_repeated_cursor(mock_settings: MagicMock) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # Every page points at the same cursor but brings new ads.
        return httpx.Response(200, json=page([calls], next_token="same"))

    result = await make_client(mock_settings, handler).search_all_ads(query="test")
    assert calls == 2
    assert [ad["ad_id"] for ad in result["ads"]] == [1, 2]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_search_all_ads_stops_on_page_without_new_ads(mock_settings: MagicMock) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=page([1, 2], next_token=f"c{calls}"))

    result = await make_client(mock_settings, handler).search_all_ads(query="test")
    assert calls == 2
    assert [ad["ad_id"] for ad in result["ads"]] == [1, 2]


@pytest.mark.asyncio
async def test_search_all_ads_stops_at_page_limit(mock_settings: MagicMock) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=page([calls], next_token=f"c{calls}", total=1000))

    client = make_client(mock_settings, handler)
    result = await client.search_all_ads(query="test", max_pages=3)
    assert calls == 3
    assert result["fetched_count"] == 3
    assert result["total"] == 1000
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_all_ads_has_default_page_limit(mock_settings: MagicMock) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=page([calls], next_token=f"c{calls}"))

    result = await make_client(mock_settings, handler).search_all_ads(query="test")
    assert calls == kufar_client.DEFAULT_MAX_PAGES
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_all_ads_stops_at_deadline(mock_settings: MagicMock) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls > 1:
            await asyncio.sleep(1.0)
        return httpx.Response(200, json=page([calls], next_token=f"c{calls}"))

    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await make_client(mock_settings, handler).search_all_ads(
        query="test",
        deadline_seconds=0.05,
    )
    assert loop.time() - started < 0.5
    assert [ad["ad_id"] for ad in result["ads"]] == [1]
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_all_ads_first_page_error_propagates(
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(KufarAPIError):
        await make_client(mock_settings, handler).search_all_ads(query="test")


@pytest.mark.asyncio
async def test_requests_are_paced_across_client_instances(mock_settings: MagicMock) -> None:
    mock_settings.kufar_request_delay = 0.03
    mock_settings.kufar_parallel_semaphore = 3
    loop = asyncio.get_running_loop()
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(loop.time())
        return httpx.Response(200, json=page([1]))

    clients = [make_client(mock_settings, handler) for _ in range(3)]
    await asyncio.gather(*(client.search(query="test") for client in clients))

    assert len(starts) == 3
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:], strict=False)]
    assert all(gap >= 0.025 for gap in gaps), gaps


@pytest.mark.asyncio
async def test_requests_in_flight_are_bounded_process_wide(mock_settings: MagicMock) -> None:
    mock_settings.kufar_parallel_semaphore = 2
    in_flight = 0
    max_in_flight = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.005)
        in_flight -= 1
        return httpx.Response(200, json=page([1]))

    clients = [make_client(mock_settings, handler) for _ in range(6)]
    await asyncio.gather(*(client.search(query="test") for client in clients))
    assert max_in_flight == 2


# --- Dataset pipeline (api/services/query_pipeline.py) -------------------------------------------


def test_normalize_ads_coerces_types_and_drops_unusable_ads() -> None:
    ads = [
        {"ad_id": None, "subject": "no id"},
        {"ad_id": "abc", "subject": "bad id"},
        "not-a-dict",
        {
            "ad_id": "42",
            "list_time": None,
            "images": None,
            "ad_parameters": [{"p": "condition", "v": "new"}, "junk", None],
            "region_id": "6",
            "price_byn": "399900",
        },
        {
            "ad_id": 7.0,
            "list_time": 1712000000,
            "images": ["https://example.com/a.jpg", {"path": "a.jpg"}],
            "ad_parameters": None,
            "region_id": 6.5,
            "price_byn": float("nan"),
        },
        {"ad_id": 8, "region_id": True, "price_byn": "inf"},
    ]

    normalized = normalize_ads(ads)

    assert [ad["ad_id"] for ad in normalized] == [42, 7, 8]
    first, second, third = normalized
    assert first["list_time"] == ""
    assert first["images"] == []
    assert first["ad_parameters"] == [{"p": "condition", "v": "new"}]
    assert first["region_id"] == 6
    assert first["price_byn"] == 399900
    assert second["list_time"] == "1712000000"
    assert second["images"] == [{"path": "a.jpg"}]
    assert second["ad_parameters"] == []
    assert second["region_id"] is None
    assert second["price_byn"] is None
    assert third["region_id"] is None
    assert third["price_byn"] is None
    # The input is not mutated.
    assert ads[3]["ad_id"] == "42"


def test_normalize_ads_handles_non_list_input() -> None:
    assert normalize_ads(None) == []
    assert normalize_ads({"ad_id": 1}) == []


class PipelineFakeClient:
    calls: list[dict[str, Any]] = []
    response: dict[str, Any] = {}

    def __init__(self, settings: object) -> None:
        del settings

    async def search_all_ads(self, **kwargs: Any) -> dict[str, Any]:
        PipelineFakeClient.calls.append(kwargs)
        await asyncio.sleep(0)
        return PipelineFakeClient.response

    async def aclose(self) -> None:
        return None


@pytest.fixture
def pipeline_client() -> type[PipelineFakeClient]:
    PipelineFakeClient.calls = []
    PipelineFakeClient.response = {
        "ads": [
            {"ad_id": 1, "subject": "iPhone 15", "price_byn": "200000", "list_time": None},
            {"ad_id": None, "subject": "iPhone 15 broken", "price_byn": 1},
            {"ad_id": 2, "subject": "iPhone 15 Pro", "price_byn": 300000, "images": None},
        ],
        "total": 3,
    }
    return PipelineFakeClient


@pytest.fixture
def dataset_cache() -> Any:
    # conftest disables the process-wide dataset cache again after every test.
    cache = DatasetCache()
    configure_dataset_cache(cache)
    return cache


@pytest.mark.asyncio
async def test_load_query_dataset_normalizes_ads(
    mock_settings: MagicMock,
    pipeline_client: type[PipelineFakeClient],
) -> None:
    assert get_dataset_cache() is None  # disabled by default
    dataset = await load_query_dataset(
        query="iphone 15",
        currency="USD",
        strict_search=False,
        settings=mock_settings,
        client_factory=pipeline_client,
    )
    # The ad without an id is not listed but its price still counts.
    assert [ad["ad_id"] for ad in dataset.ads] == [1, 2]
    assert [ad["subject"] for ad in dataset.unidentified_ads] == ["iPhone 15 broken"]
    assert "ad_id" not in dataset.unidentified_ads[0]
    assert dataset.total_results == 3
    assert dataset.ads[0]["list_time"] == ""
    assert dataset.ads[1]["images"] == []
    assert dataset.price_stats.count == 3
    # Kufar is always asked for BYN prices.
    assert pipeline_client.calls[0]["currency"] == "BYN"


@pytest.mark.asyncio
async def test_load_query_dataset_without_cache_fetches_every_time(
    mock_settings: MagicMock,
    pipeline_client: type[PipelineFakeClient],
) -> None:
    for _ in range(2):
        await load_query_dataset(
            query="iphone 15",
            currency="BYN",
            strict_search=False,
            settings=mock_settings,
            client_factory=pipeline_client,
        )
    assert len(pipeline_client.calls) == 2


@pytest.mark.asyncio
async def test_load_query_dataset_shares_cached_dataset_across_currencies(
    mock_settings: MagicMock,
    pipeline_client: type[PipelineFakeClient],
    dataset_cache: DatasetCache,
) -> None:
    async def load(query: str, currency: str, strict: bool, **search_kwargs: Any):
        return await load_query_dataset(
            query=query,
            currency=currency,
            strict_search=strict,
            settings=mock_settings,
            client_factory=pipeline_client,
            search_kwargs=search_kwargs or None,
        )

    results = await asyncio.gather(
        load("iphone 15", "BYN", False),
        load("iPhone  15", "USD", False),
    )
    assert len(pipeline_client.calls) == 1
    assert results[0].currency == "BYN"
    assert results[1].currency == "USD"
    assert results[0].ads == results[1].ads

    # The cache holds the response before the strict filter, so strict mode reuses it.
    strict = await load("iphone 15", "BYN", True)
    assert len(pipeline_client.calls) == 1
    assert [ad["ad_id"] for ad in strict.ads] == [1]

    await load("iphone 15", "BYN", False, region=7)
    assert len(pipeline_client.calls) == 2
    assert pipeline_client.calls[-1]["region"] == 7


def test_dataset_cache_key_ignores_currency_and_none_filters() -> None:
    base = dataset_cache_key("iPhone 15", False, {})
    assert dataset_cache_key("iphone 15", False, {"currency": "USD", "region": None}) == base
    assert dataset_cache_key("iphone 15", True, {}) == base
    assert dataset_cache_key("iphone 15", False, {"region": 7}) != base


# --- Later-page failures, shared 429 pause and bounded pacer queue -------------------------------


@pytest.mark.asyncio
async def test_search_all_ads_keeps_pages_when_later_page_fails(
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        calls.append(cursor)
        if cursor is None:
            return httpx.Response(200, json=page([1, 2], next_token="c2", total=500))
        if cursor == "c2":
            return httpx.Response(200, json=page([3], next_token="c3", total=500))
        return httpx.Response(503, text="unavailable")

    result = await make_client(mock_settings, handler).search_all_ads(query="test")

    assert calls == [None, "c2", "c3", "c3", "c3"]
    assert [ad["ad_id"] for ad in result["ads"]] == [1, 2, 3]
    assert result["fetched_count"] == 3
    assert result["total"] == 500
    assert result["truncated"] is True
    assert backoff_sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_search_all_ads_keeps_pages_when_later_page_is_rejected(
    mock_settings: MagicMock,
    backoff_sleeps: list[float],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") is None:
            return httpx.Response(200, json=page([1], next_token="c2"))
        return httpx.Response(400, text="bad cursor")

    result = await make_client(mock_settings, handler).search_all_ads(query="test")
    assert [ad["ad_id"] for ad in result["ads"]] == [1]
    assert result["truncated"] is True
    assert backoff_sleeps == []


@pytest.mark.asyncio
async def test_retry_after_pauses_every_request_of_the_process(
    mock_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_backoff(seconds: float) -> None:
        del seconds

    monkeypatch.setattr(kufar_client, "_backoff_sleep", no_backoff)
    loop = asyncio.get_running_loop()
    starts: list[float] = []
    limited = asyncio.Event()
    rate_limited_at = 0.0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal rate_limited_at
        starts.append(loop.time())
        if len(starts) == 1:
            rate_limited_at = loop.time()
            limited.set()
            return httpx.Response(429, headers={"Retry-After": "0.2"})
        return httpx.Response(200, json=page([len(starts)]))

    first = asyncio.create_task(make_client(mock_settings, handler).search(query="a"))
    await limited.wait()
    # Another pagination of the same process asks after the 429: it waits for the pause too.
    second = await make_client(mock_settings, handler).search(query="b")
    await first

    assert second["ads"]
    assert len(starts) == 3
    assert all(start - rate_limited_at >= 0.18 for start in starts[1:]), starts


@pytest.mark.asyncio
async def test_pacer_pause_delays_a_request_already_waiting_for_its_turn() -> None:
    loop = asyncio.get_running_loop()
    pacer = kufar_client._RequestPacer(1)
    starts: list[float] = []

    async def request() -> None:
        async with pacer.slot(0.1):
            starts.append(loop.time())

    began = loop.time()
    tasks = [asyncio.create_task(request()) for _ in range(2)]
    await asyncio.sleep(0.03)  # the second request now sleeps until its reserved start
    pacer.pause(0.3)
    await asyncio.gather(*tasks)

    assert starts[0] - began < 0.05
    assert starts[1] - began >= 0.29


def test_pacer_waiting_limit_follows_the_request_delay() -> None:
    pacer = kufar_client._RequestPacer(3)
    assert pacer.waiting_limit(0.0) == kufar_client.MAX_PACER_WAITERS
    assert pacer.waiting_limit(0.5) == 60
    assert pacer.waiting_limit(1.0) == 30
    assert pacer.waiting_limit(100.0) == 1
    assert kufar_client._RequestPacer(3, max_waiting=5).waiting_limit(1.0) == 5


@pytest.mark.asyncio
async def test_pacer_queue_is_bounded_and_fails_fast(
    mock_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    backoff_sleeps: list[float],
) -> None:
    monkeypatch.setattr(kufar_client, "MAX_PACER_WAITERS", 2)
    mock_settings.kufar_parallel_semaphore = 1
    release = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await release.wait()
        return httpx.Response(200, json=page([calls]))

    # One request in flight and two waiting fill the queue.
    running = [
        asyncio.create_task(make_client(mock_settings, handler).search(query=f"q{index}"))
        for index in range(3)
    ]
    await asyncio.sleep(0.01)
    pacer = kufar_client._get_pacer(1)
    assert pacer.waiting == 2

    with pytest.raises(kufar_client.KufarBusyError):
        await make_client(mock_settings, handler).search(query="overflow")
    # Rejected at once: no HTTP request and no retry backoff.
    assert calls == 1
    assert backoff_sleeps == []

    # A waiter that gives up (deadline, client disconnect) leaves the queue.
    running[2].cancel()
    await asyncio.sleep(0)
    assert pacer.waiting == 1

    release.set()
    results = await asyncio.gather(*running[:2])
    assert all(result["ads"] for result in results)
    assert pacer.waiting == 0
    assert issubclass(kufar_client.KufarBusyError, KufarAPIError)
