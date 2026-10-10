from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from api.services import parallel_kufar
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.parallel_kufar import parallel_search_all
from api.services.query_pipeline import (
    SEGMENT_TASKS,
    DatasetCache,
    configure_dataset_cache,
    load_segment_datasets,
)


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.kufar_request_delay = 0.0
    settings.kufar_parallel_semaphore = 2
    settings.kufar_timeout = 15.0
    return settings


def test_unused_parallel_search_is_removed() -> None:
    assert not hasattr(parallel_kufar, "parallel_search")


@pytest.mark.asyncio
async def test_parallel_search_all_paginates_every_task(mock_settings: MagicMock) -> None:
    calls: list[dict[str, object]] = []

    async def fake_search_all_ads(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {"ads": [{"ad_id": len(calls)}], "truncated": False}

    async def forbidden_search(**kwargs: object) -> dict:
        raise AssertionError("parallel_search_all must use full pagination")

    client = KufarClient(mock_settings)
    client.search_all_ads = fake_search_all_ads  # type: ignore[method-assign]
    client.search = forbidden_search  # type: ignore[method-assign]

    tasks = [{"query": "test", "condition": f"c{index}"} for index in range(4)]
    results = await parallel_search_all(client, tasks, mock_settings)

    assert len(results) == 4
    assert sorted(call["condition"] for call in calls) == ["c0", "c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_parallel_search_all_keeps_task_order(mock_settings: MagicMock) -> None:
    async def fake_search_all_ads(**kwargs: object) -> dict:
        # Later tasks finish first.
        await asyncio.sleep(0.001 * (4 - int(str(kwargs["condition"]))))
        return {"ads": [], "tag": kwargs["condition"]}

    client = KufarClient(mock_settings)
    client.search_all_ads = fake_search_all_ads  # type: ignore[method-assign]
    tasks = [{"query": "test", "condition": str(index)} for index in range(4)]
    results = await parallel_search_all(client, tasks, mock_settings)
    assert [result["tag"] for result in results] == ["0", "1", "2", "3"]


@pytest.mark.asyncio
async def test_parallel_search_all_respects_semaphore_limit(mock_settings: MagicMock) -> None:
    concurrent_count = 0
    max_concurrent = 0

    async def slow_search_all_ads(**kwargs: object) -> dict:
        del kwargs
        nonlocal concurrent_count, max_concurrent
        concurrent_count += 1
        max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.01)
        concurrent_count -= 1
        return {"ads": []}

    client = KufarClient(mock_settings)
    client.search_all_ads = slow_search_all_ads  # type: ignore[method-assign]
    await parallel_search_all(client, [{"query": "test"} for _ in range(6)], mock_settings)
    assert max_concurrent == 2


@pytest.mark.asyncio
async def test_parallel_search_all_propagates_errors(mock_settings: MagicMock) -> None:
    async def failing_search_all_ads(**kwargs: object) -> dict:
        del kwargs
        raise KufarAPIError("Kufar down")

    client = KufarClient(mock_settings)
    client.search_all_ads = failing_search_all_ads  # type: ignore[method-assign]
    with pytest.raises(KufarAPIError):
        await parallel_search_all(client, [{"query": "test"}], mock_settings)


class SegmentFakeClient:
    calls: list[dict[str, object]] = []

    def __init__(self, settings: object) -> None:
        del settings

    async def search_all_ads(self, **kwargs: object) -> dict:
        SegmentFakeClient.calls.append(kwargs)
        return {
            "ads": [
                {"ad_id": 1, "subject": "iphone", "price_byn": 200000},
                # Segment stats only need prices, so an ad without an id still counts.
                {"subject": "iphone", "price_byn": "250000"},
                {"ad_id": 3, "subject": "iphone", "price_byn": "n/a", "images": None},
            ]
        }

    async def aclose(self) -> None:
        return None


async def _load_segments(settings: MagicMock) -> dict:
    return await load_segment_datasets(
        query="iphone",
        currency="USD",
        strict_search=False,
        settings=settings,
        client_factory=SegmentFakeClient,
        parallel_search=parallel_search_all,
    )


@pytest.mark.asyncio
async def test_load_segment_datasets_runs_all_segments(mock_settings: MagicMock) -> None:
    SegmentFakeClient.calls = []
    datasets = await _load_segments(mock_settings)

    assert set(datasets) == {name for name, _ in SEGMENT_TASKS}
    assert len(SegmentFakeClient.calls) == 4
    assert {call["currency"] for call in SegmentFakeClient.calls} == {"BYN"}
    dataset = datasets["new_private"]
    assert dataset.currency == "USD"
    assert dataset.price_stats.count == 2
    assert dataset.total_results == 3
    assert [ad["ad_id"] for ad in dataset.ads] == [1, 3]
    assert dataset.ads[1]["images"] == []


@pytest.mark.asyncio
async def test_load_segment_datasets_uses_dataset_cache(mock_settings: MagicMock) -> None:
    SegmentFakeClient.calls = []
    configure_dataset_cache(DatasetCache())
    try:
        await _load_segments(mock_settings)
        await _load_segments(mock_settings)
    finally:
        configure_dataset_cache(None)
    assert len(SegmentFakeClient.calls) == 4
