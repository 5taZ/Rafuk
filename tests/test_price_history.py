from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.models import QuerySnapshot
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
        return {
            "total": 3,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 16",
                    "price_byn": 2400,
                    "ad_link": "https://www.kufar.by/item/1",
                    "list_time": "2026-04-01T10:00:00",
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 16 Pro",
                    "price_byn": 2600,
                    "ad_link": "https://www.kufar.by/item/2",
                    "list_time": "2026-04-01T11:00:00",
                },
            ],
        }

    async def aclose(self) -> None:
        return None


async def seed_history(session_factory, query: str = "iphone 16") -> None:
    async with session_factory() as session:
        await session.execute(
            delete(QuerySnapshot).where(QuerySnapshot.query == build_query_key(query, False))
        )
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        session.add_all(
            [
                QuerySnapshot(
                    query=build_query_key(query, False),
                    snapshot_at=now - timedelta(days=2),
                    total_results=100,
                    analyzed_count=80,
                    mean_byn=2500,
                    median_byn=2400,
                    min_byn=1700,
                    max_byn=3300,
                ),
                QuerySnapshot(
                    query=build_query_key(query, False),
                    snapshot_at=now - timedelta(hours=6),
                    total_results=120,
                    analyzed_count=92,
                    mean_byn=2600,
                    median_byn=2450,
                    min_byn=1750,
                    max_byn=3400,
                ),
            ]
        )
        await session.commit()


def test_price_history_endpoint_returns_snapshots() -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        asyncio.run(seed_history(app.state.session_factory, query="iphone 16 history"))
        response = client.get(
            "/api/v1/price-history",
            params={"query": "iphone 16 history", "currency": "USD", "days": 7},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "iphone 16 history"
    assert payload["days"] == 7
    assert len(payload["points"]) == 2
    assert payload["points"][-1]["median"] == 816.67


def test_price_history_caps_days_at_ninety() -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        asyncio.run(seed_history(app.state.session_factory, query="iphone 16 caps"))
        response = client.get(
            "/api/v1/price-history",
            params={"query": "iphone 16 caps", "currency": "BYN", "days": 365},
        )

    assert response.status_code == 200
    assert response.json()["days"] == 90


def test_price_stats_request_persists_snapshot(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import price_stats

    monkeypatch.setattr(price_stats, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        asyncio.run(seed_history(app.state.session_factory, query="iphone 16 persist-old"))
        stats_response = client.get(
            "/api/v1/price-stats",
            params={"query": "iphone 16 fresh", "currency": "BYN"},
        )
        history_response = client.get(
            "/api/v1/price-history",
            params={"query": "iphone 16 fresh", "currency": "BYN", "days": 1},
        )

    assert stats_response.status_code == 200
    assert history_response.status_code == 200
    payload = history_response.json()
    assert len(payload["points"]) == 1
    assert payload["points"][0]["median"] == 2500
