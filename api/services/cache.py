from __future__ import annotations

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
    async def ping(self) -> bool: ...


class MemoryCache:
    """In-memory cache with TTL enforcement and LRU eviction."""

    MAX_ENTRIES = 500

    def __init__(self) -> None:
        self._storage: OrderedDict[str, tuple[str, float]] = OrderedDict()
        # (value, expires_at) — expires_at=0 means no expiry

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

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            logger.warning("Redis ping failed", exc_info=True)
            return False
