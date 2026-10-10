from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.middleware.telegram_auth import TelegramInitData
from api.models import LeadItem, User, WatchlistItem
from api.services.kufar_client import KufarAPIError


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


LEAD_PAYLOAD = {
    "query": "iphone 15 128",
    "ad_id": 101,
    "title": "iPhone 15 128GB",
    "link": "https://www.kufar.by/item/101",
    "price_byn": 2000,
}


def make_ad(ad_id: int, price_byn: int, *, seller: str = "Частное лицо") -> dict:
    return {
        "ad_id": ad_id,
        "subject": "iPhone 15 128GB",
        "price_byn": price_byn,
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "list_time": "2026-04-06T10:00:00",
        "region_id": 6,
        "ad_parameters": [{"p": "seller_type", "v": seller}],
    }


class FakeKufarClient:
    calls: list[str] = []
    failing_queries: set[str] = set()

    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        query = kwargs.get("query", "")
        FakeKufarClient.calls.append(query)
        if query in FakeKufarClient.failing_queries:
            raise KufarAPIError("Kufar is down")
        return {
            "total": 2,
            "ads": [make_ad(101, 180000), make_ad(102, 195000, seller="Магазин")],
        }

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client(monkeypatch):
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    FakeKufarClient.calls = []
    FakeKufarClient.failing_queries = set()
    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    with TestClient(app) as test_client:
        yield test_client


async def fetch_all(session_factory, statement) -> list:
    async with session_factory() as session:
        return list((await session.execute(statement)).scalars())


async def insert_watchlist_item(session_factory, *, ad_id: int, query: str) -> None:
    async with session_factory() as session:
        session.add(
            WatchlistItem(
                user_id=123456,
                ad_id=ad_id,
                query=query,
                title="iPhone 15 128GB",
                link=f"https://www.kufar.by/item/{ad_id}",
                initial_price_byn=2000,
                current_price_byn=2000,
            )
        )
        await session.commit()


def test_leads_and_watchlist_workflow(client) -> None:
    lead_response = client.post(
        "/api/v1/leads",
        json={**LEAD_PAYLOAD, "target_resale_byn": 2250, "source": "manual"},
    )
    assert lead_response.status_code == 201
    lead = lead_response.json()
    assert lead["status"] == "new"
    assert lead["target_resale_byn"] == 2250

    update_lead_response = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"status": "in_progress", "notes": "созвониться"},
    )
    assert update_lead_response.status_code == 200
    assert update_lead_response.json()["status"] == "in_progress"

    update_lead_meta_response = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"target_resale_byn": None, "notes": "созвониться сегодня"},
    )
    assert update_lead_meta_response.status_code == 200
    assert update_lead_meta_response.json()["target_resale_byn"] is None
    assert update_lead_meta_response.json()["notes"] == "созвониться сегодня"

    list_leads_response = client.get("/api/v1/leads")
    assert list_leads_response.status_code == 200
    assert len(list_leads_response.json()) == 1

    watchlist_response = client.post("/api/v1/watchlist", json=LEAD_PAYLOAD)
    assert watchlist_response.status_code == 201
    watchlist_item = watchlist_response.json()
    assert watchlist_item["initial_price_byn"] == 2000
    assert watchlist_item["workflow_status"] == "watching"

    update_watchlist_response = client.patch(
        f"/api/v1/watchlist/{watchlist_item['id']}",
        json={"workflow_status": "reviewing", "notes": "сравнить вечером"},
    )
    assert update_watchlist_response.status_code == 200
    assert update_watchlist_response.json()["workflow_status"] == "reviewing"
    assert update_watchlist_response.json()["notes"] == "сравнить вечером"

    refresh_response = client.post("/api/v1/watchlist/refresh", json={})
    assert refresh_response.status_code == 200
    assert refresh_response.json() == {"updated": 1, "missing": 0, "price_drops": 1}

    list_watchlist_response = client.get("/api/v1/watchlist")
    assert list_watchlist_response.status_code == 200
    items = list_watchlist_response.json()
    assert len(items) == 1
    assert items[0]["current_price_byn"] == 1800
    assert items[0]["price_delta_byn"] == -200
    assert items[0]["market_status"] == "price_drop"

    delete_response = client.delete(f"/api/v1/watchlist/{watchlist_item['id']}")
    assert delete_response.status_code == 204
    assert client.get("/api/v1/watchlist").json() == []

    users = asyncio.run(fetch_all(client.app.state.session_factory, select(User)))
    assert [(user.telegram_user_id, user.first_name) for user in users] == [(123456, "Test")]


