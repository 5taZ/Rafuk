from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.models import QuerySnapshot
from api.routers.compare import compute_trend_percent
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


def _ad(ad_id: int, subject: str, price_byn: int, list_time: str) -> dict:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "price_byn": price_byn,
        "ad_link": f"https://kufar/item/{ad_id}",
        "list_time": list_time,
    }


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        query = str(kwargs.get("query", "")).casefold()
        if "iphone 16" in query:
            ads = [
                _ad(11, "iPhone 16 128GB", 250000, "2026-04-06T10:00:00"),
                _ad(12, "iPhone 16 128GB", 235000, "2026-04-06T09:00:00"),
                _ad(13, "iPhone 16 128GB", 270000, "2026-04-05T09:00:00"),
            ]
        elif "iphone 15 pro" in query:
            ads = [
                _ad(21, "iPhone 15 Pro 256GB", 220000, "2026-04-06T10:00:00"),
                _ad(22, "iPhone 15 Pro 256GB", 245000, "2026-04-05T10:00:00"),
                _ad(23, "iPhone 15 Pro 256GB", 255000, "2026-04-04T10:00:00"),
            ]
        else:
            ads = [
                _ad(1, "iPhone 15 128GB", 180000, "2026-04-06T10:00:00"),
                _ad(2, "iPhone 15 128GB", 190000, "2026-04-06T09:00:00"),
                _ad(3, "iPhone 15 128GB", 210000, "2026-04-05T09:00:00"),
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


def test_compare_endpoint_returns_multi_query_summary(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import compare

    monkeypatch.setattr(compare, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        client.portal.call(seed_history, app.state.session_factory)
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
    # Snapshots 5 days and 3 hours old: 1900 -> 1950 BYN.
    assert payload["items"][0]["trend_percent"] == 2.63
    assert payload["items"][1]["trend_percent"] is None


def make_app(monkeypatch):
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import compare

    monkeypatch.setattr(compare, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    return app


def test_compare_deduplicates_against_base_query(monkeypatch) -> None:
    app = make_app(monkeypatch)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/compare",
            params=[
                ("base_query", " iphone 15 128 "),
                ("compare_query", "iPhone 15 128GB, IPHONE 16 128"),
                ("compare_query", "iphone 16 128gb"),
            ],
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["base_query"] == "iphone 15 128"
    assert payload["currency"] == "BYN"
    assert [item["query"] for item in payload["items"]] == ["iphone 15 128", "IPHONE 16 128"]


def test_compare_validates_compare_queries(monkeypatch) -> None:
    app = make_app(monkeypatch)
    with TestClient(app) as client:
        too_long = client.get(
            "/api/v1/compare",
            params=[("base_query", "iphone 15"), ("compare_query", "x" * 300)],
        )
        blank_base = client.get("/api/v1/compare", params={"base_query": "   "})
        euro = client.get("/api/v1/compare", params={"base_query": "iphone", "currency": "EUR"})
        empty_parts = client.get(
            "/api/v1/compare",
            params=[("base_query", "iphone 15"), ("compare_query", " , ,")],
        )

    assert too_long.status_code == 422
    assert blank_base.status_code == 422
    assert euro.status_code == 422
    assert empty_parts.status_code == 200
    assert len(empty_parts.json()["items"]) == 1


def _snapshot(hours_ago: float, median: float, analyzed: int = 10) -> QuerySnapshot:
    now = datetime(2026, 4, 10, 12, tzinfo=UTC)
    return QuerySnapshot(
        query="broad::iphone",
        snapshot_at=now - timedelta(hours=hours_ago),
        total_results=analyzed,
        analyzed_count=analyzed,
        mean_byn=median,
        median_byn=median,
        min_byn=median,
        max_byn=median,
    )


def test_trend_requires_a_day_between_points() -> None:
    assert compute_trend_percent([_snapshot(2, 1000), _snapshot(1, 1100)]) is None
    assert compute_trend_percent([_snapshot(30, 1000), _snapshot(1, 1100)]) == 10.0
    assert compute_trend_percent([_snapshot(30, 1000)]) is None


def test_trend_ignores_empty_snapshots() -> None:
    snapshots = [
        _snapshot(72, 0, analyzed=0),
        _snapshot(48, 1000),
        _snapshot(20, 1050),
        _snapshot(1, 0, analyzed=0),
    ]
    assert compute_trend_percent(snapshots) == 5.0
    naive = _snapshot(48, 1000)
    naive.snapshot_at = naive.snapshot_at.replace(tzinfo=None)
    assert compute_trend_percent([naive, _snapshot(1, 900)]) == -10.0
