"""Alerts quote the Mini App card's market figure, not the page median."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from api.models import QueryListingState, TrackerEvent
from api.schemas import WatchlistComparison
from scheduler import collector
from scheduler.messages import _build_new_listing_message, _build_threshold_message


def _comparison(delta: float | None, count: int = 68, median: float = 2200.0):
    return WatchlistComparison(
        ad_found=True, current_price_byn=2000.0, comparable_count=count,
        median_byn=median, delta_percent=delta,
    )


class _Market:
    def __init__(self, found):
        self.found = found
        self.calls = []

    async def get(self, ad_ids):
        self.calls.append(list(ad_ids))
        return {ad_id: self.found.get(ad_id) for ad_id in ad_ids}


def _discount_event(ad_id: int, discount: float) -> TrackerEvent:
    return TrackerEvent(
        tracker_id=1, user_id=1, ad_id=ad_id, query="ps5", strict_mode=False,
        event_type="discount_alert", title="PS 5 Slim", link="", price_byn=2000.0,
        price_type="fixed", delta_byn=discount,
        parameters={"discount_percent": discount, "threshold_percent": 10.0, "median_byn": 2250.0},
    )


async def test_discount_alert_takes_card_figure_and_drops_below_threshold():
    tracker = SimpleNamespace(alert_discount_percent=10)
    price_event = TrackerEvent(
        tracker_id=1, user_id=1, ad_id=3, query="ps5", strict_mode=False,
        event_type="price_threshold_alert", title="x", link="", price_byn=1.0,
    )
    events = [_discount_event(1, 11.11), _discount_event(2, 11.11), price_event]
    market = _Market({1: _comparison(-9.0), 2: _comparison(-14.6)})

    kept = await collector._apply_market_discounts(events, tracker, market)

    assert [event.ad_id for event in kept] == [2, 3]
    assert market.calls == [[1, 2]]
    assert kept[0].delta_byn == 14.6
    assert kept[0].parameters["comparable_count"] == 68
    assert kept[0].parameters["median_byn"] == 2200.0
    assert _build_threshold_message(kept).splitlines()[-2] == "−15% к медиане · 68 аналогов"


async def test_discount_alert_without_comparison_keeps_event_but_no_figure():
    tracker = SimpleNamespace(alert_discount_percent=10)
    events = [_discount_event(1, 11.11)]

    kept = await collector._apply_market_discounts(events, tracker, _Market({}))

    assert kept == events
    assert _build_threshold_message(kept) == "Скидка от медианы\nPS 5 Slim\n2 000 BYN"


def test_new_listing_job_quotes_card_comparison():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=7, query="ps5", title="PS 5 Slim", last_price_byn=2000.0, link="https://k/7",
        active=True, first_seen_at=now, last_seen_at=now,
    )
    sync = SimpleNamespace(new_listings=[state], price_drops=[])
    created = [SimpleNamespace(ad_id=7, event_type="new_listing")]
    tracker = SimpleNamespace(user=SimpleNamespace(telegram_user_id=5), user_id=1)

    [job] = collector._listing_notification_jobs(
        tracker, sync, created, {}, comparisons={7: _comparison(-9.0)},
    )
    assert job.message.endswith("\n−9% к медиане · 68 аналогов")

    [plain] = collector._listing_notification_jobs(tracker, sync, created, {}, comparisons={})
    assert "медиан" not in plain.message


def test_card_rounding_matches_math_round():
    state = SimpleNamespace(title="x", last_price_byn=10.0, price_type=None)
    assert _build_new_listing_message(state, discount_pct=12.5).endswith("−12% к медиане")
    assert _build_new_listing_message(state, discount_pct=12.51).endswith("−13% к медиане")
    assert "медиан" not in _build_new_listing_message(state, discount_pct=0.4)


async def test_market_discounts_memoizes_and_survives_failures(monkeypatch):
    calls = []

    async def fake_load(targets, **kwargs):
        calls.append([ad["ad_id"] for ad in targets])
        if len(calls) == 1:
            return {1: _comparison(-5.0)}
        raise RuntimeError("kufar down")

    monkeypatch.setattr(collector, "load_page_comparisons", fake_load)
    monkeypatch.setattr(collector, "_get_kufar_client", lambda settings: object())
    monkeypatch.setattr(collector, "_get_market_cache", lambda settings: object())
    ads = {1: {"ad_id": 1}, 2: {"ad_id": 2}, 3: {"ad_id": 3}}
    market = collector._MarketDiscounts("ps5", ads, settings=object())

    first = await market.get([1, 2])
    second = await market.get([1, 2, 3, 99])

    assert calls == [[1, 2], [3]]
    assert first[1].delta_percent == -5.0 and first[2] is None
    assert second[3] is None and second[99] is None
