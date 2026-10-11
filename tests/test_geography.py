from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.services.cache import MemoryCache


class FakeCurrencyService:
    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": {"USD": 3.0},
            "source": "test",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    def convert_from_byn(self, amount_byn: float, currency: str, rates: dict[str, float]) -> float:
        if currency == "BYN":
            return round(amount_byn, 2)
        return round(amount_byn / rates[currency], 2)


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
                    "price_byn": 200000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "region_id": 6,
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 15 Pro",
                    "price_byn": 220000,
                    "ad_link": "https://www.kufar.by/item/2",
                    "region_id": 6,
                },
                {
                    "ad_id": 3,
                    "subject": "iPhone 15 Mini",
                    "price_byn": 180000,
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
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import geography

    monkeypatch.setattr(geography, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        response = client.get("/api/v1/geography", params={"query": "iphone", "currency": "BYN"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_analyzed"] == 3
    assert len(payload["regions"]) == 2
    assert payload["regions"][0]["region_name"] == "Регион 6"
    assert payload["regions"][0]["count"] == 2


def test_geography_reads_region_from_strings_and_parameters(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import geography

    calls: list[dict] = []

    class ParameterRegionClient(FakeKufarClient):
        async def search_all_ads(self, **kwargs) -> dict:
            calls.append(kwargs)
            region_param = {"p": "region", "pl": "Область", "v": "7", "vl": "Минск"}
            return {
                "ads": [
                    {"ad_id": 1, "price_byn": 200000, "region_id": "6"},
                    {"ad_id": 2, "price_byn": 210000, "ad_parameters": [region_param]},
                    {"ad_id": 3, "price_byn": 220000, "ad_parameters": [region_param]},
                    {"ad_id": 4, "price_byn": 230000, "region_id": "abc"},
                    {"ad_id": 5, "price_byn": 240000, "ad_parameters": None},
                ],
            }

    monkeypatch.setattr(geography, "KufarClient", ParameterRegionClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        response = client.get("/api/v1/geography", params={"query": "iphone", "currency": "USD"})
        blank = client.get("/api/v1/geography", params={"query": " "})
        euro = client.get("/api/v1/geography", params={"query": "iphone", "currency": "EUR"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_analyzed"] == 5
    assert [(region["region_id"], region["region_name"]) for region in payload["regions"]] == [
        (7, "Минск"),
        (6, "Регион 6"),
    ]
    assert payload["regions"][0]["median"] == 716.67
    assert calls[0]["currency"] == "BYN"
    assert blank.status_code == 422
    assert euro.status_code == 422


def test_geography_drops_placeholders_before_grouping(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import geography

    class PlaceholderClient(FakeKufarClient):
        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            ads = [{"ad_id": index, "price_byn": 100000, "region_id": 7} for index in range(1, 11)]
            ads.append({"ad_id": 11, "price_byn": 100, "region_id": 1})
            ads.append({"ad_id": 12, "price_byn": 120000, "region_id": 5})
            ads.append({"ad_id": 13, "price_byn": 110000})
            return {"ads": ads}

    monkeypatch.setattr(geography, "KufarClient", PlaceholderClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        response = client.get("/api/v1/geography", params={"query": "iphone"})

    assert response.status_code == 200
    payload = response.json()
    # The 1 BYN lot is a placeholder market-wide: it is in no region, not even its own.
    assert [region["region_id"] for region in payload["regions"]] == [7, 5]
    assert payload["total_analyzed"] == 12
    # Shares are of the lots with a known region and add up to 100%.
    assert [region["share_percent"] for region in payload["regions"]] == [90.9, 9.1]
    assert all(region["min"] >= 1000.0 for region in payload["regions"])
