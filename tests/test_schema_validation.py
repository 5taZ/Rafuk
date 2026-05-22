from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from api.schemas import (
    AdminStatusLimitUpdate,
    AdminUserStatusUpdate,
    AIAnalysisRequest,
    AIListingAssistantRequest,
    AINegotiateRequest,
    LeadCreate,
    LeadUpdate,
    TrackerCreate,
    TrackerUpdate,
    WatchlistCreate,
    WatchlistRead,
    WatchlistUpdate,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_discount_percent", -0.01),
        ("min_discount_percent", 100.01),
        ("max_price_byn", -1),
        ("max_price_byn", 10_000_000_000),
        ("seller_type", "x" * 33),
        ("condition", "x" * 33),
        ("region_name", "x" * 65),
        ("config_keyword", "x" * 129),
        ("category_id", 0),
        ("category_label", "x" * 129),
        ("alert_price_threshold", -1),
        ("alert_discount_percent", 100.01),
    ],
)
def test_tracker_create_rejects_out_of_contract_filters(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TrackerCreate(query="iphone", **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_discount_percent", -0.01),
        ("max_price_byn", -1),
        ("seller_type", "x" * 33),
        ("region_name", "x" * 65),
        ("category_id", 0),
        ("category_label", "x" * 129),
        ("alert_discount_percent", 100.01),
    ],
)
def test_tracker_update_rejects_out_of_contract_filters(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TrackerUpdate(**{field: value})


def test_tracker_update_ignores_query_field() -> None:
    assert TrackerUpdate(query="new query").model_dump(exclude_unset=True) == {}


def test_lead_and_watchlist_notes_are_bounded() -> None:
    oversized = "x" * 513
    with pytest.raises(ValidationError):
        LeadUpdate(notes=oversized)
    with pytest.raises(ValidationError):
        WatchlistCreate(
            query="iphone",
            ad_id=1,
            title="iPhone",
            link="https://example.com",
            notes=oversized,
        )
    with pytest.raises(ValidationError):
        WatchlistUpdate(notes=oversized)


# OPUS-4: ad_id and money fields must be rejected at the schema layer
# instead of bubbling up as 500/Integrity from the DB.

_VALID_LEAD_BASE = {
    "query": "iphone",
    "ad_id": 1,
    "title": "iPhone",
    "link": "https://www.kufar.by/item/1",
}
_VALID_WATCHLIST_BASE = {
    "query": "iphone",
    "ad_id": 1,
    "title": "iPhone",
    "link": "https://www.kufar.by/item/1",
}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ad_id", 0),
        ("ad_id", -1),
        ("price_byn", -1),
        ("target_resale_byn", -0.01),
        ("market_median_byn", -5),
        ("title", "x" * 256),
        ("thumbnail", "x" * 513),
    ],
)
def test_lead_create_rejects_out_of_contract_inputs(field: str, value: object) -> None:
    payload = {**_VALID_LEAD_BASE, field: value}
    with pytest.raises(ValidationError):
        LeadCreate(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("buy_price_byn", -1),
        ("sold_price_byn", -0.5),
        ("target_resale_byn", -0.01),
    ],
)
def test_lead_update_rejects_negative_money(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        LeadUpdate(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ad_id", 0),
        ("ad_id", -1),
        ("query", ""),
        ("query", "x" * 256),
        ("title", "x" * 256),
        ("link", "x" * 2049),
        ("price_byn", -1),
        ("market_median_byn", -1),
        ("thumbnail", "x" * 513),
    ],
)
def test_watchlist_create_rejects_out_of_contract_inputs(field: str, value: object) -> None:
    payload = {**_VALID_WATCHLIST_BASE, field: value}
    with pytest.raises(ValidationError):
        WatchlistCreate(**payload)


def test_ai_requests_reject_non_positive_ad_id() -> None:
    with pytest.raises(ValidationError):
        AIAnalysisRequest(ad_id=0, query="x")
    with pytest.raises(ValidationError):
        AIAnalysisRequest(ad_id=-1, query="x")
    with pytest.raises(ValidationError):
        AINegotiateRequest(
            ad_id=0,
            asking_price_byn=100,
            my_offer_byn=80,
            query="x",
        )


def test_ai_intent_goal_fields_accept_known_values() -> None:
    assert AIAnalysisRequest(ad_id=1, query="iphone", user_goal="safe_buy").user_goal == "safe_buy"
    assert AIAnalysisRequest(ad_id=1, query="macbook", user_goal="resale").user_goal == "resale"
    assert (
        AIListingAssistantRequest(title="Стул деревянный", seller_goal="sell_fast").seller_goal
        == "sell_fast"
    )
    patient = AIListingAssistantRequest(title="Стул деревянный", seller_goal="maximize_price")
    assert patient.seller_goal == "maximize_price"


def test_ai_intent_goal_fields_reject_unknown_values() -> None:
    with pytest.raises(ValidationError):
        AIAnalysisRequest(ad_id=1, query="iphone", user_goal="surprise")
    with pytest.raises(ValidationError):
        AIListingAssistantRequest(title="Стул деревянный", seller_goal="surprise")


def test_watchlist_read_price_history_uses_distinct_lists() -> None:
    payload = {
        "id": 1,
        "user_id": 1,
        "ad_id": 1,
        "query": "iphone",
        "title": "iPhone",
        "link": "https://example.com",
        "workflow_status": "default",
        "market_status": "active",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "version": 1,
    }
    first = WatchlistRead(**payload)
    second = WatchlistRead(**{**payload, "id": 2})

    assert first.price_history == []
    assert second.price_history == []
    assert first.price_history is not second.price_history


def test_admin_status_update_schemas_enforce_bounds() -> None:
    with pytest.raises(ValidationError):
        AdminStatusLimitUpdate(ai_daily_limit=-1, assistant_daily_limit=0)
    with pytest.raises(ValidationError):
        AdminStatusLimitUpdate(ai_daily_limit=0, assistant_daily_limit=10_001)
    with pytest.raises(ValidationError):
        AdminUserStatusUpdate(status_code="", note=None)
    with pytest.raises(ValidationError):
        AdminUserStatusUpdate(status_code="scout", note="x" * 513)
