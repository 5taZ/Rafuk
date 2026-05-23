from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.services.kufar_client import KufarAPIError, KufarClient

SAMPLE_RESPONSE = {
    "ads": [
        {
            "ad_id": 12345,
            "subject": "iPhone 15 Pro 256GB",
            "price_byn": 3999,
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
    settings.kufar_max_ads_per_query = 5000
    return settings


@pytest.fixture
def ok_response() -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = SAMPLE_RESPONSE
    response.raise_for_status = MagicMock()
    return response


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
    assert params["cur"] == "USD"
    assert params["rgn"] == 6


@pytest.mark.asyncio
async def test_search_sends_supported_native_filter_params(
    ok_response: MagicMock,
    mock_settings: MagicMock,
) -> None:
    # SEARCH-6: `price_range` is the already-formatted kopeck string
    # produced by `kufar_filters.kufar_price_range`. The client just
    # forwards it onto `prc=` — the BYN→kopecks conversion happens
    # one layer up so callers can't accidentally undo it here.
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response) as mock:
        await KufarClient(mock_settings).search(
            query="iphone",
            area=24,
            condition="used",
            seller_type="private",
            price_range="r:50000,100000",
        )
    params = mock.call_args.kwargs.get("params", {})
    assert params["ar"] == 24
    assert params["cnd"] == "1"
    assert params["cmp"] == "0"
    assert params["prc"] == "r:50000,100000"


@pytest.mark.asyncio
async def test_search_raises_kufar_api_error_after_retries(mock_settings: MagicMock) -> None:
    bad_response = MagicMock(spec=httpx.Response)
    bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error",
        request=MagicMock(),
        response=bad_response,
    )
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=bad_response),
        pytest.raises(KufarAPIError, match="after 3 attempts"),
    ):
        await KufarClient(mock_settings).search(query="test")


@pytest.mark.asyncio
async def test_search_retries_on_failure_then_succeeds(
    ok_response: MagicMock,
    mock_settings: MagicMock,
) -> None:
    bad_response = MagicMock(spec=httpx.Response)
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


@pytest.mark.asyncio
async def test_search_parses_next_cursor(ok_response: MagicMock, mock_settings: MagicMock) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        result = await KufarClient(mock_settings).search(query="test")
    assert KufarClient.extract_next_cursor(result) == "next_cursor_token"


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
    assert [ad["ad_id"] for ad in result["ads"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_concurrent_search_respects_request_delay(
    ok_response: MagicMock, mock_settings: MagicMock
) -> None:
    """Two concurrent search() calls used to race on _last_request_time:
    both could observe the previous timestamp, both decide they didn't
    need to wait, and fire requests inside the configured rate-limit
    window. The asyncio.Lock around _enforce_delay serialises the
    timestamp dance, so a second concurrent call always sleeps for
    (close to) `kufar_request_delay` after the first."""
    import asyncio
    import time

    mock_settings.kufar_request_delay = 0.05  # 50ms — measurable but quick
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        client = KufarClient(mock_settings)
        start = time.monotonic()
        await asyncio.gather(
            client.search(query="a"),
            client.search(query="b"),
            client.search(query="c"),
        )
        elapsed = time.monotonic() - start

    # 3 calls × 50ms spacing = ≥100ms total (first fires immediately,
    # the other two are spaced 50ms apart). Allow 90ms slack for CI.
    assert elapsed >= 0.090, (
        f"concurrent calls completed in {elapsed * 1000:.0f}ms — delay not enforced"
    )


@pytest.mark.asyncio
async def test_circuit_breaker_counter_is_thread_safe(mock_settings: MagicMock) -> None:
    """Concurrent failures + a success used to race on _consecutive_errors:
    the success path could reset the counter to 0 between a failure
    path's read and its compare, so the circuit would never open. With
    the lock, every read-modify-write is atomic and the threshold is
    respected even under heavy concurrency."""
    import asyncio

    failing = MagicMock(spec=httpx.Response)
    failing.status_code = 500
    failing.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=failing,
    ))

    mock_settings.kufar_request_delay = 0.0  # no spacing
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=failing):
        client = KufarClient(mock_settings)

        async def one_call() -> None:
            with pytest.raises(KufarAPIError):
                await client.search(query="x")

        # Fire 6 failing calls in parallel — each retries up to MAX_RETRIES,
        # so there are well over 5 failure events. With the lock, after
        # this gather() the breaker MUST be open. Without the lock the
        # counter could get clobbered and the breaker silently stay shut.
        await asyncio.gather(*[one_call() for _ in range(6)], return_exceptions=True)

        # The circuit_open_until timestamp lives in the future, ergo open.
        loop = asyncio.get_running_loop()
        assert client._circuit_open_until > loop.time(), (
            "Circuit breaker did not open after >5 concurrent failures — "
            "counter likely raced and never reached the threshold"
        )


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_success(
    ok_response: MagicMock, mock_settings: MagicMock
) -> None:
    """Once the breaker is open the per-call check returns the empty
    stub immediately. A clean response after the cooldown should also
    bring the consecutive-error counter back to 0 atomically (so a
    later transient failure isn't already at the threshold from the
    previous storm)."""
    mock_settings.kufar_request_delay = 0.0
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        client = KufarClient(mock_settings)
        # Manually simulate a partial-storm leftover (not enough to open)
        async with client._circuit_lock:
            client._consecutive_errors = 4
        await client.search(query="x")
        # Successful call must reset the counter.
        assert client._consecutive_errors == 0
