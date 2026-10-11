from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from api.services.cache import MemoryCache, RedisCache
from api.services.query_pipeline import DatasetCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


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
async def test_memory_cache_honors_ttl() -> None:
    clock = FakeClock()
    cache = MemoryCache(clock=clock)
    await cache.set("short", "v", ttl=1)
    await cache.set("forever", "v")
    clock.now += 0.5
    assert await cache.get("short") == "v"
    clock.now += 1.0
    assert await cache.get("short") is None
    assert await cache.get("forever") == "v"
    assert len(cache) == 1


@pytest.mark.asyncio
async def test_memory_cache_non_positive_ttl_stores_nothing() -> None:
    cache = MemoryCache()
    await cache.set("k", "old")
    await cache.set("k", "new", ttl=0)
    assert await cache.get("k") is None


@pytest.mark.asyncio
async def test_memory_cache_evicts_least_recently_used() -> None:
    cache = MemoryCache(max_entries=2)
    await cache.set("a", "1")
    await cache.set("b", "2")
    assert await cache.get("a") == "1"  # "a" becomes most recently used
    await cache.set("c", "3")
    assert await cache.get("b") is None
    assert await cache.get("a") == "1"
    assert await cache.get("c") == "3"
    assert len(cache) == 2


@pytest.mark.asyncio
async def test_memory_cache_eviction_drops_expired_entries_first() -> None:
    clock = FakeClock()
    cache = MemoryCache(max_entries=2, clock=clock)
    await cache.set("old", "1")
    await cache.set("expiring", "2", ttl=1)
    clock.now += 5
    await cache.set("new", "3")
    assert await cache.get("old") == "1"
    assert await cache.get("new") == "3"


@pytest.mark.asyncio
async def test_memory_cache_aclose_clears_storage() -> None:
    cache = MemoryCache()
    await cache.set("k", "v")
    await cache.aclose()
    assert await cache.get("k") is None


def make_redis_client() -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    client.aclose = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_redis_cache_uses_redis_when_healthy() -> None:
    client = make_redis_client()
    client.get.return_value = "cached"
    cache = RedisCache(client)
    await cache.set("k", "v", ttl=10)
    assert await cache.get("k") == "cached"
    client.set.assert_awaited_once_with("k", "v", ex=10)
    assert cache.using_fallback is False


@pytest.mark.asyncio
async def test_redis_cache_falls_back_to_memory_after_error() -> None:
    clock = FakeClock()
    client = make_redis_client()
    client.set.side_effect = RedisConnectionError("down")
    cache = RedisCache(client, fallback_seconds=30, clock=clock)

    await cache.set_json("currency:rates", {"rates": {"USD": 3.2}}, ttl=3600)
    assert cache.using_fallback is True
    # While in fallback mode Redis is not contacted at all and the value is served from memory.
    assert await cache.get_json("currency:rates") == {"rates": {"USD": 3.2}}
    client.get.assert_not_awaited()
    assert client.set.await_count == 1

    clock.now += 31
    assert cache.using_fallback is False
    client.get.return_value = "from-redis"
    assert await cache.get("currency:rates") == "from-redis"
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_cache_get_error_serves_memory_value() -> None:
    clock = FakeClock()
    client = make_redis_client()
    client.get.side_effect = RedisConnectionError("down")
    cache = RedisCache(client, clock=clock)
    assert await cache.get("missing") is None
    assert cache.using_fallback is True
    await cache.set("k", "v", ttl=60)
    assert await cache.get("k") == "v"
    client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_cache_ping_failure_returns_false() -> None:
    client = make_redis_client()
    client.ping.side_effect = RedisConnectionError("down")
    assert await RedisCache(client).ping() is False


@pytest.mark.asyncio
async def test_redis_cache_aclose_closes_client() -> None:
    client = make_redis_client()
    cache = RedisCache(client)
    await cache.aclose()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_cache_from_url_can_be_closed() -> None:
    cache = RedisCache.from_url("redis://localhost:6399/0")
    await cache.aclose()


# --- Dataset cache (api/services/query_pipeline.py) ---------------------------------------------


@pytest.mark.asyncio
async def test_dataset_cache_reuses_value_until_ttl_expires() -> None:
    clock = FakeClock()
    cache = DatasetCache(ttl_seconds=60, clock=clock)
    loads = 0

    async def loader() -> dict:
        nonlocal loads
        loads += 1
        return {"ads": [], "load": loads}

    assert (await cache.get_or_load("k", loader))["load"] == 1
    assert (await cache.get_or_load("k", loader))["load"] == 1
    clock.now += 61
    assert (await cache.get_or_load("k", loader))["load"] == 2


