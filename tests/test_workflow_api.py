from __future__ import annotations

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "total": 3,
            "ads": [
                {
                    "ad_id": 101,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 1800,
                    "ad_link": "https://www.kufar.by/item/101",
                    "list_time": "2026-04-06T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                },
                {
                    "ad_id": 102,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 1950,
                    "ad_link": "https://www.kufar.by/item/102",
                    "list_time": "2026-04-05T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                },
                {
                    "ad_id": 201,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 1800,
                    "ad_link": "https://www.kufar.by/item/201",
                    "list_time": "2026-04-06T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                },
            ],
        }

    async def aclose(self) -> None:
        return None


def test_leads_and_watchlist_workflow(monkeypatch) -> None:
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        lead_response = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 15 128",
                "ad_id": 101,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/101",
                "price_byn": 2000,
                "target_resale_byn": 2250,
                "source": "manual",
            },
        )
        assert lead_response.status_code == 201
        lead = lead_response.json()
        assert lead["status"] == "new"
        assert lead["target_resale_byn"] == 2250
        assert lead["version"] == 1

        update_lead_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "in_progress", "version": lead["version"]},
        )
        assert update_lead_response.status_code == 200
        lead = update_lead_response.json()
        assert lead["status"] == "in_progress"
        assert lead["version"] == 2

        stale_update_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "reviewing", "version": 1},
        )
        assert stale_update_response.status_code == 409
        # OPUS-7: missing version is no longer silently accepted; the
        # frontend always sends one. Old bundles that don't get a 428
        # so the user sees a clear "reload and retry" instead of
        # silently overwriting another tab.
        missing_version_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "reviewing"},
        )
        assert missing_version_response.status_code == 428

        # Up-to-date version still mutates as expected.
        update_lead_status_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "reviewing", "version": lead["version"]},
        )
        assert update_lead_status_response.status_code == 200
        lead = update_lead_status_response.json()
        assert lead["status"] == "reviewing"

        update_lead_meta_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"target_resale_byn": None, "version": lead["version"]},
        )
        assert update_lead_meta_response.status_code == 200
        lead = update_lead_meta_response.json()
        assert lead["target_resale_byn"] is None

        sell_lead_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={
                "status": "sold",
                "buy_price_byn": 1700,
                "sold_price_byn": 2100,
                "version": lead["version"],
            },
        )
        assert sell_lead_response.status_code == 200
        lead = sell_lead_response.json()
        assert lead["status"] == "sold"

        close_lead_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "closed", "version": lead["version"]},
        )
        assert close_lead_response.status_code == 200
        lead = close_lead_response.json()
        assert lead["status"] == "closed"

        list_leads_response = client.get("/api/v1/leads")
        assert list_leads_response.status_code == 200
        assert len(list_leads_response.json()) == 1

        sold_lead_response = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 14 pro max",
                "ad_id": 103,
                "title": "iPhone 14 Pro Max",
                "link": "https://www.kufar.by/item/103",
                "price_byn": 1900,
                "source": "manual",
            },
        )
        assert sold_lead_response.status_code == 201
        sold_lead = sold_lead_response.json()
        # OPUS-7: legacy "sell" path now also requires the version
        # like every other PATCH; freshly-created leads have version=1.
        legacy_sell_response = client.patch(
            f"/api/v1/leads/{sold_lead['id']}",
            json={
                "status": "sold",
                "buy_price_byn": 1800,
                "sold_price_byn": 2200,
                "version": sold_lead["version"],
            },
        )
        assert legacy_sell_response.status_code == 200
        assert legacy_sell_response.json()["status"] == "sold"

        delete_active_leads_response = client.delete("/api/v1/leads/all")
        assert delete_active_leads_response.status_code == 204
        remaining_leads = client.get("/api/v1/leads").json()
        assert len(remaining_leads) == 1
        assert remaining_leads[0]["status"] == "closed"

        # Use a fresh ad_id (201) for watchlist — the leads/watchlist tables
        # were merged in 20260427_0001 so (user_id, ad_id) is unique. Trying
        # to add ad_id=101 to watchlist would collide with the closed lead
        # we kept above.
        watchlist_response = client.post(
            "/api/v1/watchlist",
            json={
                "query": "iphone 15 128",
                "ad_id": 201,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/201",
                "price_byn": 2000,
            },
        )
        assert watchlist_response.status_code == 201
        watchlist_item = watchlist_response.json()
        assert watchlist_item["initial_price_byn"] == 2000
        # workflow_status is now a no-op constant after watchlist→leads merge.
        assert watchlist_item["workflow_status"] == "default"

        update_watchlist_response = client.patch(
            f"/api/v1/watchlist/{watchlist_item['id']}",
            json={
                "workflow_status": "reviewing",
                "notes": "сравнить вечером",
                "version": watchlist_item["version"],
            },
        )
        assert update_watchlist_response.status_code == 200
        # workflow_status is intentionally fixed at "default"; only notes mutates.
        assert update_watchlist_response.json()["workflow_status"] == "default"
        assert update_watchlist_response.json()["notes"] == "сравнить вечером"

        refresh_response = client.post("/api/v1/watchlist/refresh", json={})
        assert refresh_response.status_code == 200
        refresh_data = refresh_response.json()
        assert refresh_data["updated"] >= 1

        list_watchlist_response = client.get("/api/v1/watchlist")
        assert list_watchlist_response.status_code == 200
        items = list_watchlist_response.json()
        assert len(items) == 1
        assert items[0]["current_price_byn"] == 1800
        assert items[0]["price_delta_byn"] == -200
        assert items[0]["market_status"] == "price_drop"

        # Sparkline source: the watchlist response now inlines a
        # bounded price-history series so the card can render a
        # trend SVG without a per-row round-trip. After /watchlist +
        # one /watchlist/refresh that saw the price move 2000→1800,
        # we expect at least two distinct points (initial + drop).
        history = items[0]["price_history"]
        assert isinstance(history, list)
        assert len(history) >= 2, history
        assert {round(p["price_byn"]) for p in history} == {2000, 1800}
        # Newest-last ordering — the helper expects ascending snapped_at.
        timestamps = [p["snapped_at"] for p in history]
        assert timestamps == sorted(timestamps)

        delete_response = client.delete(f"/api/v1/watchlist/{watchlist_item['id']}")
        assert delete_response.status_code == 204
        assert client.get("/api/v1/watchlist").json() == []

        legacy_watchlist_response = client.post(
            "/api/v1/watchlist",
            json={
                "query": "iphone 15 128",
                "ad_id": 202,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/202",
                "price_byn": 1990,
            },
        )
        assert legacy_watchlist_response.status_code == 201
        legacy_watchlist_item = legacy_watchlist_response.json()
        # OPUS-7: explicit version is required, freshly-created
        # watchlist items start at version=1.
        legacy_promote_response = client.patch(
            f"/api/v1/leads/{legacy_watchlist_item['id']}",
            json={"status": "new", "version": legacy_watchlist_item["version"]},
        )
        assert legacy_promote_response.status_code == 200
        assert legacy_promote_response.json()["status"] == "new"
        assert client.get("/api/v1/watchlist").json() == []


