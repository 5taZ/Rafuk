from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_CACHE_MAX_ENTRIES = 1024
# How long RedisCache serves from its in-process fallback after a Redis error.
REDIS_FALLBACK_SECONDS = 30.0


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def get_json(self, key: str) -> Any: ...
    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    async def ping(self) -> bool: ...
    async def aclose(self) -> None: ...


class MemoryCache:
    """In-process cache with per-key TTL (monotonic clock) and an LRU size cap."""

    def __init__(
        self,
        max_entries: int = DEFAULT_MEMORY_CACHE_MAX_ENTRIES,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._clock = clock
        # key -> (expires_at or None for no expiry, value)
        self._storage: OrderedDict[str, tuple[float | None, str]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._storage)

    async def get(self, key: str) -> str | None:
        entry = self._storage.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at is not None and expires_at <= self._clock():
            del self._storage[key]
            return None
        self._storage.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        if ttl is not None and ttl <= 0:
            # Same outcome as Redis rejecting a non-positive expiry: nothing is stored.
            self._storage.pop(key, None)
            return
        expires_at = self._clock() + ttl if ttl is not None else None
        self._storage[key] = (expires_at, value)
        self._storage.move_to_end(key)
        self._evict()

    def _evict(self) -> None:
        if len(self._storage) <= self._max_entries:
            return
        now = self._clock()
        expired = [
            key
            for key, (expires_at, _) in self._storage.items()
            if expires_at is not None and expires_at <= now
        ]
        for key in expired:
            del self._storage[key]
        while len(self._storage) > self._max_entries:
            self._storage.popitem(last=False)

    async def get_json(self, key: str) -> Any:
        value = await self.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Ignoring corrupt JSON in memory cache for key=%s", key)
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self.set(key, json.dumps(value, default=str), ttl)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self._storage.clear()


class RedisCache:
    """Redis-backed cache that degrades to an in-process cache while Redis is failing."""

    def __init__(
        self,
        client: Redis,
        *,
        fallback: MemoryCache | None = None,
        fallback_seconds: float = REDIS_FALLBACK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._fallback = fallback or MemoryCache(clock=clock)
        self._fallback_seconds = fallback_seconds
        self._clock = clock
        self._fallback_until = 0.0

    @classmethod
    def from_url(cls, url: str) -> RedisCache:
        return cls(
            Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
                retry_on_timeout=False,
            )
        )

    @property
    def using_fallback(self) -> bool:
        return self._clock() < self._fallback_until

    def _mark_failure(self) -> None:
        if not self.using_fallback:
            logger.warning(
                "Redis is unavailable, serving from the in-memory cache for %.0f s",
                self._fallback_seconds,
            )
        self._fallback_until = self._clock() + self._fallback_seconds

    async def get(self, key: str) -> str | None:
        if self.using_fallback:
            return await self._fallback.get(key)
        try:
            return await self._client.get(key)
        except RedisError:
            logger.warning("Redis get failed for key=%s", key, exc_info=True)
            self._mark_failure()
            return await self._fallback.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        if self.using_fallback:
            await self._fallback.set(key, value, ttl)
            return
        try:
            await self._client.set(key, value, ex=ttl)
        except RedisError:
            logger.warning("Redis set failed for key=%s", key, exc_info=True)
            self._mark_failure()
            await self._fallback.set(key, value, ttl)

    async def get_json(self, key: str) -> Any:
        payload = await self.get(key)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Ignoring corrupt JSON in redis for key=%s", key)
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self.set(key, json.dumps(value, default=str), ttl)

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            logger.warning("Redis ping failed", exc_info=True)
            return False

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except RedisError:
            logger.warning("Redis client close failed", exc_info=True)
        await self._fallback.aclose()
