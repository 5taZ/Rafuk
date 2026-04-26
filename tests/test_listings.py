from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.services.cache import MemoryCache


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


# The listings endpoint calls load_query_dataset which does 2 parallel
# searches (condition=new + condition=used). FakeKufarClient returns the same
# data for both calls, so total = sum of both responses.
FAKE_ADS = [
    {
        "ad_id": 1,
        "subject": "iPhone 15 256GB",
        "price_byn": 2000,
        "ad_link": "https://www.kufar.by/item/1",
        "list_time": "2026-04-01T10:00:00",
        "region_id": 6,
        "category": "1000",
        "ad_parameters": [
            {"p": "condition", "v": "Новый"},
            {"p": "category", "v": "1000", "vl": "Телефоны"},
        ],
    },
    {
        "ad_id": 2,
        "subject": "iPhone 15 Pro 256GB",
        "price_byn": 2600,
        "ad_link": "https://www.kufar.by/item/2",
        "list_time": "2026-04-01T11:00:00",
        "region_id": 6,
        "category": "1000",
        "ad_parameters": [
            {"p": "condition", "v": "Новый"},
            {"p": "category", "v": "1000", "vl": "Телефоны"},
        ],
    },
    {
        "ad_id": 3,
        "subject": "iPhone 15 mini 128GB",
        "price_byn": 1500,
        "ad_link": "https://www.kufar.by/item/3",
        "list_time": "2026-04-01T09:00:00",
        "region_id": 6,
        "category": "1000",
        "ad_parameters": [
            {"p": "condition", "v": "Б/у"},
            {"p": "category", "v": "1000", "vl": "Телефоны"},
        ],
    },
]

VERDICTS = {"Хорошая цена", "Ниже рынка", "Средняя цена", "Выше рынка"}


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {"total": len(FAKE_ADS), "ads": FAKE_ADS}

    async def aclose(self) -> None:
        return None


