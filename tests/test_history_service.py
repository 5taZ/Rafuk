from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.database import get_engine, get_session_factory
from api.models import Base, QueryListingState, QuerySnapshot
from api.services.history_service import (
    detect_trend_reversal_up,
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
