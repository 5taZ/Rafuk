from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.services import aggregator
from api.services.cache import MemoryCache

FROZEN_NOW = datetime(2026, 4, 1, 12, tzinfo=UTC)


class FakeCurrencyService:
    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": {"USD": 3.2, "EUR": 3.5},
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
        return {
            "total": 7,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15 256GB",
                    "price_byn": 200000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "list_time": "2026-04-01T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "Новый"}],
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 15 Pro 256GB",
                    "price_byn": 260000,
                    "ad_link": "https://www.kufar.by/item/2",
                    "list_time": "2026-04-01T11:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "Новый"}],
                },
                {
                    "ad_id": 3,
                    "subject": "iPhone 15 mini 128GB",
                    "price_byn": 150000,
                    "ad_link": "https://www.kufar.by/item/3",
                    "list_time": "2026-04-01T09:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "Б/у"}],
                },
            ]
        }

    async def aclose(self) -> None:
        return None


def test_listings_endpoint_returns_items(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["returned"] == 3
    assert payload["listings"][0]["title"] == "iPhone 15 Pro 256GB"
    assert payload["normalized_query"] == "iphone"
    assert payload["listings"][0]["deal_verdict"] is not None
    assert isinstance(payload["listings"][0]["deal_reasons"], list)
    assert payload["listings"][0]["liquidity"] is not None
    assert payload["listings"][0]["flip_estimates"]


def test_listings_endpoint_supports_cheap_sort(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={
                "query": "iphone",
                "currency": "BYN",
                "sort": "cheap",
                "discount_from_percent": 10,
                "discount_to_percent": 30,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sort"] == "cheap"
    assert payload["discount_from_percent"] == 10
    assert payload["discount_to_percent"] == 30
    assert [item["ad_id"] for item in payload["listings"]] == [3]


def test_listings_endpoint_supports_strict_search(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={"query": "iphone 15 256", "currency": "BYN", "strict_search": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["ad_id"] for item in payload["listings"]] == [1]


def test_listings_endpoint_normalizes_alias_queries(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={"query": "айфон 15 256гб", "currency": "BYN", "strict_search": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["normalized_query"] == "iphone 15 256"


def test_listings_endpoint_returns_market_signals(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listings

    class SignalsClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            return {
                "total": 5,
                "ads": [
                    {
                        "ad_id": 1,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 200000,
                        "ad_link": "https://www.kufar.by/item/1",
                        "list_time": "2026-04-01T10:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 2,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 202000,
                        "ad_link": "https://www.kufar.by/item/2",
                        "list_time": "2026-04-01T11:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 3,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 198000,
                        "ad_link": "https://www.kufar.by/item/3",
                        "list_time": "2026-04-01T12:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 4,
                        "subject": "iPhone 15 Pro 256GB",
                        "price_byn": 240000,
                        "ad_link": "https://www.kufar.by/item/4",
                        "list_time": "2026-04-01T09:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                    },
                    {
                        "ad_id": 5,
                        "subject": "iPhone 15 Ultra 1TB",
                        "price_byn": 420000,
                        "ad_link": "https://www.kufar.by/item/5",
                        "list_time": "2026-04-01T08:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                    },
                ],
            }

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(aggregator, "utc_now", lambda: FROZEN_NOW)
    monkeypatch.setattr(listings, "KufarClient", SignalsClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "iphone", "currency": "BYN"})

    assert response.status_code == 200
    payload = response.json()
    duplicate_item = next(item for item in payload["listings"] if item["ad_id"] == 2)
    anomaly_item = next(item for item in payload["listings"] if item["ad_id"] == 5)
    assert duplicate_item["is_duplicate"] is True
    assert duplicate_item["duplicate_count"] >= 1
    assert duplicate_item["fair_price_label"] is not None
    assert duplicate_item["deal_score"] == 61.0
    assert duplicate_item["deal_verdict"] == "Норм"
    assert duplicate_item["deal_reasons"] == ["частник", "свежий лот", "есть дубли"]
    assert duplicate_item["liquidity"] is not None
    assert anomaly_item["anomaly_flags"] == ["too_expensive"]
    assert anomaly_item["region_name"] == "Регион 6"


def make_app(monkeypatch, client_cls, cache: MemoryCache | None = None):
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", client_cls)
    app = create_app()
    shared_cache = cache or MemoryCache()
    app.dependency_overrides[get_cache] = lambda: shared_cache
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    return app


def static_client(ads: list[dict], calls: list[dict] | None = None):
    class StaticClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            if calls is not None:
                calls.append(kwargs)
            return {"total": len(ads), "ads": ads}

        async def aclose(self) -> None:
            return None

    return StaticClient


def test_listings_rejects_blank_query_and_unknown_currency(monkeypatch) -> None:
    app = make_app(monkeypatch, FakeKufarClient)
    with TestClient(app) as client:
        blank = client.get("/api/v1/listings", params={"query": "   "})
        euro = client.get("/api/v1/listings", params={"query": "iphone", "currency": "EUR"})
        too_long = client.get("/api/v1/listings", params={"query": "x" * 256})
    assert blank.status_code == 422
    assert euro.status_code == 422
    assert too_long.status_code == 422


def test_listings_defaults_to_byn_and_caches_by_normalized_query(monkeypatch) -> None:
    calls: list[dict] = []
    ads = [{"ad_id": 1, "subject": "iPhone 15", "price_byn": 200000, "list_time": None}]
    app = make_app(monkeypatch, static_client(ads, calls))
    with TestClient(app) as client:
        first = client.get("/api/v1/listings", params={"query": "  iPhone 15 "})
        second = client.get("/api/v1/listings", params={"query": "iphone 15"})
        usd = client.get("/api/v1/listings", params={"query": "iphone 15", "currency": "USD"})

    assert first.status_code == 200
    assert first.json()["currency"] == "BYN"
    assert first.json()["query"] == "iPhone 15"
    assert second.json()["query"] == "iphone 15"
    assert usd.json()["currency"] == "USD"
    assert usd.json()["listings"][0]["price"] == 625.0
    # The second BYN request is a cache hit; the USD response is a separate response-cache
    # entry served from the cached dataset, so Kufar is queried once, in BYN.
    assert [call["currency"] for call in calls] == ["BYN"]
    assert calls[0]["query"] == "iPhone 15"


def test_listings_deal_score_ranks_whole_dataset(monkeypatch) -> None:
    monkeypatch.setattr(aggregator, "utc_now", lambda: FROZEN_NOW)
    start = FROZEN_NOW - timedelta(days=30)
    ads = []
    for index in range(300):
        is_cheap = index < 50
        ads.append(
            {
                "ad_id": index + 1,
                "subject": f"iPhone 15 lot {index}",
                "price_byn": 70000 if is_cheap else 100000,
                "ad_link": f"https://www.kufar.by/item/{index + 1}",
                # The 50 cheap lots are the oldest ones.
                "list_time": (start + timedelta(minutes=index)).isoformat(),
            }
        )
    app = make_app(monkeypatch, static_client(ads))
    with TestClient(app) as client:
        scored = client.get("/api/v1/listings", params={"query": "iphone", "sort": "deal_score"})
        cheap = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "sort": "cheap", "discount_from_percent": 10},
        )

    assert scored.status_code == 200
    payload = scored.json()
    assert payload["total"] == 300
    assert payload["returned"] == 200
    top_ids = [item["ad_id"] for item in payload["listings"][:50]]
    assert sorted(top_ids) == list(range(1, 51))
    assert cheap.json()["total"] == 50
    assert cheap.json()["returned"] == 50


def test_listings_unpriced_lot_is_neutral_and_last(monkeypatch) -> None:
    monkeypatch.setattr(aggregator, "utc_now", lambda: FROZEN_NOW)
    ads = [
        {"ad_id": 1, "subject": "iPhone 15", "price_byn": 0, "list_time": "2026-04-01T11:30:00"},
        {"ad_id": 2, "subject": "iPhone 15", "price_byn": 200000},
        {"ad_id": 3, "subject": "iPhone 15", "price_byn": 210000},
    ]
    app = make_app(monkeypatch, static_client(ads))
    with TestClient(app) as client:
        price_asc = client.get("/api/v1/listings", params={"query": "iphone", "sort": "price_asc"})
        scored = client.get("/api/v1/listings", params={"query": "iphone", "sort": "deal_score"})

    assert [item["ad_id"] for item in price_asc.json()["listings"]] == [2, 3, 1]
    unpriced = scored.json()["listings"][-1]
    assert unpriced["ad_id"] == 1
    assert unpriced["price_byn"] is None
    assert unpriced["price_vs_median"] is None
    assert unpriced["fair_price_band"] is None
    assert unpriced["deal_verdict"] == "Нет цены"
    assert unpriced["deal_score"] == 0.0
    assert unpriced["flip_estimates"] == []


def test_listings_survives_malformed_ads(monkeypatch) -> None:
    ads = [
        {"ad_id": 1, "subject": "iPhone 15", "price_byn": 200000, "list_time": "2026-04-01T10:00"},
        {"ad_id": None, "subject": "iPhone 15", "price_byn": 190000},
        {"ad_id": 3, "subject": "iPhone 15", "price_byn": 210000, "list_time": None},
        {"ad_id": 4, "subject": "iPhone 15", "price_byn": 205000, "images": None},
        {"ad_id": 5, "subject": "iPhone 15", "price_byn": 195000, "ad_parameters": None},
        {"ad_id": 6, "subject": "iPhone 15", "price_byn": 199000, "region_id": "abc"},
        {"ad_id": 7, "subject": "iPhone 15", "price_byn": 201000, "region_id": 6.5},
        {"ad_id": 8, "subject": "iPhone 15", "price_byn": 202000, "images": ["a.jpg"]},
        {"ad_id": "9", "subject": None, "price_byn": "oops", "list_time": 1760000000},
    ]
    app = make_app(monkeypatch, static_client(ads))
    with TestClient(app) as client:
        responses = [
            client.get("/api/v1/listings", params={"query": "iphone", "sort": sort})
            for sort in ("newest", "deal_score", "cheap", "price_asc", "near_median")
        ]

    assert [response.status_code for response in responses] == [200] * 5
    newest = responses[0].json()["listings"]
    assert newest[0]["ad_id"] == 1
    assert {item["ad_id"] for item in newest} == {1, 3, 4, 5, 6, 7, 8, 9}
    region_ids = {item["ad_id"]: item["region_id"] for item in newest}
    assert region_ids[6] is None
    assert region_ids[7] is None
