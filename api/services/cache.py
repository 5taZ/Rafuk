from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


def digest_cache_key(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{prefix}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


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

    # Bumped from 500 — the previous cap was too aggressive: a fresh
    # search alone caches ~50 entries (raw Kufar dataset, computed
    # listings, segments, geography, history points). At 500 entries
    # the LRU evicts active hot keys for the next request, defeating
    # the cache. 5000 fits comfortably in a few MB and matches what
    # the audit recommended.
    MAX_ENTRIES = 5000

    def __init__(self) -> None:
        self._storage: OrderedDict[str, tuple[str, float]] = OrderedDict()
        # (value, expires_at) — expires_at=0 means no expiry
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        async with self._lock:
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
        async with self._lock:
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
        async with self._lock:
            self._storage.pop(key, None)

    async def ping(self) -> bool:
        return True


class RedisCache:
    # NOTE: previously RedisCache spun JSON encode/decode through a
    # 2-thread ThreadPoolExecutor on the assumption that json.loads /
    # json.dumps could block the event loop. In practice the cached
    # payloads here are small (listings response, segment dicts —
    # < 200 KB) and Python's json is implemented in C. The
    # run_in_executor round-trip itself costs ~0.1ms per call which
    # dwarfs the actual parse time on payloads under ~1 MB. Doing the
    # JSON work synchronously in the event loop is measurably faster
    # AND removes the cross-thread contention on big lists.
    # (PERF-H5)

    def __init__(self, client: Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> RedisCache:
        # INF-04: enable connection health checks + TCP keepalive so a
        # silently-dropped Redis socket (NAT timeout, load balancer
        # rolling restart, network blip) reconnects on the next call
        # instead of surfacing as a long httpx-timeout-shaped read
        # error and requiring a worker restart.
        #
        #   health_check_interval=30 — redis-py PINGs the server every
        #     30 s on idle pool connections and recycles any that fail.
        #   socket_keepalive=True   — kernel-level keepalive probes
        #     drop dead sockets even when the redis-py heartbeat is
        #     itself stuck.
        #   max_connections=20      — pool ceiling so a stampeding
        #     watch-list fan-out can't open hundreds of sockets.
        return cls(
            Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=3.0,
                socket_keepalive=True,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=20,
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
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        await self.set(key, serialized, ttl)

    # Atomic INCR + conditional EXPIRE.
    #
    # The previous implementation issued two separate commands. Between
    # them the event loop can yield to another coroutine, and on the
    # network round-trip a transient timeout would leave the key with
    # no TTL — counters then accumulated forever and an attacker could
    # silently slip past the rate limit once the key was orphaned.
    #
    # Redis runs the whole script atomically: no other command can land
    # between the INCR and the EXPIRE, and a network failure either
    # delivers both ops or neither.
    _INCR_TTL_LUA = (
        "local n = redis.call('INCR', KEYS[1]) "
        "if n == 1 and ARGV[1] ~= '0' then "
        "  redis.call('EXPIRE', KEYS[1], ARGV[1]) "
        "end "
        "return n"
    )

    async def incr(self, key: str, ttl: int | None = None) -> int:
        """Atomically increment a counter and apply TTL on first set.

        Uses a tiny Lua script so INCR + EXPIRE land in the same Redis
        operation — no inter-command race.

        On Redis failure returns a very high number so rate-limit checks
        fail **closed** (deny the request) instead of silently allowing it.
        """
        ttl_arg = str(int(ttl)) if ttl else "0"
        try:
            count = await self._client.eval(self._INCR_TTL_LUA, 1, key, ttl_arg)
            return int(count)
        except RedisError:
            logger.warning("Redis incr failed for key=%s", key, exc_info=True)
            return 999_999

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
