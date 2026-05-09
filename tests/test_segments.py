from __future__ import annotations

from fastapi.testclient import TestClient

from api.services.cache import MemoryCache
from tests.conftest import FakeCurrencyService, FakeKufarClient


def test_segments_endpoint_returns_all_groups(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import segments

    # Segments now derive from the singleflighted dataset (no extra
    # Kufar fan-out). Stub load_query_dataset to return a mixed
    # batch of new/used + private/shop ads and verify the router
    # splits them into all 4 baskets correctly.
    async def fake_load_query_dataset_with_fallback(**kwargs):
        del kwargs
        from api.services.query_pipeline import QueryDataset
        new = [{"p": "condition", "v": "Новый"}]
        used = [{"p": "condition", "v": "Б/у"}]
        ads = [
            {"price_byn": 2000, "company_ad": False, "ad_parameters": new},
            {"price_byn": 2500, "company_ad": True, "ad_parameters": new},
            {"price_byn": 1800, "company_ad": False, "ad_parameters": used},
            {"price_byn": 2200, "company_ad": True, "ad_parameters": used},
        ]
        dataset = QueryDataset(
            query="iphone", currency="USD", strict_search=False,
            response={"ads": ads, "total": 4}, ads=ads,
        )
        from api.services.query_pipeline import DatasetWithFallback
        return DatasetWithFallback(dataset=dataset, fallback_used=False)

    monkeypatch.setattr(segments, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(
        segments, "load_query_dataset_with_fallback", fake_load_query_dataset_with_fallback,
    )
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/segments", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    # All 4 segments exist (new/used × private/shop) and each has 1 ad.
    assert payload["new_private"]["count"] == 1
    assert payload["new_shop"]["count"] == 1
    assert payload["used_private"]["count"] == 1
    assert payload["used_shop"]["count"] == 1
