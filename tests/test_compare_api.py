from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.models import Base, QuerySnapshot
from api.services.aggregator import build_query_key
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
        query = str(kwargs.get("query", "")).casefold()
        if "iphone 16" in query:
            ads = [
                {"ad_id": 11, "subject": "iPhone 16 128GB", "price_byn": 2500, "ad_link": "https://kufar/item/11", "list_time": "2026-04-06T10:00:00"},
                {"ad_id": 12, "subject": "iPhone 16 128GB", "price_byn": 2350, "ad_link": "https://kufar/item/12", "list_time": "2026-04-06T09:00:00"},
                {"ad_id": 13, "subject": "iPhone 16 128GB", "price_byn": 2700, "ad_link": "https://kufar/item/13", "list_time": "2026-04-05T09:00:00"},
            ]
        elif "iphone 15 pro" in query:
            ads = [
                {"ad_id": 21, "subject": "iPhone 15 Pro 256GB", "price_byn": 2200, "ad_link": "https://kufar/item/21", "list_time": "2026-04-06T10:00:00"},
                {"ad_id": 22, "subject": "iPhone 15 Pro 256GB", "price_byn": 2450, "ad_link": "https://kufar/item/22", "list_time": "2026-04-05T10:00:00"},
                {"ad_id": 23, "subject": "iPhone 15 Pro 256GB", "price_byn": 2550, "ad_link": "https://kufar/item/23", "list_time": "2026-04-04T10:00:00"},
            ]
        else:
            ads = [
                {"ad_id": 1, "subject": "iPhone 15 128GB", "price_byn": 1800, "ad_link": "https://kufar/item/1", "list_time": "2026-04-06T10:00:00"},
                {"ad_id": 2, "subject": "iPhone 15 128GB", "price_byn": 1900, "ad_link": "https://kufar/item/2", "list_time": "2026-04-06T09:00:00"},
                {"ad_id": 3, "subject": "iPhone 15 128GB", "price_byn": 2100, "ad_link": "https://kufar/item/3", "list_time": "2026-04-05T09:00:00"},
            ]
        return {"total": len(ads), "ads": ads}

    async def aclose(self) -> None:
        return None


async def seed_history(session_factory) -> None:
    async with session_factory() as session:
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        session.add_all(
            [
                QuerySnapshot(
                    query=build_query_key("iphone 15 128", False),
                    snapshot_at=now - timedelta(days=5),
                    total_results=80,
                    analyzed_count=60,
                    mean_byn=1950,
                    median_byn=1900,
                    min_byn=1700,
                    max_byn=2200,
                ),
                QuerySnapshot(
                    query=build_query_key("iphone 15 128", False),
                    snapshot_at=now - timedelta(hours=3),
                    total_results=90,
                    analyzed_count=72,
                    mean_byn=2000,
                    median_byn=1950,
                    min_byn=1750,
                    max_byn=2300,
                ),
            ]
        )
        await session.commit()


async def create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def test_compare_endpoint_returns_multi_query_summary(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import compare

    monkeypatch.setattr(compare, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        asyncio.run(create_tables(app.state.engine))
        asyncio.run(seed_history(app.state.session_factory))
        response = client.get(
            "/api/v1/compare",
            params=[
                ("base_query", "iphone 15 128"),
                ("compare_query", "iphone 16 128"),
                ("compare_query", "iphone 15 pro 256"),
                ("currency", "BYN"),
            ],
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["base_query"] == "iphone 15 128"
    assert len(payload["items"]) == 3
    assert payload["items"][0]["best_listing"] is not None
    assert payload["items"][0]["cheap_count"] >= 1
