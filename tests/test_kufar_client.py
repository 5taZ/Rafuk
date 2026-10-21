from __future__ import annotations

import asyncio
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import api.services.kufar_client as kufar_client_module
from api.services.kufar_client import (
    KUFAR_BASE_URL,
    RETRY_AFTER_MAX_SECONDS,
    KufarAPIError,
    KufarClient,
    KufarUnavailableError,
    _retry_after_seconds,
)

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
    """Once the breaker is open the per-call check fails fast (RV-BE-05:
    with KufarUnavailableError; it used to return an empty stub). A clean
    response after the cooldown should also bring the consecutive-error
    counter back to 0 atomically (so a later transient failure isn't
    already at the threshold from the previous storm)."""
    mock_settings.kufar_request_delay = 0.0
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        client = KufarClient(mock_settings)
        # Manually simulate a partial-storm leftover (not enough to open)
        async with client._circuit_lock:
            client._consecutive_errors = 4
        await client.search(query="x")
        # Successful call must reset the counter.
        assert client._consecutive_errors == 0


# ---------------------------------------------------------------------------
# RV-BE-05: the breaker raises instead of returning an empty stub, and the
# retry policy distinguishes our mistakes (4xx) from Kufar's (5xx/408/429).
# ---------------------------------------------------------------------------


def _http_response(
    status: int,
    *,
    json_body: object | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", KUFAR_BASE_URL)
    if text is not None:
        return httpx.Response(status, text=text, headers=headers, request=request)
    return httpx.Response(
        status,
        json=SAMPLE_RESPONSE if json_body is None else json_body,
        headers=headers,
        request=request,
    )


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry back-off delays instead of actually sleeping."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(kufar_client_module.asyncio, "sleep", fake_sleep)
    return delays


def _open_breaker(client: KufarClient, *, remaining: float) -> None:
    client._circuit_state = "open"
    client._circuit_open_until = asyncio.get_running_loop().time() + remaining


@pytest.mark.asyncio
async def test_open_breaker_raises_unavailable_instead_of_empty_stub(
    mock_settings: MagicMock, recorded_sleeps: list[float],
) -> None:
    client = KufarClient(mock_settings)
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_http_response(503, json_body={}),
    ) as get_mock:
        # 3 failed attempts, then 2 more trip the breaker (threshold 5).
        with pytest.raises(KufarAPIError, match="after 3 attempts") as first_error:
            await client.search(query="iphone")
        assert first_error.value.retry_after is None
        with pytest.raises(KufarAPIError, match="after 2 attempts") as tripping_error:
            await client.search(query="iphone")
        assert client._circuit_state == "open"
        # The call that tripped the breaker already tells the API when to retry.
        assert tripping_error.value.retry_after > 0
        calls_before = get_mock.await_count

        with pytest.raises(KufarUnavailableError) as excinfo:
            await client.search(query="iphone")

    # Fail fast: no request reaches Kufar, and no fake {"ads": []} result.
    assert get_mock.await_count == calls_before == 5
    assert isinstance(excinfo.value, KufarAPIError)
    assert 0 < excinfo.value.retry_after <= kufar_client_module.CIRCUIT_OPEN_SECONDS


@pytest.mark.asyncio
async def test_search_all_ads_raises_when_breaker_opens_mid_pagination(
    mock_settings: MagicMock,
) -> None:
    """The open breaker used to answer the next page with the empty stub;
    pagination stopped there and the short result was cached as complete."""
    client = KufarClient(mock_settings)
    original_search = client.search
    pages_requested = 0

    async def search_then_trip(**kwargs):
        nonlocal pages_requested
        pages_requested += 1
        result = await original_search(**kwargs)
        if pages_requested == 1:
            _open_breaker(client, remaining=30)
        return result

    client.search = search_then_trip  # type: ignore[method-assign]
    first_page = _http_response(
        200,
        json_body={
            "ads": [{"ad_id": 1}],
            "pagination": {"pages": [{"token": "cursor-2"}]},
            "total": 400,
        },
    )
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=first_page) as get,
        pytest.raises(KufarUnavailableError),
    ):
        await client.search_all_ads(query="iphone")
    assert get.await_count == 1
    assert pages_requested == 2


@pytest.mark.asyncio
async def test_half_open_probe_is_exclusive_and_closes_breaker_on_success(
    mock_settings: MagicMock,
) -> None:
    client = KufarClient(mock_settings)
    _open_breaker(client, remaining=-1)  # window already expired
    release_probe = asyncio.Event()

    async def slow_get(*args, **kwargs):
        await release_probe.wait()
        return _http_response(200)

    with patch("httpx.AsyncClient.get", side_effect=slow_get):
        probe = asyncio.create_task(client.search(query="probe"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert client._circuit_state == "half_open"
        # While the probe is in flight everybody else fails fast.
        with pytest.raises(KufarUnavailableError):
            await client.search(query="concurrent")
        release_probe.set()
        result = await probe

    assert result["ads"][0]["ad_id"] == 12345
    assert client._circuit_state == "closed"
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
async def test_failed_probe_reopens_breaker_without_retrying(
    mock_settings: MagicMock, recorded_sleeps: list[float],
) -> None:
    client = KufarClient(mock_settings)
    client._consecutive_errors = 5
    _open_breaker(client, remaining=-1)
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_http_response(502, json_body={}),
        ) as get_mock,
        pytest.raises(KufarAPIError) as excinfo,
    ):
        await client.search(query="probe")

    assert not isinstance(excinfo.value, KufarUnavailableError)
    assert get_mock.await_count == 1
    assert client._circuit_state == "open"
    assert client._open_remaining() > 0
    assert recorded_sleeps == []


