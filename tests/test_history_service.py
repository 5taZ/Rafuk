from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.database import get_engine, get_session_factory
from api.models import Base, QueryListingState, QuerySnapshot
from api.services.history_service import (
    QuerySyncResult,
    as_utc,
    changes_since,
    detect_trend_reversal_up,
    price_as_of,
    sync_query_listing_states,
)


def _snap(median: float, when: datetime) -> QuerySnapshot:
    return QuerySnapshot(
        query="iphone",
        snapshot_at=when,
        total_results=10,
        analyzed_count=10,
        mean_byn=median,
        median_byn=median,
        min_byn=median * 0.5,
        max_byn=median * 2.0,
    )


def _build_series(values: list[float], *, base: datetime) -> list[QuerySnapshot]:
    """Build one snapshot per day (UTC midnight) with the given medians.

    The list is ordered oldest→newest; index `i` corresponds to day `base+i`.
    """
    return [_snap(v, base + timedelta(days=i)) for i, v in enumerate(values)]


def test_detect_trend_reversal_up_classic_v_shape() -> None:
    """Price falls 1000→700 over 5 days, then rebounds to 770 today —
    decline 30 %, rebound ~10 % → fires.
    """
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 900, 800, 750, 700, 770], base=base)
    signal = detect_trend_reversal_up(snaps)
    assert signal is not None
    assert signal.today_byn == 770.0
    assert signal.low_byn == 700.0
    assert signal.pre_high_byn == 1000.0
    assert signal.decline_pct == 30.0
    assert signal.rebound_pct == 10.0


def test_detect_trend_reversal_up_returns_none_when_still_falling() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 950, 900, 850, 800, 780], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_returns_none_for_steady_rise() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([800, 850, 900, 950, 1000, 1050], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_requires_min_days() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 800, 850], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_skips_tiny_rebound_below_threshold() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 900, 800, 750, 700, 710], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_skips_tiny_decline_below_threshold() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 995, 990, 988, 985, 1015], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_collapses_intraday_snapshots() -> None:
    """6 days of data, but day 0 has 3 hourly snapshots — function should
    collapse them via median per day.
    """
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps: list[QuerySnapshot] = []
    snaps.append(_snap(995, base))
    snaps.append(_snap(1000, base + timedelta(hours=6)))
    snaps.append(_snap(1005, base + timedelta(hours=12)))
    snaps += _build_series([900, 800, 750, 700, 770], base=base + timedelta(days=1))
    signal = detect_trend_reversal_up(snaps)
    assert signal is not None
    assert signal.pre_high_byn == 1000.0  # median of 995/1000/1005


def test_detect_trend_reversal_up_ignores_zero_and_negative_medians() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 0, 800, 750, 700, 770], base=base)
    # The zero-day bucket is dropped → 5 valid days remain → still fires
    signal = detect_trend_reversal_up(snaps)
    assert signal is not None


def test_detect_trend_reversal_up_low_must_be_recent() -> None:
    """If the recent low is older than 3 buckets back, that's not a
    reversal anymore — it's an established new uptrend.
    """
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 700, 750, 800, 850, 900], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_returns_none_for_empty() -> None:
    assert detect_trend_reversal_up([]) is None


# E-FIND-05: price_drop threshold is now max(0.50 BYN, last_price * 0.5%).
# Cheap items keep the 0.50 BYN floor; expensive items need a real %
# move so we don't fire price_drop events on rounding noise.


def _existing_state(query: str, ad_id: int, last_price_byn: float) -> QueryListingState:
    now = datetime.now(UTC)
    return QueryListingState(
        query=query,
        ad_id=ad_id,
        title="Item",
        link=f"https://www.kufar.by/item/{ad_id}",
        last_price_byn=last_price_byn,
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )


