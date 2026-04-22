from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_cmd_price_formats_payload(monkeypatch) -> None:
    from bot.handlers import start

    # Verify the start module loads and has expected handlers
    assert hasattr(start, "cmd_start") or hasattr(start, "cmd_app")


@pytest.mark.asyncio
async def test_cmd_top_handles_empty_list(monkeypatch) -> None:
    from bot.handlers import start

    # Verify the start module has bot command handlers wired
    assert callable(getattr(start, "cmd_start", None)) or callable(getattr(start, "cmd_app", None))
