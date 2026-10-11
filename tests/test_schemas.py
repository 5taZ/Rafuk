from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.middleware.telegram_auth import TelegramInitData
from api.schemas import (
    CurrencyRatesResponse,
    LeadCreate,
    LeadRead,
    LeadUpdate,
    OpportunitySignal,
    SavedSearchCreate,
    TrackerCreate,
    WatchlistCreate,
    WatchlistUpdate,
)
from api.statuses import (
    LEAD_STATUSES,
    MARKET_STATUSES,
    WATCHLIST_STATUSES,
)

LEAD_PAYLOAD = {
    "query": "iphone 15 128",
    "ad_id": 101,
    "title": "iPhone 15 128GB",
    "link": "https://www.kufar.by/item/101",
    "price_byn": 1800,
}


def test_canonical_status_vocabularies() -> None:
    assert LEAD_STATUSES == (
        "new",
        "reviewing",
        "in_progress",
        "negotiating",
        "deferred",
        "bought",
        "reselling",
        "sold",
        "skipped",
    )
    assert WATCHLIST_STATUSES == ("watching", "reviewing", "in_progress", "deferred", "skipped")
    assert MARKET_STATUSES == ("active", "missing", "price_drop", "duplicate")


def test_lead_create_defaults_keep_existing_status_and_source() -> None:
    lead = LeadCreate(**LEAD_PAYLOAD)
    assert lead.status is None
    assert lead.source is None
    assert LeadCreate(**LEAD_PAYLOAD, status="negotiating").status == "negotiating"


@pytest.mark.parametrize("status", ["closed", "abandoned", "NEW", ""])
def test_lead_status_outside_vocabulary_is_rejected(status: str) -> None:
    with pytest.raises(ValidationError):
        LeadCreate(**LEAD_PAYLOAD, status=status)
    with pytest.raises(ValidationError):
        LeadUpdate(status=status)


def test_update_null_status_means_unchanged() -> None:
    lead_update = LeadUpdate.model_validate({"status": None, "notes": None})
    assert "status" not in lead_update.model_fields_set
    assert "notes" in lead_update.model_fields_set

    watch_update = WatchlistUpdate.model_validate({"workflow_status": None})
    assert "workflow_status" not in watch_update.model_fields_set
    assert WatchlistUpdate(workflow_status="deferred").workflow_status == "deferred"
    with pytest.raises(ValidationError):
        WatchlistUpdate(workflow_status="lead")


@pytest.mark.parametrize("query", ["", "   ", "x" * 256])
def test_queries_must_be_non_blank_and_fit_column(query: str) -> None:
    with pytest.raises(ValidationError):
        TrackerCreate(query=query)
    with pytest.raises(ValidationError):
        SavedSearchCreate(query=query)
    with pytest.raises(ValidationError):
        WatchlistCreate(**{**LEAD_PAYLOAD, "query": query})


def test_query_is_stripped() -> None:
    assert TrackerCreate(query="  iphone 15  ").query == "iphone 15"


@pytest.mark.parametrize(
    "link",
    ["javascript:alert(1)", "ftp://kufar.by/item/1", "kufar.by/item/1", "https://" + "a" * 510],
)
def test_links_must_be_http_urls_within_column(link: str) -> None:
    with pytest.raises(ValidationError):
        LeadCreate(**{**LEAD_PAYLOAD, "link": link})
    with pytest.raises(ValidationError):
        WatchlistCreate(**{**LEAD_PAYLOAD, "link": link})


@pytest.mark.parametrize("link", ["", "   ", None])
def test_empty_link_is_allowed_for_listings_without_ad_link(link: str | None) -> None:
    # Listings without ad_link carry link=""; the router fills the canonical item URL.
    assert LeadCreate(**{**LEAD_PAYLOAD, "link": link}).link == ""
    assert WatchlistCreate(**{**LEAD_PAYLOAD, "link": link}).link == ""


def test_missing_link_defaults_to_empty() -> None:
    payload = {key: value for key, value in LEAD_PAYLOAD.items() if key != "link"}
    assert LeadCreate(**payload).link == ""
    assert WatchlistCreate(**payload).link == ""


