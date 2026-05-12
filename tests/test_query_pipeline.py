from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.metrics import _reset_metrics_for_tests, render_prometheus_metrics
from api.services.cache import MemoryCache
from api.services.query_pipeline import (
    _CATEGORY_TOTAL_MAX_CALLS,
    _dataset_cache_key,
    _normalize_response_ads,
    fetch_category_totals,
    load_query_dataset,
)


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