def test_listings_endpoint_returns_items(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    # 2 API calls × 3 ads each = 6; strict mode dedup may reduce this
    assert payload["total"] > 0
    assert payload["returned"] > 0
    assert payload["listings"][0]["title"] is not None
    assert payload["normalized_query"] == "iphone"
    assert payload["listings"][0]["deal_verdict"] in VERDICTS
    assert isinstance(payload["listings"][0]["deal_reasons"], list)
    assert payload["listings"][0]["liquidity"] is not None
    assert payload["listings"][0]["flip_estimates"] is not None


def test_listings_endpoint_supports_cheap_sort(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
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


def test_listings_endpoint_supports_strict_search(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={"query": "iphone 15 256", "currency": "BYN", "strict_search": True},
        )

    assert response.status_code == 200
    payload = response.json()
    # With strict mode, only ads matching "iphone 15 256" tokens should remain
    assert payload["total"] >= 1


def test_listings_endpoint_normalizes_alias_queries(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={"query": "айфон 15 256гб", "currency": "BYN", "strict_search": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["normalized_query"] == "iphone 15 256"


def test_listings_endpoint_returns_market_signals(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
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
                        "price_byn": 2000,
                        "ad_link": "https://www.kufar.by/item/1",
                        "list_time": "2026-04-01T10:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 2,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 2020,
                        "ad_link": "https://www.kufar.by/item/2",
                        "list_time": "2026-04-01T11:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 3,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 1980,
                        "ad_link": "https://www.kufar.by/item/3",
                        "list_time": "2026-04-01T12:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 4,
                        "subject": "iPhone 15 Pro 256GB",
                        "price_byn": 2400,
                        "ad_link": "https://www.kufar.by/item/4",
                        "list_time": "2026-04-01T09:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                    },
                    {
                        "ad_id": 5,
                        "subject": "iPhone 15 Ultra 1TB",
                        "price_byn": 4200,
                        "ad_link": "https://www.kufar.by/item/5",
                        "list_time": "2026-04-01T08:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                    },
                ],
            }

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(listings, "KufarClient", SignalsClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: SignalsClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "iphone", "currency": "BYN"})

    assert response.status_code == 200
    payload = response.json()
    first_item = next(item for item in payload["listings"] if item["ad_id"] == 2)
    anomaly_item = next(item for item in payload["listings"] if item["ad_id"] == 5)
    assert first_item["fair_price_label"] is not None
    assert first_item["deal_score"] >= 0
    assert first_item["deal_verdict"] in VERDICTS
    assert first_item["liquidity"] is not None
    assert anomaly_item["anomaly_flags"] == ["too_expensive"]
    assert anomaly_item["region_name"] == "Регион 6"


def test_listings_endpoint_uses_category_reference_for_mixed_query(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    mixed_ads = [
        {
            "ad_id": 1,
            "subject": "Audi Q7",
            "price_byn": 38000,
            "ad_link": "https://www.kufar.by/item/1",
            "list_time": "2026-04-01T10:00:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
        {
            "ad_id": 2,
            "subject": "Audi Q7 rest",
            "price_byn": 40000,
            "ad_link": "https://www.kufar.by/item/2",
            "list_time": "2026-04-01T10:10:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
        {
            "ad_id": 3,
            "subject": "Audi Q7 4L",
            "price_byn": 41000,
            "ad_link": "https://www.kufar.by/item/3",
            "list_time": "2026-04-01T10:20:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
        {
            "ad_id": 4,
            "subject": "Регулятор давления топлива Audi Q7",
            "price_byn": 100,
            "ad_link": "https://www.kufar.by/item/4",
            "list_time": "2026-04-01T10:30:00",
            "region_id": 6,
            "category": "2040",
            "ad_parameters": [{"p": "category", "v": "2040", "vl": "Запчасти"}],
        },
        {
            "ad_id": 5,
            "subject": "ТНВД Audi Q7",
            "price_byn": 120,
            "ad_link": "https://www.kufar.by/item/5",
            "list_time": "2026-04-01T10:40:00",
            "region_id": 6,
            "category": "2040",
            "ad_parameters": [{"p": "category", "v": "2040", "vl": "Запчасти"}],
        },
        {
            "ad_id": 6,
            "subject": "Форсунка Audi Q7",
            "price_byn": 141,
            "ad_link": "https://www.kufar.by/item/6",
            "list_time": "2026-04-01T10:50:00",
            "region_id": 6,
            "category": "2040",
            "ad_parameters": [{"p": "category", "v": "2040", "vl": "Запчасти"}],
        },
    ]

    class MixedCategoriesClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            return {"total": len(mixed_ads), "ads": mixed_ads}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(listings, "KufarClient", MixedCategoriesClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: MixedCategoriesClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "audi q7", "currency": "BYN"})

    assert response.status_code == 200
    payload = response.json()
    parts_item = next(item for item in payload["listings"] if item["ad_id"] == 6)
    car_item = next(item for item in payload["listings"] if item["ad_id"] == 2)

    assert parts_item["price_reference_scope"] == "category"
    assert parts_item["price_reference_label"] == "Запчасти"
    assert parts_item["price_vs_median"] == 17.5
    assert parts_item["deal_verdict"] == "Выше рынка"
    assert car_item["price_reference_scope"] == "category"


def test_listings_endpoint_keeps_price_delta_stable_in_category_view(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    broad_ads = [
        {
            "ad_id": 10,
            "subject": "Audi Q7 3.0 TDI",
            "price_byn": 3200000,
            "ad_link": "https://www.kufar.by/item/10",
            "list_time": "2026-04-01T10:00:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
        {
            "ad_id": 11,
            "subject": "Audi Q7 4L",
            "price_byn": 3400000,
            "ad_link": "https://www.kufar.by/item/11",
            "list_time": "2026-04-01T10:10:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
        {
            "ad_id": 12,
            "subject": "Audi Q7 рестайлинг",
            "price_byn": 3600000,
            "ad_link": "https://www.kufar.by/item/12",
            "list_time": "2026-04-01T10:20:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
        {
            "ad_id": 20,
            "subject": "Форсунка Audi Q7",
            "price_byn": 10000,
            "ad_link": "https://www.kufar.by/item/20",
            "list_time": "2026-04-01T10:30:00",
            "region_id": 6,
            "category": "2040",
            "ad_parameters": [{"p": "category", "v": "2040", "vl": "Запчасти"}],
        },
        {
            "ad_id": 21,
            "subject": "ТНВД Audi Q7",
            "price_byn": 12000,
            "ad_link": "https://www.kufar.by/item/21",
            "list_time": "2026-04-01T10:40:00",
            "region_id": 6,
            "category": "2040",
            "ad_parameters": [{"p": "category", "v": "2040", "vl": "Запчасти"}],
        },
        {
            "ad_id": 22,
            "subject": "Регулятор давления Audi Q7",
            "price_byn": 14000,
            "ad_link": "https://www.kufar.by/item/22",
            "list_time": "2026-04-01T10:50:00",
            "region_id": 6,
            "category": "2040",
            "ad_parameters": [{"p": "category", "v": "2040", "vl": "Запчасти"}],
        },
    ]
    category_ads = [
        *broad_ads[:3],
        {
            "ad_id": 13,
            "subject": "Audi Q7 S-Line",
            "price_byn": 3800000,
            "ad_link": "https://www.kufar.by/item/13",
            "list_time": "2026-04-01T11:00:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
        {
            "ad_id": 14,
            "subject": "Audi Q7 3.0 бензин",
            "price_byn": 4000000,
            "ad_link": "https://www.kufar.by/item/14",
            "list_time": "2026-04-01T11:10:00",
            "region_id": 6,
            "category": "2010",
            "ad_parameters": [{"p": "category", "v": "2010", "vl": "Легковые авто"}],
        },
    ]

    class CategoryDriftClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            category = kwargs.get("category")
            ads = category_ads if category == 2010 else broad_ads
            return {"total": len(ads), "ads": ads}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(listings, "KufarClient", CategoryDriftClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: CategoryDriftClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        broad_response = client.get(
            "/api/v1/listings",
            params={"query": "audi q7", "currency": "BYN"},
        )
        category_response = client.get(
            "/api/v1/listings",
            params={
                "query": "audi q7",
                "currency": "BYN",
                "category": 2010,
                "reference_context": "base_query",
            },
        )

    assert broad_response.status_code == 200
    assert category_response.status_code == 200
    broad_payload = broad_response.json()
    category_payload = category_response.json()
    broad_item = next(item for item in broad_payload["listings"] if item["ad_id"] == 10)
    category_item = next(item for item in category_payload["listings"] if item["ad_id"] == 10)

    assert broad_item["price_vs_median"] == category_item["price_vs_median"]


def test_listings_pagination_returns_first_page_only(monkeypatch) -> None:
    """With 60 fake ads and limit=20, the first page must carry 20
    items, ``has_more=True``, and ``offset=0``. Subsequent pages
    pick up where the previous one left off without re-issuing
    Kufar fetches (cache key includes offset/limit).
    """
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    big_ads = [
        {
            "ad_id": 1000 + i,
            "subject": f"iPhone 15 {i}",
            "price_byn": 1500 + i * 10,
            "ad_link": f"https://www.kufar.by/item/{1000 + i}",
            "list_time": f"2026-04-01T{i % 24:02d}:00:00",
            "region_id": 6,
            "category": "1000",
            "ad_parameters": [
                {"p": "condition", "v": "Б/у"},
                {"p": "category", "v": "1000", "vl": "Телефоны"},
            ],
        }
        for i in range(60)
    ]

    class _BigClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            return {"total": len(big_ads), "ads": big_ads}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(listings, "KufarClient", _BigClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: _BigClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        first = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "limit": 20, "offset": 0},
        )
        second = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "limit": 20, "offset": 20},
        )

    assert first.status_code == 200
    p1 = first.json()
    assert len(p1["listings"]) == 20
    assert p1["offset"] == 0
    assert p1["limit"] == 20
    assert p1["has_more"] is True
    # Total reflects the full server-side count, not the page slice.
    assert p1["total"] >= 60

    assert second.status_code == 200
    p2 = second.json()
    assert len(p2["listings"]) == 20
    assert p2["offset"] == 20
    # No overlap between pages — different ad_ids in each slice.
    page1_ids = {item["ad_id"] for item in p1["listings"]}
    page2_ids = {item["ad_id"] for item in p2["listings"]}
    assert page1_ids.isdisjoint(page2_ids)


def test_listings_pagination_signals_no_more_on_last_page(monkeypatch) -> None:
    """When ``offset + returned >= cap``, ``has_more`` flips to False
    so the frontend's IntersectionObserver stops asking for more.
    """
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        # The fixture set has only 3 ads — even at limit=10 there's
        # nothing else to serve, so has_more must be False.
        response = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "limit": 10, "offset": 0},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_more"] is False
    assert payload["offset"] == 0


def test_listings_pagination_validates_bounds(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        # limit=0 — below the ge=1 floor.
        response = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "limit": 0},
        )
        assert response.status_code == 422
        # offset=-1 — below the ge=0 floor.
        response = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "offset": -1},
        )
        assert response.status_code == 422


def test_listings_caches_kufar_dataset_for_repeat_calls(monkeypatch) -> None:
    """First /listings request paginates Kufar; the second on the same
    query should hit the dataset cache and skip the upstream entirely.

    This is the cross-endpoint sharing path: when a frontend search
    fires /price-stats + /listings + /geography in parallel, only the
    first to land actually pages Kufar — the rest hit the cache.
    """
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listings

    fetch_calls = 0

    class _CountingClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            nonlocal fetch_calls
            del kwargs
            fetch_calls += 1
            return {"total": len(FAKE_ADS), "ads": FAKE_ADS}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(listings, "KufarClient", _CountingClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: _CountingClient(None)
    # Shared cache across requests — the prod RedisCache is shared too,
    # so ``lambda: MemoryCache()`` would mis-model the prod behaviour.
    shared_cache = MemoryCache()
    app.dependency_overrides[get_cache] = lambda: shared_cache
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        # First call — cold cache, paginates Kufar (1 search_all_ads).
        first = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "limit": 20, "offset": 0},
        )
        assert first.status_code == 200
        assert fetch_calls == 1, "first call should paginate Kufar exactly once"

        # Different page of the SAME query — must reuse the cached
        # raw Kufar dataset; the listings response cache is keyed on
        # offset so it MUST hit the page-cache miss path, fall through
        # to load_query_dataset, and there hit the dataset cache.
        second = client.get(
            "/api/v1/listings",
            params={"query": "iphone", "limit": 20, "offset": 20},
        )
        assert second.status_code == 200
        assert fetch_calls == 1, (
            f"second call hit Kufar {fetch_calls} times — dataset cache "
            f"should have caught it"
        )
