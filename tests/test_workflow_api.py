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
    # E-FIND-06: watching → bought is now allowed (direct purchase from watchlist).
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


def test_update_lead_notes_round_trip(monkeypatch) -> None:
    """A-1: PATCH /leads/{id} now respects ``notes`` (used to be
    silently dropped because update_lead never checked the field).
    Sending None clears it; omitting the field leaves it untouched.
    """
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/leads",
            json={
                "query": "macbook",
                "ad_id": 7777,
                "title": "MacBook",
                "link": "https://www.kufar.by/item/7777",
                "price_byn": 3000,
                "source": "manual",
            },
        )
        assert create.status_code == 201
        lead = create.json()

        # Set notes via PATCH.
        set_notes = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"notes": "haggle hard", "version": lead["version"]},
        )
        assert set_notes.status_code == 200
        assert set_notes.json()["notes"] == "haggle hard"

        # Omitting notes must NOT clear them.
        bumped = set_notes.json()
        keep = client.patch(
            f"/api/v1/leads/{bumped['id']}",
            json={"target_resale_byn": 3500, "version": bumped["version"]},
        )
        assert keep.status_code == 200
        assert keep.json()["notes"] == "haggle hard"

        # Explicit null clears notes.
        bumped2 = keep.json()
        cleared = client.patch(
            f"/api/v1/leads/{bumped2['id']}",
            json={"notes": None, "version": bumped2["version"]},
        )
        assert cleared.status_code == 200
        assert cleared.json()["notes"] is None


def test_update_lead_stamps_bought_at_on_bought_transition(monkeypatch) -> None:
    """E-FIND-02: status → 'bought' must populate bought_at, and the
    LeadRead response must surface a hold_time_days value derived
    from it. Idempotent re-bought does NOT re-stamp."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/leads",
            json={
                "query": "lens", "ad_id": 8001,
                "title": "Sigma 18-35", "link": "https://www.kufar.by/item/8001",
                "price_byn": 800, "source": "manual",
            },
        )
        lead = create.json()
        assert lead["bought_at"] is None
        assert lead["hold_time_days"] is None

        bought = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "bought", "version": lead["version"]},
        )
        assert bought.status_code == 200
        body = bought.json()
        assert body["bought_at"] is not None
        assert body["hold_time_days"] == 0  # bought just now

        # Idempotent: re-PATCH bought → bought (well, status unchanged
        # but explicitly listed) keeps the original bought_at.
        original_bought_at = body["bought_at"]
        idem = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"target_resale_byn": 1000, "version": body["version"]},
        )
        assert idem.json()["bought_at"] == original_bought_at


def test_update_lead_stamps_bought_at_on_skip_to_sold(monkeypatch) -> None:
    """E-FIND-02: a lead that goes straight from new → sold (skipping
    bought) gets bought_at retroactively stamped at sold_at so
    hold_time_days renders as 0 instead of NULL."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/leads",
            json={
                "query": "drone", "ad_id": 8002,
                "title": "DJI Mini", "link": "https://www.kufar.by/item/8002",
                "price_byn": 1500, "source": "manual",
            },
        )
        lead = create.json()
        # Skip bought, go straight to sold via sold_price_byn.
        sold = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"sold_price_byn": 1700, "version": lead["version"]},
        )
        body = sold.json()
        assert body["status"] == "sold"
        assert body["sold_at"] is not None
        assert body["bought_at"] is not None
        # M14: instant flip (bought_at == sold_at, same second) returns None
        # instead of 0 to indicate "instant flip".
        assert body["hold_time_days"] is None


def test_lead_projected_profit_from_target_resale(monkeypatch) -> None:
    """E-FIND-01: unsold leads with target_resale_byn show projected_profit_byn."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/leads",
            json={
                "query": "test", "ad_id": 9001,
                "title": "Test", "link": "https://www.kufar.by/item/9001",
                "price_byn": 1000,
                "target_resale_byn": 1500, "source": "manual",
            },
        )
        lead = create.json()
        # Set buy_price via update
        updated = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"buy_price_byn": 1000, "version": lead["version"]},
        )
        body = updated.json()
        assert body["projected_profit_byn"] == 500.0
        assert body["actual_profit"] is None


def test_lead_incomplete_cost_basis_when_sold_without_buy_price(monkeypatch) -> None:
    """E-FIND-09: sold lead with buy_price=None → incomplete_cost_basis=True."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/leads",
            json={
                "query": "test", "ad_id": 9002,
                "title": "Test", "link": "https://www.kufar.by/item/9002",
                "price_byn": 1000, "source": "manual",
            },
        )
        lead = create.json()
        # Sell without setting buy_price
        sold = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"sold_price_byn": 1500, "version": lead["version"]},
        )
        body = sold.json()
        assert body["incomplete_cost_basis"] is True
        assert body["actual_profit"] is None
        assert body["roi_percent"] is None


