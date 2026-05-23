from __future__ import annotations

from fastapi.testclient import TestClient

from api.services.cache import MemoryCache
from tests.conftest import FakeCurrencyService


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "total": 4,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15",
                    "price_byn": 2000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "region_id": 6,
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 15 Pro",
                    "price_byn": 2200,
                    "ad_link": "https://www.kufar.by/item/2",
                    "region_id": 6,
                },
                {
                    "ad_id": 3,
                    "subject": "iPhone 15 Mini",
                    "price_byn": 1800,
                    "ad_link": "https://www.kufar.by/item/3",
                    "region_id": 7,
                },
                {
                    "ad_id": 4,
                    "subject": "iPhone 15 Max",
                    "price_byn": 0,
                    "ad_link": "https://www.kufar.by/item/4",
                    "region_id": 7,
                },
            ],
        }

    async def aclose(self) -> None:
        return None


def test_geography_endpoint_returns_region_stats(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import geography

    monkeypatch.setattr(geography, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/geography",
            params={"query": "iphone", "currency": "BYN", "strict_search": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_analyzed"] == 3
    assert len(payload["regions"]) == 2
    assert payload["regions"][0]["region_name"] == "Регион 6"
    assert payload["regions"][0]["count"] == 2


def test_geography_share_percent_sums_to_100(monkeypatch) -> None:
    """B-05/D-3: share_percent uses sum of per-region counts as denominator."""
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import geography

    class FakeKufarClientOutlier:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            # Region 6 has an outlier that will be removed per-region
            return {
                "total": 5,
                "ads": [
                    {"ad_id": 1, "subject": "A", "price_byn": 100, "ad_link": "x", "region_id": 6},
                    {"ad_id": 2, "subject": "B", "price_byn": 110, "ad_link": "x", "region_id": 6},
                    {"ad_id": 3, "subject": "C", "price_byn": 120, "ad_link": "x", "region_id": 6},
                    {"ad_id": 4, "subject": "D", "price_byn": 200, "ad_link": "x", "region_id": 7},
                    {"ad_id": 5, "subject": "E", "price_byn": 210, "ad_link": "x", "region_id": 7},
                ],
            }

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(geography, "KufarClient", FakeKufarClientOutlier)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClientOutlier(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/geography",
            params={"query": "test", "currency": "BYN", "strict_search": False},
        )

    assert response.status_code == 200
    regions = response.json()["regions"]
    total_share = sum(r["share_percent"] for r in regions)
    assert 99.0 <= total_share <= 101.0  # rounding tolerance
