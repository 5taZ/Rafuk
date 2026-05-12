from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import api.services.query_pipeline as query_pipeline
from api.metrics import _reset_metrics_for_tests, render_prometheus_metrics
from api.services.cache import MemoryCache
from api.services.query_pipeline import (
    _CATEGORY_TOTAL_MAX_CALLS,
    _dataset_cache_key,
    _normalize_response_ads,
    fetch_category_totals,
    load_query_dataset,
)


class _FakeDistributedCache(MemoryCache):
    def __init__(self, *, acquire_result: bool | None) -> None:
        super().__init__()
        self.acquire_result = acquire_result
        self.acquire_calls: list[tuple[str, str, int]] = []
        self.release_calls: list[tuple[str, str]] = []

    async def try_acquire_lock(self, key: str, token: str, *, ttl_seconds: int) -> bool | None:
        self.acquire_calls.append((key, token, ttl_seconds))
        return self.acquire_result

    async def release_lock(self, key: str, token: str) -> None:
        self.release_calls.append((key, token))


def test_normalize_response_ads_keeps_real_kufar_minor_units() -> None:
    response = {
        "ads": [
            {"price_byn": 400, "price_usd": 142},
            {"price_byn": 1200, "price_usd": 426},
            {"price_byn": 8000, "price_usd": 2838},
        ]
    }

    normalized = _normalize_response_ads(response)

    assert [ad["price_byn"] for ad in normalized["ads"]] == [400, 1200, 8000]


def test_normalize_response_ads_supports_direct_byn_test_payloads() -> None:
    response = {
        "ads": [
            {"price_byn": 4},
            {"price_byn": 12},
            {"price_byn": 80},
        ]
    }

    normalized = _normalize_response_ads(response)

    assert [ad["price_byn"] for ad in normalized["ads"]] == [400, 1200, 8000]


def test_dataset_cache_key_hashes_canonical_tuple() -> None:
    key_a = _dataset_cache_key(
        "foo:cur=USD",
        "BYN",
        None,
        {"region": "Минск"},
    )
    key_b = _dataset_cache_key(
        "foo",
        "cur=USD:cat=:region=Минск",
        None,
        {},
    )

    assert key_a.startswith("kufar:dataset:")
    assert len(key_a.removeprefix("kufar:dataset:")) == 64
    assert key_a != key_b


@pytest.mark.asyncio
async def test_fetch_category_totals_uses_client_delay_path() -> None:
    seen_kwargs: list[dict] = []

    class Client:
        async def search(self, **kwargs) -> dict:
            seen_kwargs.append(kwargs)
            return {"ads": [{"subject": "iphone", "price_byn": 100}], "total": 1}

    result = await fetch_category_totals(
        query="iphone",
        currency="BYN",
        strict_search=False,
        client=Client(),
        category_ids=[1, 2],
    )

    assert result == {1: 1, 2: 1}
    assert seen_kwargs
    assert all("bypass_delay" not in kwargs for kwargs in seen_kwargs)


@pytest.mark.asyncio
async def test_fetch_category_totals_caps_cold_fanout_calls() -> None:
    seen_category_ids: list[int] = []

    class Client:
        async def search(self, **kwargs) -> dict:
            seen_category_ids.append(int(kwargs["category"]))
            return {"ads": [{"subject": "iphone", "price_byn": 100}], "total": 1}

    await fetch_category_totals(
        query="iphone",
        currency="BYN",
        strict_search=False,
        client=Client(),
        category_ids=list(range(_CATEGORY_TOTAL_MAX_CALLS + 5)),
    )

    assert seen_category_ids == list(range(_CATEGORY_TOTAL_MAX_CALLS))


@pytest.mark.asyncio
async def test_fetch_category_totals_mirrors_listing_count_below_filtered_cap() -> None:
    class Client:
        async def search(self, **kwargs) -> dict:
            del kwargs
            matching = [
                {"subject": f"Volkswagen Polo {i}", "price_byn": 100}
                for i in range(175)
            ]
            noisy = [
                {"subject": f"Volkswagen Golf {i}", "price_byn": 100}
                for i in range(25)
            ]
            return {"ads": [*matching, *noisy], "total": 236}

    result = await fetch_category_totals(
        query="Volkswagen Polo",
        currency="BYN",
        strict_search=True,
        client=Client(),
        category_ids=[2010],
    )

    assert result == {2010: 175}


