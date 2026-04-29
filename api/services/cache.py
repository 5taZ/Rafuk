from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def get_json(self, key: str) -> Any: ...
    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    async def incr(self, key: str, ttl: int | None = None) -> int: ...
    async def delete(self, key: str) -> None: ...
    async def ping(self) -> bool: ...


class MemoryCache:
    """In-memory cache with TTL enforcement and LRU eviction."""

    MAX_ENTRIES = 500

    def __init__(self) -> None:
        self._storage: OrderedDict[str, tuple[str, float]] = OrderedDict()
        # (value, expires_at) — expires_at=0 means no expiry
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        entry = self._storage.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at > 0 and time.monotonic() >= expires_at:
            del self._storage[key]
            return None
        # Move to end (most recently used)
        self._storage.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        expires_at = 0.0
        if ttl and ttl > 0:
            expires_at = time.monotonic() + ttl
        self._storage[key] = (value, expires_at)
        self._storage.move_to_end(key)
        # Evict oldest if over capacity
        while len(self._storage) > self.MAX_ENTRIES:
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

    async def incr(self, key: str, ttl: int | None = None) -> int:
        """Atomically increment a counter. Returns the new value."""
        async with self._lock:
            entry = self._storage.get(key)
            if entry is not None:
                value, expires_at = entry
                if expires_at > 0 and time.monotonic() >= expires_at:
                    del self._storage[key]
                    count = 1
                    # Fresh TTL for re-created counter — don't reuse expired timestamp
                    new_expires = time.monotonic() + ttl if ttl else 0.0
                else:
                    try:
                        count = int(value) + 1
                    except (TypeError, ValueError):
                        count = 1
                    new_expires = expires_at
            else:
                count = 1
                new_expires = time.monotonic() + ttl if ttl else 0.0
            self._storage[key] = (str(count), new_expires)
            self._storage.move_to_end(key)
        return count

    async def delete(self, key: str) -> None:
        """Remove a key from the cache. No-op if the key does not exist."""
        self._storage.pop(key, None)

    async def ping(self) -> bool:
        return True


class RedisCache:
    def __init__(self, client: Redis) -> None:
        self._client = client

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

    async def aclose(self) -> None:
        """Close the Redis connection pool."""
        await self._client.aclose()

    async def get(self, key: str) -> str | None:
        try:
            return await self._client.get(key)
        except RedisError:
            logger.warning("Redis get failed for key=%s", key, exc_info=True)
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        try:
            await self._client.set(key, value, ex=ttl)
        except RedisError:
            logger.warning("Redis set failed for key=%s", key, exc_info=True)

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

    async def incr(self, key: str, ttl: int | None = None) -> int:
        """Atomically increment a counter using Redis INCR. Returns the new value.

        Uses SET NX EX for the initial key to make INCR + TTL atomic,
        avoiding the race where a process crashes between INCR and EXPIRE.
        """
        try:
            if ttl:
                # SET key 1 NX EX ttl — only sets if key doesn't exist
                created = await self._client.set(key, "1", nx=True, ex=ttl)
                if created:
                    return 1
                # Key already exists — just increment (TTL preserved)
                return await self._client.incr(key)
            return await self._client.incr(key)
        except RedisError:
            logger.warning("Redis incr failed for key=%s", key, exc_info=True)
            return 0

    async def delete(self, key: str) -> None:
        """Remove a key from Redis. No-op if the key does not exist."""
        try:
            await self._client.delete(key)
        except RedisError:
            logger.warning("Redis delete failed for key=%s", key, exc_info=True)

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            logger.warning("Redis ping failed", exc_info=True)
            return False
