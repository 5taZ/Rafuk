from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from api.database import get_engine, get_session_factory
from api.models import QueryListingState, QuerySnapshot
from api.services.history_service import (
    is_significant_price_drop,
    snapshot_bucket,
    sync_query_listing_states,
    upsert_query_snapshot,
)

BUCKET = snapshot_bucket(datetime(2026, 10, 11, 10, 25, tzinfo=UTC))


def make_ad(ad_id: int, price_byn: float | None, subject: str = "iPhone 15") -> dict[str, object]:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "price_byn": int(price_byn * 100) if price_byn is not None else 0,
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "list_time": "2026-10-11T10:00:00",
    }


def ads_with_prices(*prices: float) -> list[dict[str, object]]:
    return [make_ad(index + 1, price) for index, price in enumerate(prices)]


async def load_snapshots(session_factory) -> list[QuerySnapshot]:
    async with session_factory() as session:
        return list((await session.execute(select(QuerySnapshot))).scalars())


async def test_upsert_query_snapshot_inserts_and_skips_empty_stats() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)

    async with session_factory() as session:
        written = await upsert_query_snapshot(
            session,
            query="broad::x",
            ads=[make_ad(1, None)],
            total_results=1,
            bucket_at=BUCKET,
        )
        await session.commit()
    assert written is False
    assert await load_snapshots(session_factory) == []

    async with session_factory() as session:
        written = await upsert_query_snapshot(
            session,
            query="broad::x",
            ads=ads_with_prices(100, 200, 300),
            total_results=40,
            bucket_at=BUCKET,
        )
        await session.commit()
    assert written is True
    snapshots = await load_snapshots(session_factory)
    assert len(snapshots) == 1
    assert snapshots[0].analyzed_count == 3
    assert snapshots[0].median_byn == 200
    assert snapshots[0].total_results == 40

    # An empty sample never overwrites an existing snapshot with zeros.
    async with session_factory() as session:
        written = await upsert_query_snapshot(
            session,
            query="broad::x",
            ads=[],
            total_results=0,
            bucket_at=BUCKET,
        )
        await session.commit()
    assert written is False
    assert (await load_snapshots(session_factory))[0].median_byn == 200
    await engine.dispose()


async def test_upsert_query_snapshot_keeps_the_larger_sample() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)

    async with session_factory() as session:
        await upsert_query_snapshot(
            session,
            query="broad::x",
            ads=ads_with_prices(100, 200, 300, 400),
            total_results=4,
            bucket_at=BUCKET,
        )
        await session.commit()

    async with session_factory() as session:
        written = await upsert_query_snapshot(
            session,
            query="broad::x",
            ads=ads_with_prices(900, 950),
            total_results=2,
            bucket_at=BUCKET,
        )
        await session.commit()
    assert written is False
    snapshot = (await load_snapshots(session_factory))[0]
    assert snapshot.analyzed_count == 4
    assert snapshot.median_byn == 250

    async with session_factory() as session:
        written = await upsert_query_snapshot(
            session,
            query="broad::x",
            ads=ads_with_prices(10, 20, 30, 40, 50),
            total_results=5,
            bucket_at=BUCKET,
        )
        await session.commit()
    assert written is True
    snapshots = await load_snapshots(session_factory)
    assert len(snapshots) == 1
    assert snapshots[0].analyzed_count == 5
    assert snapshots[0].median_byn == 30

    # Another bucket is a separate row.
    async with session_factory() as session:
        await upsert_query_snapshot(
            session,
            query="broad::x",
            ads=ads_with_prices(10),
            total_results=1,
            bucket_at=BUCKET + timedelta(hours=1),
        )
        await session.commit()
    assert len(await load_snapshots(session_factory)) == 2
    await engine.dispose()