@pytest.mark.asyncio
async def test_dataset_cache_lru_cap() -> None:
    cache = DatasetCache(max_entries=2)

    def loader_for(value: str):
        async def loader() -> dict:
            return {"value": value}

        return loader

    await cache.get_or_load("a", loader_for("a"))
    await cache.get_or_load("b", loader_for("b"))
    await cache.get_or_load("a", loader_for("a2"))  # refreshes "a"
    await cache.get_or_load("c", loader_for("c"))
    assert len(cache) == 2
    assert cache.get("b") is None
    assert cache.get("a") == {"value": "a"}


@pytest.mark.asyncio
async def test_dataset_cache_deduplicates_concurrent_loads() -> None:
    cache = DatasetCache()
    loads = 0
    release = asyncio.Event()

    async def loader() -> dict:
        nonlocal loads
        loads += 1
        await release.wait()
        return {"ads": [1]}

    waiters = [asyncio.create_task(cache.get_or_load("k", loader)) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*waiters)
    assert loads == 1
    assert all(result is results[0] for result in results)


@pytest.mark.asyncio
async def test_dataset_cache_does_not_cache_failures() -> None:
    cache = DatasetCache()
    attempts = 0

    async def loader() -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("kufar down")
        return {"ads": []}

    with pytest.raises(RuntimeError):
        await cache.get_or_load("k", loader)
    assert await cache.get_or_load("k", loader) == {"ads": []}
    assert attempts == 2


@pytest.mark.asyncio
async def test_dataset_cache_load_survives_cancelled_waiter() -> None:
    cache = DatasetCache()
    release = asyncio.Event()
    loads = 0

    async def loader() -> dict:
        nonlocal loads
        loads += 1
        await release.wait()
        return {"ads": ["x"]}

    first = asyncio.create_task(cache.get_or_load("k", loader))
    second = asyncio.create_task(cache.get_or_load("k", loader))
    await asyncio.sleep(0)
    first.cancel()
    release.set()
    assert await second == {"ads": ["x"]}
    assert loads == 1
    assert cache.get("k") == {"ads": ["x"]}


def _ads(count: int) -> list[dict]:
    return [{"ad_id": index + 1} for index in range(count)]


def test_dataset_cache_purges_expired_entries_on_access() -> None:
    clock = FakeClock()
    cache = DatasetCache(ttl_seconds=60, clock=clock)
    cache.put("a", {"ads": _ads(100)})
    cache.put("b", {"ads": _ads(50)})
    assert cache.total_ads == 150

    clock.now += 61
    # Writing another key drops the stale datasets instead of keeping them until eviction.
    cache.put("c", {"ads": _ads(10)})
    assert len(cache) == 1
    assert cache.total_ads == 10

    clock.now += 61
    assert cache.get("missing") is None
    assert len(cache) == 0
    assert cache.total_ads == 0


@pytest.mark.asyncio
async def test_dataset_cache_get_or_load_purges_expired_entries() -> None:
    clock = FakeClock()
    cache = DatasetCache(ttl_seconds=60, clock=clock)

    async def loader() -> dict:
        return {"ads": _ads(5)}

    await cache.get_or_load("a", loader)
    clock.now += 61
    await cache.get_or_load("b", loader)
    assert len(cache) == 1
    assert cache.total_ads == 5


def test_dataset_cache_is_bounded_by_total_ads() -> None:
    cache = DatasetCache(max_entries=10, max_ads=100)
    cache.put("a", {"ads": _ads(40)})
    cache.put("b", {"ads": _ads(40)})
    assert cache.get("a") is not None  # "b" is now the least recently used entry
    cache.put("c", {"ads": _ads(40)})

    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None
    assert cache.total_ads == 80

    # A replaced entry is not counted twice.
    cache.put("c", {"ads": _ads(20)})
    assert cache.total_ads == 60


def test_dataset_cache_skips_a_dataset_larger_than_the_bound() -> None:
    cache = DatasetCache(max_ads=100)
    cache.put("small", {"ads": _ads(10)})
    cache.put("huge", {"ads": _ads(101)})
    assert cache.get("huge") is None
    assert cache.get("small") is not None
    assert cache.total_ads == 10


def test_dataset_cache_keeps_truncated_datasets_for_a_shorter_time() -> None:
    clock = FakeClock()
    cache = DatasetCache(ttl_seconds=120, truncated_ttl_seconds=30, clock=clock)
    cache.put("full", {"ads": [], "truncated": False})
    cache.put("partial", {"ads": [], "truncated": True})

    clock.now += 31
    assert cache.get("partial") is None
    assert cache.get("full") is not None

    # The truncated TTL never exceeds the normal one.
    short = DatasetCache(ttl_seconds=10, truncated_ttl_seconds=30)
    assert short.ttl_for({"truncated": True}) == 10
