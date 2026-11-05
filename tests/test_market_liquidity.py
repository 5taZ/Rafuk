from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from api.database import get_engine, get_session_factory
from api.models import MarketLiquidityObservation
from api.services.history_service import as_utc
from api.services.market_liquidity import load_liquidity_insight, observe_market_listings
from api.services.watchlist_market import liquidity_segment
from scheduler.market_liquidity import check_due_observations

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def _phone(ad_id: int, **overrides: object) -> dict:
    return {
        "ad_id": ad_id,
        "subject": "iPhone 13 128 GB",
        "category": "17010",
        "price_byn": 60_000,
        "list_time": NOW.isoformat(),
        "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
        **overrides,
    }


def test_phone_segment_matches_title_variations_but_separates_configs() -> None:
    target = liquidity_segment(_phone(1))
    assert target is not None
    assert target == liquidity_segment(_phone(
        2, subject="Смартфон Apple iPhone 13 на 128 ГБ",
        ad_parameters=[{"p": "condition", "vl": "Б/у"}, {"p": "ram", "v": "6"}],
    ))
    for variant in [
        _phone(3, subject="iPhone 13 Pro 128 GB"),
        _phone(4, subject="iPhone 13 256 GB"),
        _phone(5, ad_parameters=[{"p": "condition", "vl": "Новый"}]),
        _phone(6, company_ad=True),
        _phone(7, category="12345"),
    ]:
        assert liquidity_segment(variant) != target
    assert liquidity_segment(_phone(8, subject="iPhone 13 128 GB обмен")) is None


async def test_public_checks_follow_known_ids_and_confirm_missing_twice() -> None:
    factory = get_session_factory(get_engine())
    async with factory() as session:
        await observe_market_listings(session, [
            *[_phone(i) for i in range(1, 6)],
            _phone(6, list_time=(NOW - timedelta(days=2)).isoformat()),
            _phone(7, list_time="invalid"),
        ], now=NOW)
        tomorrow = NOW + timedelta(days=1)
        await observe_market_listings(session, [
            _phone(1, price_byn=10_000, list_time=tomorrow.isoformat()),
        ], now=tomorrow)
        await session.commit()

    calls = []
    status = {1: 200, 2: 404, 3: 404, 4: 302, 5: 429}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert request.url.host == "www.kufar.by"
        ad_id = int(request.url.path.rsplit("/", 1)[1])
        calls.append(ad_id)
        return httpx.Response(status[ad_id])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await check_due_observations(factory, client=client, now=tomorrow, request_delay=0)
        assert not calls
        due = NOW + timedelta(days=7)
        await check_due_observations(factory, client=client, now=due, request_delay=0)
        assert calls == [1, 2, 3, 4, 5]
        async with factory() as session:
            assert (await session.get(MarketLiquidityObservation, 2)).available is None
        status[3] = 200
        await check_due_observations(
            factory, client=client, now=due + timedelta(hours=1), request_delay=0,
        )
        assert calls[5:] == [2, 3, 4, 5]

    async with factory() as session:
        rows = {
            row.ad_id: row for row in await session.scalars(select(MarketLiquidityObservation))
        }
    assert set(rows) == {1, 2, 3, 4, 5}
    assert as_utc(rows[1].first_seen_at) == NOW
    assert rows[1].price_byn == 600
    assert rows[1].available is True
    assert rows[2].available is False
    assert rows[3].available is True
    assert rows[4].available is None
    assert rows[5].available is None


async def test_liquidity_counts_mature_peers_in_price_band_and_requires_coverage() -> None:
    factory = get_session_factory(get_engine())
    target = _phone(1)
    segment = liquidity_segment(target)
    async with factory() as session:
        await observe_market_listings(session, [
            *[_phone(i) for i in range(1, 17)],
            _phone(17, price_byn=90_000),
            _phone(18, subject="iPhone 13 256 GB"),
        ], now=NOW)
        await observe_market_listings(session, [
            _phone(19, list_time=(NOW + timedelta(days=2)).isoformat()),
        ], now=NOW + timedelta(days=2))
        await session.execute(
            update(MarketLiquidityObservation)
            .values(available=MarketLiquidityObservation.ad_id > 11),
        )
        await session.commit()

    async with factory() as session:
        insight = await load_liquidity_insight(
            session, target, segment=segment, now=NOW + timedelta(days=8),
        )
        assert (insight.score, insight.label) == (66.7, "Высокая")
        assert (insight.unavailable_count, insight.sample_size) == (10, 15)

        await session.execute(update(MarketLiquidityObservation).values(available=True))
        insight = await load_liquidity_insight(
            session, target, segment=segment, now=NOW + timedelta(days=8),
        )
        assert (insight.score, insight.label) == (0, "Низкая")

        await observe_market_listings(session, [_phone(i) for i in range(20, 30)], now=NOW)
        insight = await load_liquidity_insight(
            session, target, segment=segment, now=NOW + timedelta(days=8),
        )
        assert insight.score is None
        assert insight.sample_size == 15
