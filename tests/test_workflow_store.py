from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from api.database import get_engine, get_session_factory
from api.models import Base, LeadItem, LeadItemPriceSnapshot
from api.services.workflow_store import (
    load_last_snapshot_prices,
    record_price_snapshot,
)

sys.path.insert(0, str(Path(__file__).parent))
from conftest import make_user


@pytest.mark.asyncio
async def test_load_last_snapshot_prices_returns_latest_per_item() -> None:
    """Bulk-loader must return the *latest* snapshot price per
    lead_item_id in a single query, regardless of insertion order.
    Used by watchlist refresh to skip a per-row SELECT inside
    record_price_snapshot.
    """
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = make_user(telegram_user_id=42, first_name="Snap")
        session.add(user)
        await session.flush()
        item_a = LeadItem(
            user_id=user.id, ad_id=1, query="iphone", title="A",
            link="https://x", status="watching",
        )
        item_b = LeadItem(
            user_id=user.id, ad_id=2, query="iphone", title="B",
            link="https://x", status="watching",
        )
        item_no_snaps = LeadItem(
            user_id=user.id, ad_id=3, query="iphone", title="C",
            link="https://x", status="watching",
        )
        session.add_all([item_a, item_b, item_no_snaps])
        await session.flush()

        now = datetime.now(UTC)
        # Two snapshots for item_a — the second one must win even
        # though the first one was inserted first.
        session.add_all([
            LeadItemPriceSnapshot(
                lead_item_id=item_a.id, price_byn=1000.0,
                snapped_at=now - timedelta(days=2),
            ),
            LeadItemPriceSnapshot(
                lead_item_id=item_a.id, price_byn=900.0,
                snapped_at=now - timedelta(days=1),
            ),
            LeadItemPriceSnapshot(
                lead_item_id=item_b.id, price_byn=2500.0, snapped_at=now,
            ),
        ])
        await session.commit()

        result = await load_last_snapshot_prices(
            session, [item_a.id, item_b.id, item_no_snaps.id]
        )

    assert result[item_a.id] == 900.0  # latest, not 1000
    assert result[item_b.id] == 2500.0
    # Item without snapshots is simply absent — caller treats as "no
    # prior price" and always records on first refresh.
    assert item_no_snaps.id not in result

    await engine.dispose()


@pytest.mark.asyncio
async def test_record_price_snapshot_skips_db_round_trip_when_hint_provided() -> None:
    """When the caller passes ``last_known_price``, the helper must
    NOT issue a SELECT — that's the whole point of the bulk pre-fetch
    in watchlist refresh.
    """
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = make_user(telegram_user_id=43, first_name="Skip")
        session.add(user)
        await session.flush()
        item = LeadItem(
            user_id=user.id, ad_id=10, query="iphone", title="X",
            link="https://x", status="watching",
        )
        session.add(item)
        await session.flush()

        # Identical price within epsilon → no snapshot recorded.
        snap = await record_price_snapshot(
            session, lead_item=item, price_byn=1000.0, last_known_price=1000.2,
        )
        assert snap is None

        # Price moved → snapshot recorded.
        snap = await record_price_snapshot(
            session, lead_item=item, price_byn=950.0, last_known_price=1000.0,
        )
        assert snap is not None
        assert snap.price_byn == 950.0

        # Sentinel < 0 means "no prior snapshot" → always record.
        snap = await record_price_snapshot(
            session, lead_item=item, price_byn=950.0, last_known_price=-1.0,
        )
        assert snap is not None
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_load_last_snapshot_prices_empty_input_returns_empty_dict() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        result = await load_last_snapshot_prices(session, [])
    assert result == {}
    await engine.dispose()