@pytest.mark.asyncio
async def test_load_query_dataset_records_cache_miss_hit_and_fetch_metrics() -> None:
    _reset_metrics_for_tests()
    cache = MemoryCache()
    fetch_calls = 0

    class Client:
        async def search_all_ads(self, **kwargs) -> dict:
            nonlocal fetch_calls
            fetch_calls += 1
            return {"ads": [{"subject": kwargs["query"], "price_byn": 100}], "total": 1}

    settings = SimpleNamespace(cache_ttl_seconds=300)
    client = Client()

    await load_query_dataset(
        query="iphone",
        currency="BYN",
        strict_search=False,
        settings=settings,
        client=client,
        cache=cache,
    )
    await load_query_dataset(
        query="iphone",
        currency="BYN",
        strict_search=False,
        settings=settings,
        client=client,
        cache=cache,
    )

    text = render_prometheus_metrics()

    assert fetch_calls == 1
    assert 'kufar_query_dataset_events_total{event="cache_miss"} 1' in text
    assert 'kufar_query_dataset_events_total{event="cache_hit"} 1' in text
    assert 'kufar_query_dataset_events_total{event="singleflight_owner"} 1' in text
    assert (
        'kufar_query_dataset_upstream_fetch_duration_seconds_count{status="success"} 1'
        in text
    )


@pytest.mark.asyncio
async def test_load_query_dataset_distributed_owner_fetches_and_releases_lock() -> None:
    _reset_metrics_for_tests()
    cache = _FakeDistributedCache(acquire_result=True)
    fetch_calls = 0

    class Client:
        async def search_all_ads(self, **kwargs) -> dict:
            nonlocal fetch_calls
            fetch_calls += 1
            return {"ads": [{"subject": kwargs["query"], "price_byn": 100}], "total": 1}

    dataset = await load_query_dataset(
        query="iphone",
        currency="BYN",
        strict_search=False,
        settings=SimpleNamespace(cache_ttl_seconds=300),
        client=Client(),
        cache=cache,
    )

    text = render_prometheus_metrics()

    assert fetch_calls == 1
    assert dataset.ads[0]["subject"] == "iphone"
    assert len(cache.acquire_calls) == 1
    assert cache.acquire_calls[0][2] == 30
    assert cache.release_calls == [
        (cache.acquire_calls[0][0], cache.acquire_calls[0][1])
    ]
    assert 'kufar_query_dataset_events_total{event="distributed_singleflight_owner"} 1' in text


@pytest.mark.asyncio
async def test_load_query_dataset_distributed_follower_waits_for_cached_owner_result(
    monkeypatch,
) -> None:
    _reset_metrics_for_tests()
    monkeypatch.setattr(query_pipeline, "_DISTRIBUTED_SINGLEFLIGHT_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(query_pipeline, "_DISTRIBUTED_SINGLEFLIGHT_POLL_SECONDS", 0.01)
    cache = _FakeDistributedCache(acquire_result=False)
    sf_key = _dataset_cache_key("iphone", "BYN", None, {})

    class Client:
        async def search_all_ads(self, **kwargs) -> dict:
            raise AssertionError(f"follower should not fetch upstream: {kwargs}")

    async def seed_cache_from_other_worker() -> None:
        await asyncio.sleep(0.03)
        await cache.set_json(
            sf_key,
            {"ads": [{"subject": "iphone", "price_byn": 100}], "total": 1},
            ttl=300,
        )

    seed_task = asyncio.create_task(seed_cache_from_other_worker())
    try:
        dataset = await load_query_dataset(
            query="iphone",
            currency="BYN",
            strict_search=False,
            settings=SimpleNamespace(cache_ttl_seconds=300),
            client=Client(),
            cache=cache,
        )
    finally:
        await seed_task

    text = render_prometheus_metrics()

    assert dataset.ads[0]["subject"] == "iphone"
    assert len(cache.acquire_calls) == 1
    assert not cache.release_calls
    assert 'kufar_query_dataset_events_total{event="distributed_singleflight_wait"} 1' in text
    assert (
        'kufar_query_dataset_events_total{event="distributed_singleflight_cache_hit"} 1'
        in text
    )


@pytest.mark.asyncio
async def test_load_query_dataset_distributed_wait_timeout_falls_back_to_fetch(
    monkeypatch,
) -> None:
    _reset_metrics_for_tests()
    monkeypatch.setattr(query_pipeline, "_DISTRIBUTED_SINGLEFLIGHT_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(query_pipeline, "_DISTRIBUTED_SINGLEFLIGHT_POLL_SECONDS", 0.001)
    cache = _FakeDistributedCache(acquire_result=False)
    fetch_calls = 0

    class Client:
        async def search_all_ads(self, **kwargs) -> dict:
            nonlocal fetch_calls
            fetch_calls += 1
            return {"ads": [{"subject": kwargs["query"], "price_byn": 100}], "total": 1}

    dataset = await load_query_dataset(
        query="iphone",
        currency="BYN",
        strict_search=False,
        settings=SimpleNamespace(cache_ttl_seconds=300),
        client=Client(),
        cache=cache,
    )

    text = render_prometheus_metrics()

    assert fetch_calls == 1
    assert dataset.total_results == 1
    assert 'kufar_query_dataset_events_total{event="distributed_singleflight_timeout"} 1' in text