def test_repeated_posts_do_not_fail(client) -> None:
    first = client.post("/api/v1/leads", json=LEAD_PAYLOAD)
    assert first.status_code == 201
    second = client.post("/api/v1/leads", json={**LEAD_PAYLOAD, "price_byn": 1900})
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["price_byn"] == 1900
    assert second.json()["updated_at"]

    watch_first = client.post("/api/v1/watchlist", json=LEAD_PAYLOAD)
    assert watch_first.status_code == 201
    watch_second = client.post("/api/v1/watchlist", json={**LEAD_PAYLOAD, "price_byn": 1950})
    assert watch_second.status_code == 201
    assert watch_second.json()["id"] == watch_first.json()["id"]
    assert watch_second.json()["initial_price_byn"] == 2000
    assert watch_second.json()["current_price_byn"] == 1950


def test_repeated_lead_post_keeps_status_and_source(client) -> None:
    lead = client.post("/api/v1/leads", json={**LEAD_PAYLOAD, "source": "manual"}).json()
    assert lead["status"] == "new"
    assert lead["source"] == "manual"
    bought = client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "bought"})
    assert bought.status_code == 200

    # The Mini App always sends status "new" and its own source.
    again = client.post(
        "/api/v1/leads",
        json={**LEAD_PAYLOAD, "status": "new", "source": "watchlist", "price_byn": 1700},
    )
    assert again.status_code == 201
    assert again.json()["status"] == "bought"
    assert again.json()["source"] == "manual"
    assert again.json()["price_byn"] == 1700

    # Even an explicit in-progress status does not downgrade a bought lead.
    again = client.post("/api/v1/leads", json={**LEAD_PAYLOAD, "status": "in_progress"})
    assert again.status_code == 201
    assert again.json()["status"] == "bought"


def test_lead_defaults_without_status_and_source(client) -> None:
    response = client.post("/api/v1/leads", json=dict(LEAD_PAYLOAD))
    assert response.status_code == 201
    assert response.json()["status"] == "new"
    assert response.json()["source"] == "manual"


def test_patch_with_null_status_keeps_value(client) -> None:
    lead = client.post("/api/v1/leads", json=LEAD_PAYLOAD).json()
    response = client.patch(f"/api/v1/leads/{lead['id']}", json={"status": None})
    assert response.status_code in {200, 422}
    assert client.get("/api/v1/leads").json()[0]["status"] == "new"

    item = client.post("/api/v1/watchlist", json=LEAD_PAYLOAD).json()
    response = client.patch(f"/api/v1/watchlist/{item['id']}", json={"workflow_status": None})
    assert response.status_code in {200, 422}
    assert client.get("/api/v1/watchlist").json()[0]["workflow_status"] == "watching"


def test_path_ids_are_bounded(client) -> None:
    assert client.patch("/api/v1/leads/0", json={"notes": "x"}).status_code == 422
    assert client.patch("/api/v1/leads/99999999999", json={"notes": "x"}).status_code == 422
    assert client.patch("/api/v1/watchlist/99999999999", json={"notes": "x"}).status_code == 422
    assert client.delete("/api/v1/watchlist/99999999999").status_code == 422
    assert client.delete("/api/v1/watchlist/2147483647").status_code == 404


def test_watchlist_quota(client, monkeypatch) -> None:
    from api.services import workflow_store

    monkeypatch.setattr(workflow_store, "MAX_WATCHLIST_ITEMS_PER_USER", 1)
    assert client.post("/api/v1/watchlist", json=LEAD_PAYLOAD).status_code == 201
    # Updating an existing item is not limited.
    assert client.post("/api/v1/watchlist", json=LEAD_PAYLOAD).status_code == 201
    over_limit = client.post("/api/v1/watchlist", json={**LEAD_PAYLOAD, "ad_id": 102})
    assert over_limit.status_code == 409
    assert "избранном" in over_limit.json()["detail"]


def test_price_drop_signal_survives_next_refresh(client) -> None:
    client.post("/api/v1/watchlist", json=LEAD_PAYLOAD)

    first = client.post("/api/v1/watchlist/refresh", json={})
    assert first.json() == {"updated": 1, "missing": 0, "price_drops": 1}
    second = client.post("/api/v1/watchlist/refresh", json={})
    assert second.json() == {"updated": 1, "missing": 0, "price_drops": 1}

    item = client.get("/api/v1/watchlist").json()[0]
    assert item["market_status"] == "price_drop"
    assert item["price_delta_byn"] == -200