def test_watchlist_patch_optimistic_lock_409(monkeypatch) -> None:
    """G-01: two PATCHes with the same version — first succeeds, second 409."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/watchlist",
            json={
                "query": "iphone 15 128",
                "ad_id": 501,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/501",
                "price_byn": 1800,
            },
        )
        assert created.status_code == 201
        item = created.json()
        v = item["version"]

        first = client.patch(
            f"/api/v1/watchlist/{item['id']}",
            json={"notes": "first", "version": v},
        )
        assert first.status_code == 200

        second = client.patch(
            f"/api/v1/watchlist/{item['id']}",
            json={"notes": "second", "version": v},
        )
        assert second.status_code == 409


def test_create_watchlist_duplicate_409(monkeypatch) -> None:
    """BE-DEEP-8: POST /watchlist for an ad_id already in 'reviewing' returns 409."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        # Create a lead and promote it to 'reviewing' so it's non-watching.
        created = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 15 128",
                "ad_id": 601,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/601",
                "price_byn": 1900,
                "source": "manual",
            },
        )
        assert created.status_code == 201
        lead = created.json()
        promoted = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "reviewing", "version": lead["version"]},
        )
        assert promoted.status_code == 200

        # Now POST /watchlist with the same ad_id — must 409.
        dup = client.post(
            "/api/v1/watchlist",
            json={
                "query": "iphone 15 128",
                "ad_id": 601,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/601",
                "price_byn": 1900,
            },
        )
        assert dup.status_code == 409
        assert "уже в покупках" in dup.json()["detail"]


def test_update_lead_with_null_status_is_noop(monkeypatch) -> None:
    """BE-DEEP-10: PATCH with {"status": null} must not crash; treat as no-op."""
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
                "query": "iphone 15",
                "ad_id": 701,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/701",
                "price_byn": 2000,
                "source": "manual",
            },
        )
        assert created.status_code == 201
        lead = created.json()
        assert lead["status"] == "new"

        resp = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": None, "version": lead["version"]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "new"


def test_is_sold_field_in_lead_responses(monkeypatch) -> None:
    """LOGIC-NEW-3: /leads responses include is_sold matching
    sold_price_byn is not None or status == 'sold'."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        # 1) Sold lead with sold_price set → is_sold=True
        resp = client.post(
            "/api/v1/leads",
            json={
                "query": "sold test",
                "ad_id": 80001,
                "title": "Sold item",
                "link": "https://www.kufar.by/item/80001",
                "price_byn": 500,
                "source": "manual",
            },
        )
        assert resp.status_code == 201
        lead = resp.json()
        sold_resp = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={
                "status": "sold",
                "buy_price_byn": 400,
                "sold_price_byn": 600,
                "version": lead["version"],
            },
        )
        assert sold_resp.status_code == 200
        assert sold_resp.json()["is_sold"] is True

        # 2) Bought lead (not sold) → is_sold=False
        resp2 = client.post(
            "/api/v1/leads",
            json={
                "query": "bought test",
                "ad_id": 80002,
                "title": "Bought item",
                "link": "https://www.kufar.by/item/80002",
                "price_byn": 300,
                "status": "bought",
                "source": "manual",
                "buy_price_byn": 300,
            },
        )
        assert resp2.status_code == 201
        assert resp2.json()["is_sold"] is False

        # 3) New lead (watching-like) → is_sold=False
        resp3 = client.post(
            "/api/v1/leads",
            json={
                "query": "new test",
                "ad_id": 80003,
                "title": "New item",
                "link": "https://www.kufar.by/item/80003",
                "price_byn": 200,
                "source": "manual",
            },
        )
        assert resp3.status_code == 201
        assert resp3.json()["is_sold"] is False

        # Verify list endpoint also includes is_sold
        leads_list = client.get("/api/v1/leads").json()
        sold_leads = [x for x in leads_list if x["id"] == lead["id"]]
        assert sold_leads[0]["is_sold"] is True


def test_incomplete_projection_when_no_buy_price(monkeypatch) -> None:
    """LOGIC-NEW-8: projected_profit_byn is null and incomplete_projection is true
    when buy_price_byn is unknown but target_resale_byn is set."""
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)

    with TestClient(app) as client:
        # Create a lead with target_resale but no buy_price
        resp = client.post(
            "/api/v1/leads",
            json={
                "query": "incomplete proj",
                "ad_id": 90001,
                "title": "No buy price",
                "link": "https://www.kufar.by/item/90001",
                "price_byn": 500,
                "status": "bought",
                "source": "manual",
                "target_resale_byn": 800,
                # buy_price_byn intentionally omitted
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["projected_profit_byn"] is None
        assert data["incomplete_projection"] is True


def test_delete_all_leads_deletes_sold(monkeypatch) -> None:
    """L10: verify sold leads ARE deleted by delete_all_leads (only closed
    and watching are preserved)."""
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
                "query": "sold delete test",
                "ad_id": 41001,
                "title": "Sold Item",
                "link": "https://www.kufar.by/item/41001",
                "price_byn": 1000,
                "source": "manual",
            },
        )
        assert created.status_code == 201
        lead = created.json()

        sold = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={
                "status": "sold",
                "buy_price_byn": 900,
                "sold_price_byn": 1200,
                "version": lead["version"],
            },
        )
        assert sold.status_code == 200
        sold_lead = sold.json()
        assert sold_lead["status"] == "sold"

        client.delete("/api/v1/leads/all")
        remaining = client.get("/api/v1/leads").json()
        sold_items = [r for r in remaining if r["id"] == sold_lead["id"]]
        assert len(sold_items) == 0


def test_delete_watchlist_item_after_promotion_idempotent(monkeypatch) -> None:
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/watchlist",
            json={
                "query": "iphone 15 128",
                "ad_id": 301,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/301",
                "price_byn": 1800,
            },
        )
        assert created.status_code == 201
        item = created.json()

        promoted = client.patch(
            f"/api/v1/leads/{item['id']}",
            json={"status": "new", "version": item["version"]},
        )
        assert promoted.status_code == 200
        assert promoted.json()["status"] == "new"

        delete_resp = client.delete(f"/api/v1/watchlist/{item['id']}")
        assert delete_resp.status_code == 204
