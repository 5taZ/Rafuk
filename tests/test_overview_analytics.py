from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.models import QuerySnapshot
from api.services.aggregator import build_query_key, compute_price_histogram
from api.services.cache import MemoryCache
from tests.conftest import FakeCurrencyService


@pytest.mark.parametrize("prices", [[], [0], [10] * 12, [0, 1, 1, 2], [0.50, 0.51, 0.52],
                                    list(range(201))])
def test_histogram_assigns_each_price_to_exactly_one_bin(prices: list[float]) -> None:
    bins = compute_price_histogram(prices)
    assert sum(bucket["count"] for bucket in bins) == len(prices)
    assert len(bins) <= 10
    for i, bucket in enumerate(bins):
        assert bucket["count"] == sum(
            bucket["from_price"] <= price
            and (price <= bucket["to_price"] if i == len(bins) - 1
                 else price < bucket["to_price"])
            for price in prices
        )


def test_histogram_uses_readable_price_steps() -> None:
    bins = compute_price_histogram([900 + i * 25 for i in range(40)])
    assert all(bucket["from_price"] % 100 == 0 for bucket in bins)


def _app_for_ads(ads: list[dict]):
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app

    class Client:
        async def search_all_ads(self, **kwargs):
            return {"ads": list(ads), "total": len(ads)}

        async def aclose(self):
            pass

    app = create_app()
    cache = MemoryCache()
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_kufar_client] = lambda: Client()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService(rates={"USD": 2})
    return app


def _ad(price: float, condition: str = "used", *, shop: bool = False) -> dict:
    return {"subject": "phone", "price_byn": round(price * 100), "company_ad": shop,
            "ad_parameters": [{"p": "condition", "v": condition}]}


def test_distribution_uses_the_stats_sample_filters_and_currency() -> None:
    ads = [_ad(100, "new") for _ in range(10)] + [_ad(200) for _ in range(10)]
    ads += [_ad(8000)]
    with TestClient(_app_for_ads(ads)) as client:
        params = {"query": "phone", "currency": "USD"}
        payload = client.get("/api/v1/price-stats", params=params).json()
        assert payload["count"] == sum(bucket["count"] for bucket in payload["histogram"]) == 20
        assert payload["histogram"][0]["from_price"] == payload["min"] == 50
        assert payload["histogram"][-1]["to_price"] == payload["max"] == 100
        assert payload["median"] == 75
        assert payload["reliable"] is True

        filtered = client.get("/api/v1/price-stats", params={**params, "condition": "new"}).json()
        assert filtered["histogram"] == [{"from_price": 50, "to_price": 50, "count": 10}]
        assert filtered["count"] == 10


def test_segments_supply_their_own_reference_and_sample_count() -> None:
    ads = [_ad(100, value) for value in ("1", " USED ", "Б/У")]
    ads += [_ad(200, value, shop=True) for value in ("2", " NEW ", "НОВЫЙ")]
    ads += [_ad(100, "unknown")]
    with TestClient(_app_for_ads(ads)) as client:
        params = {"query": "phone", "currency": "USD"}
        segments = client.get("/api/v1/segments", params=params).json()
        filtered = client.get("/api/v1/price-stats", params={**params, "condition": "new"}).json()
    assert segments["used_private"]["count"] == segments["new_shop"]["count"] == 3
    assert segments["sample_count"] == 6
    assert segments["reference"]["count"] == 7
    assert segments["reference"]["median"] == 50
    assert filtered["median"] == segments["new_shop"]["median"] == 100


def test_history_sees_first_snapshot_refreshes_same_bucket_and_skips_empty_points() -> None:
    ads = [_ad(100) for _ in range(3)]
    app = _app_for_ads(ads)

    async def seed_empty():
        async with app.state.session_factory() as session:
            session.add(QuerySnapshot(
                query=build_query_key("phone", True),
                snapshot_at=datetime.now(UTC) - timedelta(days=1),
                analyzed_count=0,
            ))
            await session.commit()

    with TestClient(app) as client:
        client.portal.call(seed_empty)
        params = {"query": "phone", "days": 7}
        assert client.get("/api/v1/price-history", params=params).json()["points"] == []
        assert client.get("/api/v1/price-stats", params={"query": "phone"}).status_code == 200
        first = client.get("/api/v1/price-history", params=params).json()["points"]
        assert len(first) == 1
        assert first[0]["median"] == 100
        assert first[0]["snapshot_at"].endswith(("Z", "+00:00"))
        ads[:] = [_ad(200) for _ in range(3)]
        assert client.get("/api/v1/price-stats", params={
            "query": "phone", "force_refresh": True,
        }).status_code == 200
        refreshed = client.get("/api/v1/price-history", params={
            **params, "force_refresh": True,
        }).json()["points"]
        assert len(refreshed) == 1
        assert refreshed[0]["median"] == 200


def test_broad_fallback_never_writes_into_strict_history() -> None:
    with TestClient(_app_for_ads([_ad(100) for _ in range(3)])) as client:
        stats = client.get("/api/v1/price-stats", params={"query": "phone fresh"}).json()
        assert stats["fallback_used"] is True
        assert stats["strict_search"] is False
        strict = client.get("/api/v1/price-history", params={"query": "phone fresh"}).json()
        broad = client.get("/api/v1/price-history", params={
            "query": "phone fresh", "strict_search": False,
        }).json()
    assert strict["points"] == []
    assert len(broad["points"]) == 1