def test_link_is_stripped() -> None:
    lead = LeadCreate(**{**LEAD_PAYLOAD, "link": "  https://www.kufar.by/item/101  "})
    assert lead.link == "https://www.kufar.by/item/101"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (TrackerCreate, {"query": "iphone\x00"}),
        (TrackerCreate, {"query": "iphone", "region_name": "Минск\x00"}),
        (TrackerCreate, {"query": "iphone", "config_keyword": "a\x00b"}),
        (TrackerCreate, {"query": "iphone", "seller_type": "\x00"}),
        (SavedSearchCreate, {"query": "iphone", "name": "x\x00"}),
        (SavedSearchCreate, {"query": "iphone", "group_name": "x\x00"}),
        (LeadCreate, {**LEAD_PAYLOAD, "title": "iPhone\x00"}),
        (LeadCreate, {**LEAD_PAYLOAD, "link": "https://www.kufar.by/item/1\x00"}),
        (LeadCreate, {**LEAD_PAYLOAD, "notes": "a\x00b"}),
        (LeadCreate, {**LEAD_PAYLOAD, "source": "bot\x00"}),
        (LeadUpdate, {"notes": "a\x00b"}),
        (WatchlistCreate, {**LEAD_PAYLOAD, "query": "x\x00y"}),
        (WatchlistUpdate, {"notes": "\x00"}),
    ],
)
def test_nul_characters_are_rejected(model: type, payload: dict[str, object]) -> None:
    # Postgres text columns cannot store 0x00: reject it as a 422 instead of a 500 (r3-core-3).
    with pytest.raises(ValidationError, match="NUL"):
        model.model_validate(payload)


def test_search_query_validator_rejects_nul() -> None:
    from api.validators import NUL_CHARACTER_MESSAGE, validate_query

    with pytest.raises(ValueError, match=NUL_CHARACTER_MESSAGE):
        validate_query("iphone\x00")
    assert validate_query("  iphone  ") == "iphone"


def test_response_schemas_expose_partial_result_flags() -> None:
    from api.schemas import (
        ListingsResponse,
        OpportunityBoardResponse,
        PriceStatsResponse,
        WatchlistRefreshResponse,
    )

    stats = {"query": "q", "currency": "BYN", "count": 0}
    stats.update(dict.fromkeys(("mean", "median", "q1", "q3", "min", "max"), 0.0))
    assert PriceStatsResponse(**stats).truncated is False
    assert PriceStatsResponse(**stats, truncated=True).truncated is True

    listings = {"query": "q", "currency": "BYN", "sort": "newest", "total": 0, "listings": []}
    assert ListingsResponse(**listings).truncated is False
    assert ListingsResponse(**listings, truncated=True).model_dump()["truncated"] is True

    refresh = WatchlistRefreshResponse(updated=1, missing=0, price_drops=0)
    assert refresh.skipped == 0
    assert WatchlistRefreshResponse(updated=1, missing=0, price_drops=0, skipped=2).skipped == 2

    board = OpportunityBoardResponse(currency="BYN", items=[])
    assert board.partial is False
    assert OpportunityBoardResponse(currency="BYN", items=[], partial=True).partial is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x" * 256),
        ("notes", "x" * 513),
        ("source", "x" * 33),
        ("ad_id", 0),
        ("ad_id", 2**63),
        ("price_byn", -1),
        ("price_byn", float("nan")),
        ("target_resale_byn", 0),
        ("target_resale_byn", float("inf")),
    ],
)
def test_lead_create_rejects_out_of_range_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        LeadCreate(**{**LEAD_PAYLOAD, field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("interval_min", 0),
        ("interval_min", 1441),
        ("min_discount_percent", -1),
        ("min_discount_percent", 101),
        ("min_discount_percent", float("nan")),
        ("max_price_byn", 0),
        ("max_price_byn", float("inf")),
        ("seller_type", "x" * 33),
        ("condition", "x" * 33),
        ("region_name", "x" * 65),
        ("config_keyword", "x" * 129),
    ],
)
def test_tracker_create_rejects_out_of_range_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TrackerCreate(query="iphone 15", **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "x" * 129),
        ("group_name", "x" * 129),
        ("target_discount_percent", 150),
        ("target_discount_percent", float("nan")),
        ("max_price_byn", -5),
    ],
)
def test_saved_search_create_rejects_out_of_range_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        SavedSearchCreate(query="iphone 15", **{field: value})


