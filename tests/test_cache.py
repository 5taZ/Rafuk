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


@pytest.mark.asyncio
async def test_memory_cache_incr_traces_corrupt_counter(caplog) -> None:
    """PR-20: a non-integer entry at a counter key is a logic bug
    somewhere upstream (Redis restart with stale JSON, an
    accidental ``set_json`` over a counter key). The fallback
    behaviour is to reset to 1, but ops needs a breadcrumb to
    track the offending caller. ``logger.debug`` keeps the warn
    channel clean — Redis restarts surface the same shape
    immediately afterwards.
    """
    import logging

    cache = MemoryCache()
    # Seed a corrupt value at the counter key (mimics a stale JSON
    # blob left behind by a previous ``set_json`` on the same key).
    await cache.set("ai_rate:42:negotiate", "not-a-number")

    caplog.set_level(logging.DEBUG, logger="api.services.cache")
    new_count = await cache.incr("ai_rate:42:negotiate", ttl=60)

    # Counter recovers to 1 — same as if the key had been missing.
    assert new_count == 1
    # Breadcrumb is in the log.
    assert any(
        "non-integer value" in rec.message and "ai_rate:42:negotiate" in rec.message
        for rec in caplog.records
    ), "Expected debug breadcrumb on corrupt-counter incr"


@pytest.mark.asyncio
async def test_memory_cache_incr_enforces_max_entries() -> None:
    """PR-06: ``incr()`` must respect ``MAX_ENTRIES`` so the in-memory
    fallback path can't grow unbounded. Real workload that exercises
    this: per-user replay counters (``auth:replay_warn:{uid}``) and
    AI rate-limit buckets (``ai_rate:{uid}:{endpoint}``) when Redis
    is unavailable — both go through ``incr`` only.
    """
    cache = MemoryCache()
    # Push past the eviction threshold using ``incr`` exclusively so
    # we know the eviction loop on the incr path is what's keeping
    # the size bounded (the ``set`` path already had its own loop).
    for i in range(cache.MAX_ENTRIES + 50):
        await cache.incr(f"counter:{i}")
    # The dict must have shed the oldest entries down to (or below)
    # the configured ceiling. We don't pin the exact size because
    # the eviction loop is "while > MAX_ENTRIES", not "== ".
    assert len(cache._storage) <= cache.MAX_ENTRIES
    # The most recently incremented key must still be present.
    assert f"counter:{cache.MAX_ENTRIES + 49}" in cache._storage
