from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.parallel_kufar import parallel_search


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.kufar_request_delay = 0.0
    settings.kufar_parallel_semaphore = 2
    settings.kufar_timeout = 15.0
    return settings


@pytest.mark.asyncio
async def test_parallel_search_returns_results_for_all_tasks(mock_settings: MagicMock) -> None:
    call_count = 0

    async def fake_search(**kwargs: object) -> dict:
        del kwargs
        nonlocal call_count
        call_count += 1
        return {"ads": [{"ad_id": call_count}], "pagination": {}}

    client = KufarClient(mock_settings)
    client.search = fake_search  # type: ignore[method-assign]

    tasks = [{"query": "test"} for _ in range(4)]
    results = await parallel_search(client, tasks, mock_settings)
    assert len(results) == 4
    assert call_count == 4


@pytest.mark.asyncio
async def test_parallel_search_respects_semaphore_limit(mock_settings: MagicMock) -> None:
    """parallel_search no longer wraps calls in its own semaphore
    (PERF-H7) — concurrency is now bounded by KufarClient's own
    semaphore. Patch the inner http GET so the work still goes through
    `client._semaphore`, then count concurrent enters."""
    concurrent_count = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    from unittest.mock import AsyncMock, patch

    async def slow_get(*args: object, **kwargs: object) -> object:
        del args, kwargs
        nonlocal concurrent_count, max_concurrent
        async with lock:
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.02)
        async with lock:
            concurrent_count -= 1
        # Build a minimal httpx-like response stub.
        resp = MagicMock()
        resp.json = MagicMock(return_value={"ads": [], "pagination": {}, "total": 0})
        resp.raise_for_status = MagicMock()
        return resp

    client = KufarClient(mock_settings)
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=slow_get)):
        await parallel_search(
            client, [{"query": "test"} for _ in range(6)], mock_settings,
        )
    # mock_settings.kufar_parallel_semaphore = 2 — the in-client
    # semaphore must keep concurrent calls at or below that.
    assert max_concurrent <= 2, (
        f"client-level semaphore should cap concurrency at 2; saw {max_concurrent}"
    )


@pytest.mark.asyncio
async def test_parallel_search_returns_empty_on_failure(mock_settings: MagicMock) -> None:
    async def failing_search(**kwargs: object) -> dict:
        del kwargs
        raise KufarAPIError("Kufar down")

    client = KufarClient(mock_settings)
    client.search = failing_search  # type: ignore[method-assign]
    results = await parallel_search(client, [{"query": "test"}], mock_settings)
    assert len(results) == 1
    assert results[0]["ads"] == []
    assert results[0]["total"] == 0


@pytest.mark.asyncio
async def test_parallel_search_preserves_list_length_on_partial_failure(
    mock_settings: MagicMock,
) -> None:
    call_idx = 0

    async def flaky_search(**kwargs: object) -> dict:
        del kwargs
        nonlocal call_idx
        call_idx += 1
        if call_idx == 2:
            raise KufarAPIError("timeout")
        return {"ads": [{"ad_id": call_idx}], "pagination": {}, "total": 1}

    client = KufarClient(mock_settings)
    client.search = flaky_search  # type: ignore[method-assign]
    results = await parallel_search(
        client,
        [{"query": "a"}, {"query": "b"}, {"query": "c"}],
        mock_settings,
    )
    assert len(results) == 3
    assert results[0]["ads"] == [{"ad_id": 1}]
    assert results[1]["ads"] == []  # failed task → empty response
    assert results[2]["ads"] == [{"ad_id": 3}]