def test_blank_optional_filters_become_none() -> None:
    tracker = TrackerCreate(query="iphone", seller_type="  ", region_name="", config_keyword=" ")
    assert tracker.seller_type is None
    assert tracker.region_name is None
    assert tracker.config_keyword is None
    assert tracker.interval_min == 15
    assert TrackerCreate(query="iphone", condition=" used ").condition == "used"


def test_listing_price_zero_is_allowed() -> None:
    assert LeadCreate(**{**LEAD_PAYLOAD, "price_byn": 0}).price_byn == 0


def test_opportunity_signal_accepts_strict_mode() -> None:
    signal = OpportunitySignal(title="t", subtitle="s", query="q", metric="m")
    assert signal.strict_mode is False
    assert OpportunitySignal(
        title="t", subtitle="s", query="q", metric="m", strict_mode=True
    ).strict_mode


def test_response_datetimes_are_serialized_with_utc_offset() -> None:
    naive = datetime(2026, 1, 1, 10, 0)
    minsk = datetime(2026, 1, 1, 13, 0, tzinfo=timezone(timedelta(hours=3)))
    lead = LeadRead(
        id=1,
        user_id=1,
        ad_id=1,
        query="q",
        title="t",
        link="https://www.kufar.by/item/1",
        status="new",
        source="manual",
        created_at=naive,
        updated_at=minsk,
    )
    payload = json.loads(lead.model_dump_json())
    assert payload["created_at"] == "2026-01-01T10:00:00+00:00"
    assert payload["updated_at"] == "2026-01-01T10:00:00+00:00"
    assert lead.created_at.tzinfo is UTC

    rates = CurrencyRatesResponse(rates={"USD": 3.2}, source="nbrb", fetched_at=naive)
    assert json.loads(rates.model_dump_json())["fetched_at"].endswith("+00:00")


def _fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


@pytest.fixture
def client():
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = _fake_telegram_user
    with TestClient(app) as test_client:
        yield test_client


def test_api_rejects_invalid_bodies_with_422(client: TestClient) -> None:
    bad_status = client.post("/api/v1/leads", json={**LEAD_PAYLOAD, "status": "closed"})
    assert bad_status.status_code == 422

    too_long = client.post("/api/v1/trackers", json={"query": "x" * 300})
    assert too_long.status_code == 422

    nan_body = '{"query": "iphone 15", "max_price_byn": NaN}'
    nan_response = client.post(
        "/api/v1/saved-searches",
        content=nan_body,
        headers={"Content-Type": "application/json"},
    )
    assert nan_response.status_code == 422
    # The rejected NaN is echoed as a string so the error body stays valid JSON.
    assert nan_response.json()["detail"][0]["input"] == "nan"

    blank_query = client.post("/api/v1/watchlist", json={**LEAD_PAYLOAD, "query": "   "})
    assert blank_query.status_code == 422


def test_api_rejects_nul_characters_in_bodies_with_422(client: TestClient) -> None:
    tracker = client.post("/api/v1/trackers", json={"query": "iphone\u0000"})
    assert tracker.status_code == 422
    assert tracker.json()["detail"][0]["loc"] == ["body", "query"]

    created = client.post("/api/v1/leads", json=LEAD_PAYLOAD)
    assert created.status_code == 201
    notes = client.patch(f"/api/v1/leads/{created.json()['id']}", json={"notes": "a\u0000b"})
    assert notes.status_code == 422


def test_api_accepts_lead_and_watchlist_without_link(client: TestClient) -> None:
    payload = {**LEAD_PAYLOAD, "link": ""}
    assert client.post("/api/v1/leads", json=payload).status_code == 201
    assert client.post("/api/v1/watchlist", json=payload).status_code == 201


def test_api_lead_round_trip_uses_defaults_and_utc_datetimes(client: TestClient) -> None:
    created = client.post("/api/v1/leads", json=LEAD_PAYLOAD)
    assert created.status_code == 201
    lead = created.json()
    assert lead["status"] == "new"
    assert lead["source"] == "manual"
    assert lead["created_at"].endswith("+00:00")

    unchanged = client.patch(f"/api/v1/leads/{lead['id']}", json={"status": None})
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "new"

    bad = client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "closed"})
    assert bad.status_code == 422
