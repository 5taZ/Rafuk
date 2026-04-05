from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_cmd_price_formats_payload(monkeypatch) -> None:
    from bot.handlers import price

    async def fake_fetch(query: str) -> dict[str, object]:
        assert query == "iphone"
        return {
            "count": 3,
            "mean": 100.0,
            "median": 90.0,
            "min": 70.0,
            "max": 120.0,
            "currency": "USD",
        }

    monkeypatch.setattr(price, "fetch_price_stats", fake_fetch)
    message = SimpleNamespace(answer=AsyncMock())
    command = SimpleNamespace(args="iphone")
    await price.cmd_price(message, command)  # type: ignore[arg-type]
    message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_top_handles_empty_list(monkeypatch) -> None:
    from bot.handlers import price

    async def fake_fetch(query: str) -> dict[str, object]:
        assert query == "iphone"
        return {"listings": []}

    monkeypatch.setattr(price, "fetch_listings", fake_fetch)
    message = SimpleNamespace(answer=AsyncMock())
    command = SimpleNamespace(args="iphone")
    await price.cmd_top(message, command)  # type: ignore[arg-type]
    message.answer.assert_awaited()
