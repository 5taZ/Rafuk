from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from api.services import kufar_client
from api.services.kufar_client import KufarClient
from api.services.parallel_kufar import parallel_search_all
from api.services.query_pipeline import (
    SEGMENT_TASKS,
    DatasetCache,
    QueryDataset,
    configure_dataset_cache,
    dataset_cache_key,
    load_query_dataset,
    load_segment_datasets,
    normalize_cache_query,
)


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.kufar_request_delay = 0.0
    settings.kufar_parallel_semaphore = 3
    settings.kufar_timeout = 15.0
    return settings


@pytest.fixture
def dataset_cache() -> DatasetCache:
    # conftest disables the process-wide dataset cache again after every test.
    cache = DatasetCache()
    configure_dataset_cache(cache)
    return cache


class RecordingClient:
    calls: list[dict[str, Any]] = []
    response: dict[str, Any] = {}

    def __init__(self, settings: object) -> None:
        del settings

    async def search_all_ads(self, **kwargs: Any) -> dict[str, Any]:
        RecordingClient.calls.append(kwargs)
        await asyncio.sleep(0)
        return RecordingClient.response

    async def aclose(self) -> None:
        return None


@pytest.fixture
def recording_client() -> type[RecordingClient]:
    RecordingClient.calls = []
    RecordingClient.response = {
        "ads": [
            {"ad_id": 1, "subject": "iPhone 15", "price_byn": 200000},
            {"ad_id": 2, "subject": "iPhone 15 Pro", "price_byn": 300000},
            {"ad_id": 3, "subject": "iPhone 14", "price_byn": 150000},
        ],
        "total": 3,
        "truncated": False,
    }
    return RecordingClient


async def _load(
    client: type[RecordingClient] | type[KufarClient],
    settings: MagicMock,
    query: str = "iphone 15",
    *,
    strict: bool = False,
    **search_kwargs: Any,
) -> QueryDataset:
    return await load_query_dataset(
        query=query,
        currency="BYN",
        strict_search=strict,
        settings=settings,
        client_factory=client,
        search_kwargs=search_kwargs or None,
    )


# --- total_results and truncated --------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncated_broad_dataset_reports_kufar_total(
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
) -> None:
    recording_client.response = {**recording_client.response, "total": 20000, "truncated": True}
    dataset = await _load(recording_client, mock_settings)

    assert dataset.truncated is True
    assert dataset.kufar_total == 20000
    assert dataset.fetched_count == 3
    # The market size is Kufar's count, not the size of the fetched sample.
    assert dataset.total_results == 20000
    assert dataset.price_stats.count == 3


@pytest.mark.asyncio
async def test_truncated_strict_dataset_reports_fetched_count(
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
) -> None:
    recording_client.response = {**recording_client.response, "total": 20000, "truncated": True}
    dataset = await _load(recording_client, mock_settings, strict=True)

    # Kufar's total counts unfiltered results, which strict mode does not show.
    assert dataset.truncated is True
    assert [ad["ad_id"] for ad in dataset.ads] == [1]
    assert dataset.total_results == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total", "truncated", "expected"),
    [(3, False, 3), (2, True, 3), (None, True, 3), (50, False, 3), ("bad", True, 3)],
)
async def test_total_results_falls_back_to_fetched_count(
    total: Any,
    truncated: bool,
    expected: int,
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
) -> None:
    recording_client.response = {
        **recording_client.response,
        "total": total,
        "truncated": truncated,
    }
    dataset = await _load(recording_client, mock_settings)
    assert dataset.truncated is truncated
    assert dataset.total_results == expected


