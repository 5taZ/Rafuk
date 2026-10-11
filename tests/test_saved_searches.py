from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.middleware.telegram_auth import TelegramInitData
from api.models import SavedSearch, Tracker, TrackerEvent
from api.services import aggregator
from api.services.kufar_client import KufarAPIError
from api.services.users import ensure_user

# 30 hours after the list_time of make_ad() listings: the small "свежий" freshness bonus.
FROZEN_NOW = datetime(2026, 4, 7, 16, tzinfo=UTC)


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


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


def make_ad(ad_id: int, price_byn: int, *, seller: str = "Частное лицо") -> dict:
    return {
        "ad_id": ad_id,
        "subject": "iPhone 15 256GB",
        "price_byn": price_byn,
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "list_time": "2026-04-06T10:00:00",
        "region_id": 6,
        "ad_parameters": [{"p": "seller_type", "v": seller}],
    }


SMALL_MARKET = [
    make_ad(1, 180000),
    make_ad(2, 198000),
    make_ad(3, 225000, seller="Магазин"),
]
# 12 listings: a regular market, no "rare offer" and no "thin market" signal.
LARGE_MARKET = [make_ad(10, 150000)] + [make_ad(100 + index, 220000) for index in range(11)]


class FakeKufarClient:
    markets: dict[str, list[dict]] = {}
    failing_queries: set[str] = set()

    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        query = kwargs.get("query", "")
        if query in FakeKufarClient.failing_queries:
            raise KufarAPIError("Kufar is down")
        ads = FakeKufarClient.markets.get(query, SMALL_MARKET)
        return {"total": len(ads), "ads": ads}

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client(monkeypatch):
    from api.dependencies import get_currency_service, get_telegram_user
    from api.main import create_app
    from api.routers import saved_searches

    FakeKufarClient.markets = {}
    FakeKufarClient.failing_queries = set()
    monkeypatch.setattr(saved_searches, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as test_client:
        yield test_client


async def seed_price_drop(session_factory) -> None:
    async with session_factory() as session:
        await ensure_user(session, 123456)
        tracker = Tracker(user_id=123456, query="iphone 15", strict_mode=True)
        session.add(tracker)
        await session.flush()
        session.add(
            TrackerEvent(
                tracker_id=tracker.id,
                user_id=123456,
                ad_id=55,
                query="iphone 15",
                strict_mode=True,
                event_type="price_drop",
                title="iPhone 15 128GB",
                link="https://www.kufar.by/item/55",
                price_byn=1700.0,
                delta_byn=150.0,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def fetch_all(session_factory, statement) -> list:
    async with session_factory() as session:
        return list((await session.execute(statement)).scalars())


def test_saved_searches_crud_and_board(client, monkeypatch) -> None:
    # Deal scores include a freshness bonus, so the clock is frozen next to list_time.
    monkeypatch.setattr(aggregator, "utc_now", lambda: FROZEN_NOW)
    create_response = client.post(
        "/api/v1/saved-searches",
        json={
            "query": "iphone 15 256",
            "group_name": "iPhone набор",
            "strict_mode": True,
            "target_discount_percent": 8,
            "seller_type": "Частное лицо",
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["normalized_query"] == "iphone 15 256"
    assert created["group_name"] == "iPhone набор"
    assert created["strict_mode"] is True
    # The query is not turned into a keyword filter (H2).
    assert created["config_keyword"] is None

    list_response = client.get("/api/v1/saved-searches")
    assert list_response.status_code == 200
    saved_searches_payload = list_response.json()
    assert len(saved_searches_payload) == 1
    assert saved_searches_payload[0]["id"] == created["id"]

    board_response = client.get("/api/v1/opportunity-board", params={"currency": "BYN"})
    assert board_response.status_code == 200
    board = board_response.json()
    # Three listings on the whole market: the deal is a rare offer, shown in one section only.
    assert board["items"] == []
    assert board["rare_opportunities"]
    rare = board["rare_opportunities"][0]
    assert rare["saved_search_id"] == created["id"]
    assert rare["signal_label"] == "Редкий оффер"
    # 55 base + 13.6 (-9% to the 1980 BYN median) + 8 private + 4 fresh + 14 config match.
    assert rare["listing"]["ad_id"] == 1
    assert rare["listing"]["deal_score"] == 94.6
    assert rare["listing"]["deal_verdict"] == "Забирать"
    assert rare["listing"]["deal_reasons"] == ["-9% к медиане", "частник", "свежий", "256GB"]
    assert [signal["subtitle"] for signal in board["market_signals"]] == ["рынок тонкий"]
    assert board["top_price_drops"] == []

    delete_response = client.delete(f"/api/v1/saved-searches/{created['id']}")
    assert delete_response.status_code == 204

    empty_board_response = client.get("/api/v1/opportunity-board", params={"currency": "BYN"})
    assert empty_board_response.status_code == 200
    assert empty_board_response.json()["items"] == []
    assert empty_board_response.json()["rare_opportunities"] == []

    stored = client.portal.call(fetch_all, client.app.state.session_factory, select(SavedSearch))
    assert stored[0].active is False
    assert stored[0].deleted_at is not None


def test_board_regular_market_lists_items_once(client) -> None:
    FakeKufarClient.markets = {"iphone 15 256": LARGE_MARKET}
    client.post("/api/v1/saved-searches", json={"query": "iphone 15 256"})

    board = client.get("/api/v1/opportunity-board").json()
    assert board["currency"] == "BYN"
    assert [item["listing"]["ad_id"] for item in board["items"]] == [10]
    assert board["items"][0]["signal_label"] is None
    assert board["rare_opportunities"] == []
    assert all(signal["subtitle"] != "рынок тонкий" for signal in board["market_signals"])


def test_board_shows_price_drops_without_saved_searches(client) -> None:
    client.portal.call(seed_price_drop, client.app.state.session_factory)

    response = client.get("/api/v1/opportunity-board")
    assert response.status_code == 200
    board = response.json()
    assert board["items"] == []
    assert len(board["top_price_drops"]) == 1
    signal = board["top_price_drops"][0]
    assert signal["query"] == "iphone 15"
    assert signal["metric"] == "-150 BYN"
    # OpportunitySignal.strict_mode comes with the schema change (contract 3); once the field
    # exists, the tracker's strict mode must be carried over.
    if "strict_mode" in signal:
        assert signal["strict_mode"] is True


def test_board_isolates_failing_saved_search(client) -> None:
    FakeKufarClient.markets = {"iphone 15 256": LARGE_MARKET}
    FakeKufarClient.failing_queries = {"broken query"}
    client.post("/api/v1/saved-searches", json={"query": "broken query"})
    client.post("/api/v1/saved-searches", json={"query": "iphone 15 256"})

    response = client.get("/api/v1/opportunity-board")
    assert response.status_code == 200
    assert [item["query"] for item in response.json()["items"]] == ["iphone 15 256"]


def test_board_rejects_unknown_currency(client) -> None:
    assert client.get("/api/v1/opportunity-board", params={"currency": "EUR"}).status_code == 422
    usd = client.get("/api/v1/opportunity-board", params={"currency": "USD"})
    assert usd.status_code == 200
    assert usd.json()["currency"] == "USD"


def test_saved_search_quota(client, monkeypatch) -> None:
    from api.routers import saved_searches

    monkeypatch.setattr(saved_searches, "MAX_ACTIVE_SAVED_SEARCHES_PER_USER", 1)
    assert client.post("/api/v1/saved-searches", json={"query": "iphone 15"}).status_code == 201
    over_limit = client.post("/api/v1/saved-searches", json={"query": "iphone 16"})
    assert over_limit.status_code == 409
    assert "лимит" in over_limit.json()["detail"]


def test_saved_search_path_bounds(client) -> None:
    assert client.delete("/api/v1/saved-searches/0").status_code == 422
    assert client.delete("/api/v1/saved-searches/99999999999").status_code == 422
    assert client.delete("/api/v1/saved-searches/5").status_code == 404