def test_refresh_without_drop_marks_active(client) -> None:
    client.post("/api/v1/watchlist", json={**LEAD_PAYLOAD, "price_byn": 1800})
    response = client.post("/api/v1/watchlist/refresh", json={})
    assert response.json() == {"updated": 1, "missing": 0, "price_drops": 0}
    assert client.get("/api/v1/watchlist").json()[0]["market_status"] == "active"


def test_refresh_skips_empty_queries_and_isolates_failures(client) -> None:
    client.post("/api/v1/watchlist", json={**LEAD_PAYLOAD, "query": "iphone 15 128"})
    client.post("/api/v1/watchlist", json={**LEAD_PAYLOAD, "ad_id": 102, "query": "broken"})
    # An empty query (legacy row) must never be sent to Kufar as a catalogue-wide search.
    asyncio.run(insert_watchlist_item(client.app.state.session_factory, ad_id=103, query="   "))
    FakeKufarClient.failing_queries = {"broken"}

    response = client.post("/api/v1/watchlist/refresh", json={})
    assert response.status_code == 200
    assert response.json() == {"updated": 1, "missing": 0, "price_drops": 1}
    assert sorted(FakeKufarClient.calls) == ["broken", "iphone 15 128"]

    statuses = {
        item["ad_id"]: item["market_status"] for item in client.get("/api/v1/watchlist").json()
    }
    assert statuses == {101: "price_drop", 102: "active", 103: "active"}


def test_refresh_reports_error_when_every_query_fails(monkeypatch) -> None:
    from api.config import get_settings
    from api.routers import workflow

    FakeKufarClient.calls = []
    FakeKufarClient.failing_queries = {"a", "b"}
    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    with pytest.raises(KufarAPIError):
        asyncio.run(workflow._load_watchlist_datasets(["a", "b"], settings=get_settings()))


async def test_upsert_lead_recovers_from_concurrent_insert(monkeypatch) -> None:
    from api.database import get_engine, get_session_factory
    from api.services import workflow_store

    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with session_factory() as session:
        session.add(
            LeadItem(
                user_id=123456,
                ad_id=101,
                query="iphone",
                title="iPhone",
                link="https://www.kufar.by/item/101",
                price_byn=2000,
                status="bought",
                source="manual",
            )
        )
        await session.commit()

    # Simulate the race: the first read misses the row another request has just inserted.
    real_get_lead = workflow_store._get_lead
    calls = {"count": 0}

    async def racing_get_lead(session, user_id, ad_id):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return await real_get_lead(session, user_id, ad_id)

    monkeypatch.setattr(workflow_store, "_get_lead", racing_get_lead)
    async with session_factory() as session:
        lead = await workflow_store.upsert_lead(
            session,
            user_id=123456,
            ad_id=101,
            query="iphone",
            title="iPhone 15",
            link="https://www.kufar.by/item/101",
            price_byn=1900,
            status="in_progress",
            source="telegram_alert",
        )
        await session.commit()
        assert calls["count"] == 2

    leads = await fetch_all(session_factory, select(LeadItem))
    assert len(leads) == 1
    assert leads[0].id == lead.id
    assert (leads[0].status, leads[0].source, leads[0].price_byn) == ("bought", "manual", 1900)
    await engine.dispose()


async def test_upsert_lead_status_transitions() -> None:
    from api.database import get_engine, get_session_factory
    from api.services.workflow_store import upsert_lead

    engine = get_engine()
    session_factory = get_session_factory(engine)

    async def upsert(status: str | None, source: str | None = "telegram_alert") -> LeadItem:
        async with session_factory() as session:
            lead = await upsert_lead(
                session,
                user_id=1,
                ad_id=7,
                query="q",
                title="t",
                link="https://www.kufar.by/item/7",
                price_byn=10,
                status=status,
                source=source,
            )
            await session.commit()
            return lead

    assert (await upsert(None, None)).status == "new"
    assert (await upsert("deferred")).status == "deferred"
    lead = await upsert("in_progress")
    assert (lead.status, lead.source) == ("in_progress", "telegram_alert")
    # Once in work, alert buttons and repeated adds keep the status.
    assert (await upsert("deferred")).status == "in_progress"
    assert (await upsert("new")).status == "in_progress"
    await engine.dispose()
