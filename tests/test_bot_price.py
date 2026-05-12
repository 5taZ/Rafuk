from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_cmd_deals_formats_summary() -> None:
    from bot.handlers.analytics import cmd_deals

    fake_data = {
        "funnel": [
            {"status": "in_progress", "count": 3},
            {"status": "researching", "count": 1},
            {"status": "bought", "count": 2},
            {"status": "sold", "count": 1},
            {"status": "new", "count": 5},
        ],
        "total_cost_byn": 5000,
        "total_profit_byn": 1200,
        "average_roi_percent": 24,
    }

    message = SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123),
    )

    with patch("bot.handlers.analytics._api_get", return_value=fake_data):
        await cmd_deals(message)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "Активные сделки" in text
    assert "В работе — 4" in text
    assert "1,200" in text


@pytest.mark.asyncio
async def test_cmd_deals_shows_error_on_api_failure() -> None:
    from bot.handlers.analytics import cmd_deals

    message = SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123),
    )

    with patch("bot.handlers.analytics._api_get", return_value=None):
        await cmd_deals(message)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "⚠️" in text


@pytest.mark.asyncio
async def test_cmd_profit_formats_finance_summary() -> None:
    from bot.handlers.analytics import cmd_profit

    fake_data = {
        "total_cost_byn": 10000,
        "total_revenue_byn": 13500,
        "total_profit_byn": 3500,
        "average_roi_percent": 35,
        "sold_leads": 6,
    }

    message = SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123),
    )

    with patch("bot.handlers.analytics._api_get", return_value=fake_data):
        await cmd_profit(message)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "Финансы" in text
    assert "3,500" in text
    assert "35%" in text


@pytest.mark.asyncio
async def test_cmd_stats_requires_query_argument() -> None:
    from bot.handlers.analytics import cmd_stats

    message = SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123),
        text="/stats",
    )

    await cmd_stats(message)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "/stats" in text


@pytest.mark.asyncio
async def test_cmd_stats_returns_price_stats() -> None:
    from bot.handlers.analytics import cmd_stats

    fake_data = {
        "median": 2000,
        "total_results": 42,
        "count": 30,
        "q1": 1800,
        "q3": 2300,
    }

    message = SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123),
        text="/stats iphone 15",
    )

    with patch("bot.handlers.analytics._api_get", return_value=fake_data):
        await cmd_stats(message)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "iphone 15" in text
    assert "2,000" in text