@pytest.mark.asyncio
async def test_sync_query_listing_states_cheap_item_keeps_absolute_floor() -> None:
    """A 50 BYN listing dropping by 0.50 BYN still fires (relative
    floor 0.005*50 = 0.25 < 0.50, so the absolute 0.50 BYN floor
    wins)."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with sf() as session:
            session.add(_existing_state("cheap", ad_id=1, last_price_byn=50.0))
            await session.commit()

        async with sf() as session:
            result = await sync_query_listing_states(
                session,
                query="cheap",
                # 49.50 BYN = 4950 kopecks (price_byn from Kufar is in kopecks).
                ads=[{"ad_id": 1, "price_byn": 4950, "subject": "Item",
                      "ad_link": "https://www.kufar.by/item/1"}],
                observed_at=datetime.now(UTC),
                total_results=1,
            )
            assert len(result.price_drops) == 1
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_query_listing_states_expensive_item_ignores_subwhisper_drop() -> None:
    """A 5000 BYN listing dropping by 1 BYN must NOT fire — relative
    floor 0.005*5000 = 25 BYN, well above the 1 BYN move."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with sf() as session:
            session.add(_existing_state("expensive", ad_id=2, last_price_byn=5000.0))
            await session.commit()

        async with sf() as session:
            result = await sync_query_listing_states(
                session,
                query="expensive",
                ads=[{"ad_id": 2, "price_byn": 499_900, "subject": "Item",
                      "ad_link": "https://www.kufar.by/item/2"}],
                observed_at=datetime.now(UTC),
                total_results=1,
            )
            assert result.price_drops == []
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_query_listing_states_expensive_item_real_drop_fires() -> None:
    """5000 → 4960 BYN is 40 BYN, above the 25 BYN relative floor."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with sf() as session:
            session.add(_existing_state("real_drop", ad_id=3, last_price_byn=5000.0))
            await session.commit()

        async with sf() as session:
            result = await sync_query_listing_states(
                session,
                query="real_drop",
                ads=[{"ad_id": 3, "price_byn": 496_000, "subject": "Item",
                      "ad_link": "https://www.kufar.by/item/3"}],
                observed_at=datetime.now(UTC),
                total_results=1,
            )
            assert len(result.price_drops) == 1
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# RV-SCH-01: listing state is shared by every tracker on a search key, so
# "new" and "cheaper" are derived per tracker from its own last check.


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _listing(ad_id: int, *, first_seen: datetime, price: float, history=None) -> QueryListingState:
    return QueryListingState(
        query="per-tracker",
        ad_id=ad_id,
        title=f"Item {ad_id}",
        link=f"https://www.kufar.by/item/{ad_id}",
        last_price_byn=price,
        price_type="fixed",
        active=True,
        first_seen_at=first_seen,
        last_seen_at=datetime.now(UTC),
        price_history=history,
    )


def test_changes_since_new_listings_are_relative_to_the_reader() -> None:
    now = datetime.now(UTC)
    old = _listing(1, first_seen=now - timedelta(hours=2), price=100)
    fresh = _listing(2, first_seen=now - timedelta(minutes=10), price=100)
    page = QuerySyncResult(stats_count=2, total_results=2, listings=[old, fresh])

    assert [s.ad_id for s in changes_since(page, now - timedelta(hours=1)).new_listings] == [2]
    assert [s.ad_id for s in changes_since(page, now - timedelta(hours=3)).new_listings] == [1, 2]
    assert changes_since(page, now).new_listings == []


def test_changes_since_price_drop_compares_with_price_at_reader_cursor() -> None:
    now = datetime.now(UTC)
    first_seen, dropped = now - timedelta(hours=3), now - timedelta(hours=1)
    state = _listing(
        1, first_seen=first_seen, price=1000,
        history=[[_epoch(first_seen), 1100.0], [_epoch(dropped), 1000.0]],
    )
    page = QuerySyncResult(stats_count=1, total_results=1, listings=[state])

    # Looked before the drop → it is news; looked after it → it is not.
    assert changes_since(page, now - timedelta(hours=2)).price_drops == [(state, Decimal("100"))]
    assert changes_since(page, now - timedelta(minutes=30)).price_drops == []


def test_changes_since_judges_the_net_change_since_the_reader_looked() -> None:
    """1000 → 1200 → 1100: a reader that saw 1000 sees a rise, not a
    drop (a single "previous price" would have called it -100); a reader
    that saw 1200 sees the drop."""
    now = datetime.now(UTC)
    t0, t1, t2 = now - timedelta(hours=3), now - timedelta(hours=2), now - timedelta(hours=1)
    state = _listing(
        1, first_seen=t0, price=1100,
        history=[[_epoch(t0), 1000.0], [_epoch(t1), 1200.0], [_epoch(t2), 1100.0]],
    )
    page = QuerySyncResult(stats_count=1, total_results=1, listings=[state])

    assert changes_since(page, t0 + timedelta(minutes=5)).price_drops == []
    assert changes_since(page, t1 + timedelta(minutes=5)).price_drops == [(state, Decimal("100"))]


def test_price_as_of_without_history_is_the_current_price() -> None:
    state = _listing(1, first_seen=datetime.now(UTC) - timedelta(days=1), price=500)
    assert price_as_of(state, datetime.now(UTC) - timedelta(hours=5)) == Decimal("500")


@pytest.mark.asyncio
async def test_sync_records_price_changes_for_later_readers() -> None:
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        first_seen = datetime.now(UTC) - timedelta(hours=3)
        async with sf() as session:
            session.add(_listing(7, first_seen=first_seen, price=1100))
            await session.commit()

        def _page(kopecks):
            return [{"ad_id": 7, "price_byn": kopecks, "subject": "Item 7",
                     "ad_link": "https://www.kufar.by/item/7"}]

        t1 = datetime.now(UTC) - timedelta(minutes=30)
        async with sf() as session:
            result = await sync_query_listing_states(
                session, query="per-tracker", ads=_page(100_000), observed_at=t1,
                total_results=1,
            )
            # The key-level diff still reports the drop to its own caller.
            assert len(result.price_drops) == 1
            assert [s.ad_id for s in result.listings] == [7]
            await session.commit()

        async with sf() as session:
            # Same price again: nothing to record.
            await sync_query_listing_states(
                session, query="per-tracker", ads=_page(100_000),
                observed_at=datetime.now(UTC), total_results=1,
            )
            await session.commit()

        async with sf() as session:
            state = (await session.execute(select(QueryListingState))).scalar_one()
            assert state.price_history == [[_epoch(first_seen), 1100.0], [_epoch(t1), 1000.0]]
            # A tracker that last looked before t1 still sees the drop.
            changes = changes_since(
                QuerySyncResult(stats_count=1, total_results=1, listings=[state]),
                first_seen + timedelta(minutes=1),
            )
            assert [(s.ad_id, d) for s, d in changes.price_drops] == [(7, Decimal("100"))]
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_price_history_is_bounded() -> None:
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        start = datetime.now(UTC) - timedelta(days=1)
        async with sf() as session:
            session.add(_listing(8, first_seen=start, price=2000))
            await session.commit()
        for step in range(1, 13):
            async with sf() as session:
                await sync_query_listing_states(
                    session, query="per-tracker",
                    ads=[{"ad_id": 8, "price_byn": 200_000 - step * 1_000, "subject": "Item 8",
                          "ad_link": "https://www.kufar.by/item/8"}],
                    observed_at=start + timedelta(hours=step), total_results=1,
                )
                await session.commit()
        async with sf() as session:
            state = (await session.execute(select(QueryListingState))).scalar_one()
        assert len(state.price_history) == 8
        assert state.price_history[-1] == [_epoch(start + timedelta(hours=12)), 1880.0]
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_refreshes_listing_outside_the_load_window(monkeypatch) -> None:
    """RV-SCH-01: a known listing older than ``load_listing_states``' window
    used to come back from the insert-race path as "new" with its stale
    price; it is now refreshed like any known listing."""
    import api.services.history_service as history_service

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        first_seen = datetime.now(UTC) - timedelta(days=20)
        async with sf() as session:
            session.add(_listing(9, first_seen=first_seen, price=1100))
            await session.commit()

        async def _window_without_it(session, *, query):
            return {}

        monkeypatch.setattr(history_service, "load_listing_states", _window_without_it)
        observed = datetime.now(UTC)
        async with sf() as session:
            result = await sync_query_listing_states(
                session, query="per-tracker",
                ads=[{"ad_id": 9, "price_byn": 100_000, "subject": "Item 9",
                      "ad_link": "https://www.kufar.by/item/9"}],
                observed_at=observed, total_results=1,
            )
            assert result.new_listings == []
            assert [s.ad_id for s in result.listings] == [9]
            await session.commit()

        async with sf() as session:
            state = (await session.execute(select(QueryListingState))).scalar_one()
        assert float(state.last_price_byn) == 1000.0
        assert as_utc(state.last_seen_at) == observed
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
