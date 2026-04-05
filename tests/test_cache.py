from __future__ import annotations

import pytest

from api.services.cache import MemoryCache


@pytest.mark.asyncio
async def test_memory_cache_roundtrip() -> None:
    cache = MemoryCache()
    await cache.set("foo", "bar")
    assert await cache.get("foo") == "bar"


@pytest.mark.asyncio
async def test_memory_cache_json_roundtrip() -> None:
    cache = MemoryCache()
    payload = {"mean": 123.4}
    await cache.set_json("stats", payload)
    assert await cache.get_json("stats") == payload


@pytest.mark.asyncio
async def test_get_json_handles_corrupt_data() -> None:
    cache = MemoryCache()
    await cache.set("broken", "{not-json}")
    assert await cache.get_json("broken") is None
