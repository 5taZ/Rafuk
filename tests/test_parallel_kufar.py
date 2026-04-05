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
    concurrent_count = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def slow_search(**kwargs: object) -> dict:
        del kwargs
        nonlocal concurrent_count, max_concurrent
        async with lock:
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.01)
        async with lock:
            concurrent_count -= 1
        return {"ads": [], "pagination": {}}

    client = KufarClient(mock_settings)
    client.search = slow_search  # type: ignore[method-assign]
    await parallel_search(client, [{"query": "test"} for _ in range(6)], mock_settings)
    assert max_concurrent <= 2


@pytest.mark.asyncio
async def test_parallel_search_propagates_errors(mock_settings: MagicMock) -> None:
    async def failing_search(**kwargs: object) -> dict:
        del kwargs
        raise KufarAPIError("Kufar down")

    client = KufarClient(mock_settings)
    client.search = failing_search  # type: ignore[method-assign]
    with pytest.raises(KufarAPIError):
        await parallel_search(client, [{"query": "test"}], mock_settings)
