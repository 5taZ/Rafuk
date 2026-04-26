from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData
from api.models import Base, DealExpense, LeadItem, User


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=987654, first_name="Analytics", raw={})


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_leads(session_factory) -> int:
    """Seed a fixed lead pipeline that exercises every analytics path.

    Returns the user_id so tests can grab it if needed.
    """
    async with session_factory() as session:
        user = User(telegram_user_id=987654, first_name="Analytics")
        session.add(user)
        await session.flush()

        now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
        # Two sold deals — one profitable, one not.
        sold_profit = LeadItem(
            user_id=user.id,
            ad_id=1,
            query="iphone 13",
            title="iPhone 13",
            link="https://www.kufar.by/item/1",
            price_byn=1000,
            buy_price_byn=Decimal("1000"),
            sold_price_byn=Decimal("1300"),
            status="sold",
            sold_at=now - timedelta(days=2),
            created_at=now - timedelta(days=10),
        )
        sold_loss = LeadItem(
            user_id=user.id,
            ad_id=2,
            query="iphone 13",
            title="iPhone 13",
            link="https://www.kufar.by/item/2",
            price_byn=1500,
            buy_price_byn=Decimal("1500"),
            sold_price_byn=Decimal("1450"),
            status="closed",
            sold_at=now - timedelta(days=20),
            created_at=now - timedelta(days=25),
        )
        # One skipped lead — counts in pursued, not in won.
        skipped = LeadItem(
            user_id=user.id,
            ad_id=3,
            query="ps5",
            title="PS5",
            link="https://www.kufar.by/item/3",
            price_byn=1200,
            status="skipped",
            created_at=now - timedelta(days=15),
        )
        # One active deal — counts in pursued and active, not in won.
        active = LeadItem(
            user_id=user.id,
            ad_id=4,
            query="ps5",
            title="PS5",
            link="https://www.kufar.by/item/4",
            price_byn=1100,
            status="in_progress",
            created_at=now - timedelta(days=3),
        )
        # One watchlist item — does NOT count as pursued.
        watching = LeadItem(
            user_id=user.id,
            ad_id=5,
            query="macbook",
            title="MacBook Air",
            link="https://www.kufar.by/item/5",
            price_byn=2500,
            status="watching",
            created_at=now - timedelta(days=1),
        )
        session.add_all([sold_profit, sold_loss, skipped, active, watching])
        await session.flush()
        # Add 50 BYN of expenses to the profitable sold deal — analytics
        # should subtract these from profit.
        session.add(
            DealExpense(
                lead_id=sold_profit.id,
                user_id=user.id,
                expense_type="delivery",
                amount_byn=Decimal("50"),
            )
        )
        await session.commit()
        return user.id


def _bootstrap_app():
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    return app


def test_lead_analytics_returns_full_dashboard_payload() -> None:
    app = _bootstrap_app()
    with TestClient(app) as client:
        asyncio.run(_create_tables(app.state.engine))
        asyncio.run(_seed_leads(app.state.session_factory))
        response = client.get("/api/v1/analytics/leads?days=90")

    assert response.status_code == 200
    payload = response.json()
    assert payload["period_days"] == 90
    assert payload["total_leads"] == 5
    # 4 pursued (sold_profit, sold_loss, skipped, active) — watching excluded
    assert payload["pursued_leads"] == 4
    assert payload["sold_leads"] == 2
    assert payload["skipped_leads"] == 1
    assert payload["active_leads"] == 1
    # win rate = 2 sold / 4 pursued = 50 %
    assert payload["win_rate_percent"] == 50.0
    # Revenue = 1300 + 1450; cost = (1000 + 50 expense) + 1500 = 2550
    assert payload["total_revenue_byn"] == 2750.0
    assert payload["total_cost_byn"] == 2550.0
    assert payload["total_profit_byn"] == 200.0
    assert payload["total_expenses_byn"] == 50.0
    # Profit deal: ROI = (1300 - 1050) / 1050 ≈ 23.8 %
    # Loss deal: ROI = (1450 - 1500) / 1500 ≈ -3.3 %
    # Average ≈ 10.25 %
    assert 9.0 < payload["average_roi_percent"] < 12.0
    # Profit-deal sold 8 days after creation, loss-deal 5 days
    assert payload["average_days_to_close"] > 0
    assert payload["median_days_to_close"] > 0