def test_create_lead_accepts_long_kufar_link(monkeypatch) -> None:
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    long_link = "https://www.kufar.by/item/long?" + ("utm_campaign=rafuk&" * 40)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 15 128",
                "ad_id": 10_101,
                "title": "iPhone 15 128GB",
                "link": long_link,
                "price_byn": 2000,
                "source": "manual",
            },
        )

    assert len(long_link) > 512
    assert len(long_link) <= 2048
    assert response.status_code == 201
    assert response.json()["link"] == long_link



def test_lead_status_state_machine_blocks_invalid_transitions(monkeypatch) -> None:
    """LOGIC-HIGH (issues §11.4): update_lead must reject status
    transitions outside the deal-pipeline state machine.

    Specifically ``sold`` is terminal except for archiving via
    ``closed``, and ``watching`` cannot jump straight to ``sold`` /
    ``bought`` (must pass through one of the active deal lanes).
    """
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        # Create a lead, sell it, then try to bounce it back to "new".
        created = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 15 128",
                "ad_id": 9001,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/9001",
                "price_byn": 2000,
                "source": "manual",
            },
        )
        assert created.status_code == 201
        lead = created.json()

        sold = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={
                "status": "sold",
                "buy_price_byn": 1800,
                "sold_price_byn": 2100,
                "version": lead["version"],
            },
        )
        assert sold.status_code == 200
        sold_lead = sold.json()
        assert sold_lead["status"] == "sold"

        # sold → new is blocked (only sold → closed is allowed).
        invalid = client.patch(
            f"/api/v1/leads/{sold_lead['id']}",
            json={"status": "new", "version": sold_lead["version"]},
        )
        assert invalid.status_code == 422

        # sold → closed (archive) is allowed.
        archived = client.patch(
            f"/api/v1/leads/{sold_lead['id']}",
            json={"status": "closed", "version": sold_lead["version"]},
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "closed"

    # Watchlist → sold transition is blocked by the helper (the
    # /leads PATCH wouldn't normally surface watching items, so we
    # exercise the state-machine helper directly).
    import pytest as _pytest
    from fastapi import HTTPException

    from api.routers.workflow import _validate_lead_status_transition

    with _pytest.raises(HTTPException) as exc:
        _validate_lead_status_transition("watching", "sold")
    assert exc.value.status_code == 422
    with _pytest.raises(HTTPException):
        _validate_lead_status_transition("watching", "bought")
    # watching → reviewing is OK
    _validate_lead_status_transition("watching", "reviewing")
    # closed → reviewing reactivation is OK
    _validate_lead_status_transition("closed", "reviewing")
    # closed → sold is blocked
    with _pytest.raises(HTTPException):
        _validate_lead_status_transition("closed", "sold")


def test_lead_double_sale_protection(monkeypatch) -> None:
    """LOGIC-MEDIUM (issues §11.4): once sold_price_byn is recorded,
    overwriting it with a different value must require an explicit
    revert (``sold_price_byn=None``) first."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/leads",
            json={
                "query": "ipad",
                "ad_id": 9100,
                "title": "iPad Air",
                "link": "https://www.kufar.by/item/9100",
                "price_byn": 1500,
                "source": "manual",
            },
        )
        lead = created.json()
        first_sale = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={
                "status": "sold",
                "buy_price_byn": 1400,
                "sold_price_byn": 1700,
                "version": lead["version"],
            },
        )
        assert first_sale.status_code == 200
        sold_lead = first_sale.json()

        # Trying to overwrite sold_price with a DIFFERENT amount: 409.
        clobber = client.patch(
            f"/api/v1/leads/{sold_lead['id']}",
            json={"sold_price_byn": 1900, "version": sold_lead["version"]},
        )
        assert clobber.status_code == 409

        # Same amount is idempotent: 200.
        idem = client.patch(
            f"/api/v1/leads/{sold_lead['id']}",
            json={"sold_price_byn": 1700, "version": sold_lead["version"]},
        )
        assert idem.status_code == 200

        # Revert to bought via None, then re-sell at a new price.
        revert = client.patch(
            f"/api/v1/leads/{idem.json()['id']}",
            json={"sold_price_byn": None, "version": idem.json()["version"]},
        )
        assert revert.status_code == 200
        assert revert.json()["status"] == "bought"
        re_sold = client.patch(
            f"/api/v1/leads/{revert.json()['id']}",
            json={
                "status": "sold",
                "sold_price_byn": 1900,
                "version": revert.json()["version"],
            },
        )
        assert re_sold.status_code == 200
        assert re_sold.json()["sold_price_byn"] == 1900
