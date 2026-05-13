from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from api.schemas import (
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