@pytest.mark.asyncio
async def test_cancelled_probe_does_not_wedge_breaker_in_half_open(
    mock_settings: MagicMock,
) -> None:
    """The AI-02 failure mode: a probe that never reports back would leave
    half_open forever and every later call would fail fast."""
    client = KufarClient(mock_settings)
    _open_breaker(client, remaining=-1)
    never = asyncio.Event()

    async def hanging_get(*args, **kwargs):
        await never.wait()

    with patch("httpx.AsyncClient.get", side_effect=hanging_get):
        probe = asyncio.create_task(client.search(query="probe"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert client._circuit_state == "half_open"
        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probe

    assert client._circuit_state == "open"
    assert client._open_remaining() == 0
    # The next caller becomes a fresh probe and can close the breaker.
    with patch(
        "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=_http_response(200),
    ):
        await client.search(query="next")
    assert client._circuit_state == "closed"


@pytest.mark.asyncio
async def test_client_errors_are_neither_retried_nor_counted(
    mock_settings: MagicMock, recorded_sleeps: list[float],
) -> None:
    """A 4xx is about our request: retrying can't fix it, and counting it
    would let one user's malformed queries open the breaker for everyone."""
    client = KufarClient(mock_settings)
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_http_response(404, json_body={}),
    ) as get_mock:
        for _ in range(10):
            with pytest.raises(KufarAPIError, match="HTTP 404"):
                await client.search(query="bad")

    assert get_mock.await_count == 10
    assert recorded_sleeps == []
    assert client._consecutive_errors == 0
    assert client._circuit_state == "closed"


@pytest.mark.asyncio
async def test_client_error_settles_half_open_probe(mock_settings: MagicMock) -> None:
    client = KufarClient(mock_settings)
    client._consecutive_errors = 5
    _open_breaker(client, remaining=-1)
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_http_response(400, json_body={}),
        ),
        pytest.raises(KufarAPIError, match="HTTP 400"),
    ):
        await client.search(query="probe")
    # Kufar answered, so it is reachable: the breaker must not stay half-open.
    assert client._circuit_state == "closed"
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
async def test_timeout_and_rate_limit_statuses_are_retried(
    mock_settings: MagicMock, recorded_sleeps: list[float],
) -> None:
    client = KufarClient(mock_settings)
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=[
            _http_response(429, json_body={}),
            _http_response(408, json_body={}),
            _http_response(200),
        ],
    ) as get_mock:
        result = await client.search(query="iphone")

    assert result["ads"][0]["ad_id"] == 12345
    assert get_mock.await_count == 3
    assert recorded_sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_retry_after_header_is_honoured(
    mock_settings: MagicMock, recorded_sleeps: list[float],
) -> None:
    client = KufarClient(mock_settings)
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=[
            _http_response(429, json_body={}, headers={"Retry-After": "7"}),
            _http_response(200),
        ],
    ):
        await client.search(query="iphone")

    # The server's pause wins over the 1 s exponential back-off.
    assert recorded_sleeps == [7.0]


@pytest.mark.asyncio
async def test_retry_after_beyond_cap_fails_fast(
    mock_settings: MagicMock, recorded_sleeps: list[float],
) -> None:
    client = KufarClient(mock_settings)
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_http_response(429, json_body={}, headers={"Retry-After": "120"}),
        ) as get_mock,
        pytest.raises(KufarAPIError) as excinfo,
    ):
        await client.search(query="iphone")

    assert get_mock.await_count == 1
    assert recorded_sleeps == []
    assert excinfo.value.retry_after == 120
    assert client._consecutive_errors == 1


def test_retry_after_parsing_supports_seconds_and_http_dates() -> None:
    from datetime import UTC, datetime, timedelta

    assert _retry_after_seconds(_http_response(429, headers={"Retry-After": "3"})) == 3.0
    assert _retry_after_seconds(_http_response(429, headers={"Retry-After": "-5"})) == 0.0
    assert _retry_after_seconds(_http_response(429, headers={"Retry-After": "soon"})) is None
    assert _retry_after_seconds(_http_response(429)) is None
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    parsed = _retry_after_seconds(_http_response(503, headers={"Retry-After": when}))
    assert parsed is not None
    assert 25 <= parsed <= 31
    assert parsed > RETRY_AFTER_MAX_SECONDS  # i.e. this pause would fail fast


@pytest.mark.asyncio
async def test_non_json_body_is_an_upstream_failure(
    mock_settings: MagicMock, recorded_sleeps: list[float],
) -> None:
    """An HTML anti-bot page with 200 used to escape as a bare ValueError
    (500 in the API, an unhandled error in the scheduler tick)."""
    client = KufarClient(mock_settings)
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_http_response(200, text="<html>checking your browser</html>"),
        ),
        pytest.raises(KufarAPIError, match="after 3 attempts"),
    ):
        await client.search(query="iphone")
    assert client._consecutive_errors == 3


@pytest.mark.asyncio
async def test_retries_stop_once_the_breaker_opens_elsewhere(
    mock_settings: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = KufarClient(mock_settings)

    async def sleep_while_others_fail(delay: float) -> None:
        del delay
        _open_breaker(client, remaining=30)

    monkeypatch.setattr(kufar_client_module.asyncio, "sleep", sleep_while_others_fail)
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_http_response(503, json_body={}),
        ) as get_mock,
        pytest.raises(KufarUnavailableError),
    ):
        await client.search(query="iphone")
    assert get_mock.await_count == 1