async def test_upsert_query_snapshot_only_if_missing_never_overwrites() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)

    async with session_factory() as session:
        assert await upsert_query_snapshot(
            session,
            query="strict::x",
            ads=ads_with_prices(100),
            total_results=1,
            bucket_at=BUCKET,
            only_if_missing=True,
        )
        await session.commit()

    async with session_factory() as session:
        written = await upsert_query_snapshot(
            session,
            query="strict::x",
            ads=ads_with_prices(500, 600, 700),
            total_results=3,
            bucket_at=BUCKET,
            only_if_missing=True,
        )
        await session.commit()
    assert written is False
    snapshot = (await load_snapshots(session_factory))[0]
    assert snapshot.analyzed_count == 1
    assert snapshot.median_byn == 100
    await engine.dispose()


async def test_upsert_query_snapshot_twice_in_one_transaction_does_not_conflict() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)

    async with session_factory() as session:
        for _ in range(2):
            await upsert_query_snapshot(
                session,
                query="broad::x",
                ads=ads_with_prices(100, 200),
                total_results=2,
                bucket_at=BUCKET,
            )
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(QuerySnapshot))
    assert count == 1
    await engine.dispose()


def test_is_significant_price_drop_threshold() -> None:
    assert is_significant_price_drop(1000, 950)
    assert is_significant_price_drop(1000, 990)  # exactly 1%
    assert not is_significant_price_drop(1000, 997)  # 0.3% exchange-rate noise
    assert not is_significant_price_drop(50, 49.5)  # 1% but below 1 BYN
    assert not is_significant_price_drop(1000, 1000)
    assert not is_significant_price_drop(1000, 1100)
    assert is_significant_price_drop(1000, 997, min_drop_byn=0, min_drop_percent=0)


async def test_sync_query_listing_states_new_listings_and_price_drops() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)
    first_seen = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)

    async with session_factory() as session:
        result = await sync_query_listing_states(
            session,
            query="broad::iphone 15",
            ads=ads_with_prices(1000, 2000, 3000),
            observed_at=first_seen,
            total_results=3,
        )
        await session.commit()
    assert sorted(state.ad_id for state in result.new_listings) == [1, 2, 3]
    assert result.price_drops == []

    # Ad 2 drops out of the window, ad 1 gets cheaper by 0.3%, ad 3 by 10%.
    async with session_factory() as session:
        result = await sync_query_listing_states(
            session,
            query="broad::iphone 15",
            ads=[make_ad(1, 997), make_ad(3, 2700), make_ad(4, 500)],
            observed_at=first_seen + timedelta(minutes=30),
            total_results=3,
        )
        await session.commit()
    assert [state.ad_id for state in result.new_listings] == [4]
    assert [(state.ad_id, round(delta)) for state, delta in result.price_drops] == [(3, 300)]

    async with session_factory() as session:
        states = {
            row.ad_id: row
            for row in (await session.execute(select(QueryListingState))).scalars()
        }
    assert states[2].active is False
    assert states[1].last_price_byn == 997

    # Ad 2 comes back to the window: it is not a new listing.
    async with session_factory() as session:
        result = await sync_query_listing_states(
            session,
            query="broad::iphone 15",
            ads=[make_ad(2, 2000), make_ad(4, 500)],
            observed_at=first_seen + timedelta(hours=1),
            total_results=2,
        )
        await session.commit()
    assert result.new_listings == []
    assert result.price_drops == []

    async with session_factory() as session:
        state = await session.scalar(
            select(QueryListingState).where(QueryListingState.ad_id == 2)
        )
    assert state is not None
    assert state.active is True
    await engine.dispose()


async def test_sync_query_listing_states_truncates_long_values() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)
    ad = make_ad(7, 100, subject="x" * 400)
    ad["list_time"] = "t" * 100

    async with session_factory() as session:
        result = await sync_query_listing_states(
            session,
            query="broad::x",
            ads=[ad, dict(ad)],
            observed_at=datetime.now(UTC),
            total_results=1,
        )
        await session.commit()
    assert len(result.new_listings) == 1
    assert len(result.new_listings[0].title) == 255
    assert len(result.new_listings[0].list_time or "") == 64
    await engine.dispose()