def test_lead_analytics_funnel_includes_every_status_with_correct_counts() -> None:
    app = _bootstrap_app()
    with TestClient(app) as client:
        asyncio.run(_create_tables(app.state.engine))
        asyncio.run(_seed_leads(app.state.session_factory))
        response = client.get("/api/v1/analytics/leads")

    payload = response.json()
    funnel = {stage["status"]: stage for stage in payload["funnel"]}
    assert funnel["watching"]["count"] == 1
    assert funnel["in_progress"]["count"] == 1
    assert funnel["sold"]["count"] == 1
    assert funnel["closed"]["count"] == 1
    assert funnel["skipped"]["count"] == 1
    # Stages with no leads still appear (so the chart layout is stable).
    assert funnel["new"]["count"] == 0
    assert funnel["bought"]["count"] == 0
    assert funnel["researching"]["count"] == 0


def test_lead_analytics_monthly_aggregates_revenue_per_bucket() -> None:
    app = _bootstrap_app()
    with TestClient(app) as client:
        asyncio.run(_create_tables(app.state.engine))
        asyncio.run(_seed_leads(app.state.session_factory))
        response = client.get("/api/v1/analytics/leads")

    monthly = response.json()["monthly"]
    # Both sold deals fall in the same calendar month (April 2026).
    assert len(monthly) == 1
    bucket = monthly[0]
    assert bucket["sold_count"] == 2
    assert bucket["revenue_byn"] == 2750.0
    assert bucket["profit_byn"] == 200.0


def test_lead_analytics_period_filter_excludes_old_sales_from_roi() -> None:
    """Tightening the window past the loss-deal's sold_at should leave
    ROI / win-rate dominated by the recent profitable sale only."""
    app = _bootstrap_app()
    with TestClient(app) as client:
        asyncio.run(_create_tables(app.state.engine))
        asyncio.run(_seed_leads(app.state.session_factory))
        # 7-day window — only sold_profit (created 10d ago) falls inside
        # ... actually 10 days > 7, so even the profit sale would be
        # excluded. Use 14 days so the profit sale is in (10d) and the
        # loss sale is out (25d).
        response = client.get("/api/v1/analytics/leads?days=14")

    payload = response.json()
    assert payload["sold_leads"] == 1
    assert payload["total_revenue_byn"] == 1300.0
    # cost = 1000 + 50 expense; profit = 1300 - 1050 = 250
    assert payload["total_profit_byn"] == 250.0


def test_lead_analytics_returns_zero_dashboard_for_unknown_user() -> None:
    """A Telegram user who never created any leads should still get a
    valid 200 with all metrics at zero — easier for the frontend than
    handling a 404."""

    def _other_user() -> TelegramInitData:
        return TelegramInitData(user_id=11111, first_name="Other", raw={})

    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = _other_user

    with TestClient(app) as client:
        asyncio.run(_create_tables(app.state.engine))
        response = client.get("/api/v1/analytics/leads")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_leads"] == 0
    assert payload["sold_leads"] == 0
    assert payload["win_rate_percent"] == 0.0
    assert payload["funnel"] == []
    assert payload["monthly"] == []


@pytest.mark.parametrize("days", [6, 366])
def test_lead_analytics_validates_period_bounds(days: int) -> None:
    app = _bootstrap_app()
    with TestClient(app) as client:
        response = client.get(f"/api/v1/analytics/leads?days={days}")
    assert response.status_code == 422
