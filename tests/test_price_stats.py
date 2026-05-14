from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from api.services.cache import MemoryCache
from tests.conftest import FakeCurrencyService, init_test_tables


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "ads": [{"price_byn": 2000}, {"price_byn": 2200}, {"price_byn": 1800}],
            "total": 3,
        }

    async def aclose(self) -> None:
        return None


def test_price_stats_endpoint_returns_payload(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import price_stats

    monkeypatch.setattr(price_stats, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        asyncio.get_event_loop().run_until_complete(init_test_tables(app))
        response = client.get("/api/v1/price-stats", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "iphone"
    assert payload["count"] == 3
    # total_results comes from the Kufar API "total" field — may be aggregated
    assert payload["total_results"] >= 3
    assert payload["analyzed_count"] >= 3
    assert payload["currency"] == "USD"


def test_price_stats_exposes_category_total_cap_metadata(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import price_stats
    from api.services.query_pipeline import CATEGORY_TOTAL_MAX_CALLS

    class ManyCategoriesClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            ads = [
                {
                    "subject": f"iphone category {i}",
                    "price_byn": 1000 + i,
                    "category": str(10_000 + i),
                    "ad_parameters": [
                        {"p": "category", "v": str(10_000 + i), "vl": f"Категория {i}"}
                    ],
                }
                for i in range(CATEGORY_TOTAL_MAX_CALLS + 2)
            ]
            return {"ads": ads, "total": len(ads)}

        async def search(self, **kwargs) -> dict:
            category = int(kwargs["category"])
            return {
                "ads": [{"subject": "iphone", "price_byn": 1000, "category": str(category)}],
                "total": 1,
            }

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(price_stats, "KufarClient", ManyCategoriesClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: ManyCategoriesClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        asyncio.get_event_loop().run_until_complete(init_test_tables(app))
        response = client.get("/api/v1/price-stats", params={"query": "iphone"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["categories_limited"] is True
    assert payload["category_total_limit"] == CATEGORY_TOTAL_MAX_CALLS
    assert payload["category_total_candidates"] == CATEGORY_TOTAL_MAX_CALLS + 2


def test_price_stats_fetches_real_total_for_dominant_category(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import price_stats

    class DominantCategoryClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            ads = [
                {
                    "subject": f"iPhone 14 Pro {i}",
                    "price_byn": 2000 + i,
                    "category": "17010",
                    "ad_parameters": [
                        {"p": "category", "v": "17010", "vl": "Мобильные телефоны"}
                    ],
                }
                for i in range(4)
            ]
            ads.append({
                "subject": "Чехол iPhone 14 Pro",
                "price_byn": 40,
                "category": "17030",
                "ad_parameters": [
                    {"p": "category", "v": "17030", "vl": "Аксессуары для телефонов"}
                ],
            })
            return {"ads": ads, "total": 3174}

        async def search(self, **kwargs) -> dict:
            category = int(kwargs["category"])
            totals = {17010: 2242, 17030: 320}
            return {
                "ads": [
                    {
                        "subject": "iPhone 14 Pro",
                        "price_byn": 2000,
                        "category": str(category),
                    }
                ],
                "total": totals[category],
            }

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(price_stats, "KufarClient", DominantCategoryClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: DominantCategoryClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        asyncio.get_event_loop().run_until_complete(init_test_tables(app))
        response = client.get("/api/v1/price-stats", params={"query": "Iphone 14 Pro"})

    assert response.status_code == 200
    payload = response.json()
    categories = {cat["label"]: cat["count"] for cat in payload["categories"]}
    assert payload["total_results"] == 3174
    assert categories["Мобильные телефоны"] == 2242
    assert categories["Аксессуары для телефонов"] == 320