@pytest.mark.asyncio
async def test_failed_later_page_gives_truncated_dataset(
    mock_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr(kufar_client, "_backoff_sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") is None:
            return httpx.Response(
                200,
                json={
                    "ads": [
                        {"ad_id": 1, "subject": "iphone", "price_byn": 100000},
                        {"ad_id": 2, "subject": "iphone", "price_byn": 200000},
                    ],
                    "total": 900,
                    "pagination": {"pages": [{"label": "next", "token": "c2"}]},
                },
            )
        return httpx.Response(503)

    class MockTransportClient(KufarClient):
        def __init__(self, settings: Any) -> None:
            super().__init__(
                settings,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            )

    dataset = await _load(MockTransportClient, mock_settings, "iphone")
    assert [ad["ad_id"] for ad in dataset.ads] == [1, 2]
    assert dataset.truncated is True
    assert dataset.total_results == 900


# --- Forwarded pagination budget ------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_query_dataset_forwards_pagination_budget(
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
) -> None:
    await _load(recording_client, mock_settings, deadline_seconds=12.5, max_pages=4)
    (call,) = recording_client.calls
    assert call["deadline_seconds"] == 12.5
    assert call["max_pages"] == 4
    assert call["currency"] == "BYN"


@pytest.mark.asyncio
async def test_dataset_cache_key_ignores_deadline_but_not_max_pages(
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
    dataset_cache: DatasetCache,
) -> None:
    await _load(recording_client, mock_settings, deadline_seconds=5)
    await _load(recording_client, mock_settings, deadline_seconds=30)
    await _load(recording_client, mock_settings)
    assert len(recording_client.calls) == 1

    await _load(recording_client, mock_settings, max_pages=2)
    assert len(recording_client.calls) == 2


@pytest.mark.asyncio
async def test_load_segment_datasets_forwards_pagination_budget(
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
    dataset_cache: DatasetCache,
) -> None:
    async def load(**search_kwargs: Any) -> dict[str, QueryDataset]:
        return await load_segment_datasets(
            query="iphone 15",
            currency="BYN",
            strict_search=False,
            settings=mock_settings,
            client_factory=recording_client,
            parallel_search=parallel_search_all,
            search_kwargs=search_kwargs,
        )

    datasets = await load(deadline_seconds=8.0)
    assert set(datasets) == {name for name, _ in SEGMENT_TASKS}
    assert len(recording_client.calls) == 4
    assert {call["deadline_seconds"] for call in recording_client.calls} == {8.0}
    assert {call["condition"] for call in recording_client.calls} == {"new", "used"}

    # Same segments with another time budget: served from the cache.
    await load(deadline_seconds=20.0)
    assert len(recording_client.calls) == 4


# --- Dataset cache key ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("айфон 15", "iphone 15"),
        ("PlayStation 5", "ps5"),
        ("ssd 1tb", "ssd 1024"),
        ("iphone-15 128gb", "iphone 15 128"),
        ("ёлка", "лка"),
        ("!!!", "ё"),
    ],
)
def test_dataset_cache_key_keeps_different_kufar_queries_apart(first: str, second: str) -> None:
    assert dataset_cache_key(first, False, {}) != dataset_cache_key(second, False, {})


def test_dataset_cache_key_light_normalization() -> None:
    base = dataset_cache_key("iPhone 15", False, {})
    assert dataset_cache_key("  IPHONE\t 15 ", False, {}) == base
    assert dataset_cache_key("iphone 15", True, {}) == base
    assert dataset_cache_key("iphone 15", False, {"deadline_seconds": 3}) == base
    assert dataset_cache_key("iphone 15", False, {"condition": "new"}) != base
    assert normalize_cache_query("  Ёлка   Б/У ") == "ёлка б/у"


@pytest.mark.asyncio
async def test_alias_equivalent_queries_are_fetched_separately(
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
    dataset_cache: DatasetCache,
) -> None:
    await _load(recording_client, mock_settings, "ssd 1tb")
    await _load(recording_client, mock_settings, "ssd 1024")
    assert [call["query"] for call in recording_client.calls] == ["ssd 1tb", "ssd 1024"]


@pytest.mark.asyncio
async def test_truncated_dataset_is_cached_for_a_shorter_time(
    mock_settings: MagicMock,
    recording_client: type[RecordingClient],
) -> None:
    clock_now = [1000.0]
    cache = DatasetCache(ttl_seconds=120, truncated_ttl_seconds=30, clock=lambda: clock_now[0])
    configure_dataset_cache(cache)
    recording_client.response = {**recording_client.response, "truncated": True}

    await _load(recording_client, mock_settings)
    clock_now[0] += 31
    await _load(recording_client, mock_settings)
    assert len(recording_client.calls) == 2
