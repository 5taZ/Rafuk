from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Protocol

from redis.asyncio import BlockingConnectionPool, Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class QuotaStoreUnavailableError(RuntimeError):
    pass


def digest_cache_key(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{prefix}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def get_json(self, key: str) -> Any: ...
    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    async def incr(self, key: str, ttl: int | None = None) -> int: ...
    async def decr(self, key: str) -> int: ...
    # RV-SEC-04: atomic "set only if missing" with a TTL (Redis SET NX EX).
    # True when this call created the key.
    async def set_if_absent(self, key: str, value: str, ttl: int) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def ping(self) -> bool: ...
    async def rolling_quota(
        self, key: str, *, now: float, short_limit: int, weekly_limit: int,
        request_id: str = "", refund_id: str = "",
    ) -> tuple[int, int, float, float, bool]: ...

    # PERF-NEW-4: batched pipeline ops
    async def pipeline_get(self, keys: list[str]) -> list[str | None]: ...
    async def pipeline_hgetall(self, keys: list[str]) -> list[dict[str, str]]: ...


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
                        # PR-20: a non-integer value at a counter key
                        # is a logic bug somewhere upstream (Redis
                        # eviction surfaced a stale serialised JSON
                        # blob, a test reused an incr key for
                        # ``set_json``, etc.). We still want to
                        # recover gracefully — counters reset to 1 —
                        # but ops needs a breadcrumb to find the
                        # offending caller. ``debug`` (not warning)
                        # because the same shape is also expected
                        # immediately after a Redis restart and we
                        # don't want to flood the warn channel.
                        logger.debug(
                            "MemoryCache.incr: non-integer value at key=%s; resetting to 1",
                            key,
                        )
                        count = 1
                    new_expires = expires_at
            else:
                count = 1
                new_expires = time.monotonic() + ttl if ttl else 0.0
            self._storage[key] = (str(count), new_expires)
            self._storage.move_to_end(key)
            # PR-06: enforce MAX_ENTRIES on the incr path too. The
            # previous shape only ran the eviction loop inside ``set``;
            # when the API runs on the MemoryCache fallback (Redis
            # unreachable), per-user rate-limit counters and replay
            # warnings (auth:replay_warn:{uid}, ai_rate:{uid}:...)
            # flow exclusively through this code path, so without the
            # eviction loop they could grow without bound.
            while len(self._storage) > self.MAX_ENTRIES:
                self._storage.popitem(last=False)
        return count

    async def decr(self, key: str) -> int:
        """RV-AI-07: undo one ``incr``; never creates a key or goes below 0."""
        async with self._lock:
            entry = self._storage.get(key)
            if entry is None:
                return 0
            value, expires_at = entry
            if expires_at > 0 and time.monotonic() >= expires_at:
                del self._storage[key]
                return 0
            try:
                count = int(value)
            except (TypeError, ValueError):
                return 0
            if count <= 0:
                return 0
            count -= 1
            # Keep the original expiry: a refunded quota unit must not
            # stretch the counter's window.
            self._storage[key] = (str(count), expires_at)
            return count

    async def set_if_absent(self, key: str, value: str, ttl: int) -> bool:
        """Store *value* only when *key* is missing or expired (RV-SEC-04)."""
        async with self._lock:
            entry = self._storage.get(key)
            now = time.monotonic()
            if entry is not None and (entry[1] <= 0 or now < entry[1]):
                return False
            self._storage[key] = (value, now + ttl if ttl and ttl > 0 else 0.0)
            self._storage.move_to_end(key)
            while len(self._storage) > self.MAX_ENTRIES:
                self._storage.popitem(last=False)
            return True

    async def rolling_quota(
        self, key: str, *, now: float, short_limit: int, weekly_limit: int,
        request_id: str = "", refund_id: str = "",
    ) -> tuple[int, int, float, float, bool]:
        async with self._lock:
            entry = self._storage.get(key)
            events = json.loads(entry[0]) if entry and (
                not entry[1] or entry[1] > time.monotonic()
            ) else {}
            events = {token: at for token, at in events.items() if at > now - 604800}
            if refund_id:
                events.pop(refund_id, None)
            short_used = sum(at > now - 18000 for at in events.values())
            accepted = not request_id or request_id in events or (
                short_used < short_limit and len(events) < weekly_limit
            )
            if request_id and accepted:
                events.setdefault(request_id, now)
            if events:
                expires = time.monotonic() + 604860 if request_id and accepted else (
                    entry[1] if entry else time.monotonic() + 604860
                )
                self._storage[key] = (json.dumps(events), expires)
                self._storage.move_to_end(key)
                while len(self._storage) > self.MAX_ENTRIES:
                    self._storage.popitem(last=False)
            else:
                self._storage.pop(key, None)
            short = sorted(at for at in events.values() if at > now - 18000)
            weekly = sorted(events.values())
            short_index = min(len(short) - 1, max(0, len(short) - short_limit))
            week_index = min(len(weekly) - 1, max(0, len(weekly) - weekly_limit))
            return (
                len(short), len(events), short[short_index] + 18000 if short else 0,
                weekly[week_index] + 604800 if weekly else 0, accepted,
            )

    async def delete(self, key: str) -> None:
        """Remove a key from the cache. No-op if the key does not exist."""
        async with self._lock:
            self._storage.pop(key, None)

    async def pipeline_get(self, keys: list[str]) -> list[str | None]:
        """PERF-NEW-4: fan-out to per-key get (no real round-trip to save)."""
        return [await self.get(k) for k in keys]

    async def pipeline_hgetall(self, keys: list[str]) -> list[dict[str, str]]:
        """PERF-NEW-4: no-op fan-out — MemoryCache has no hash type."""
        return [{} for _ in keys]

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
        #   max_connections=50      — pool ceiling so a stampeding
        #     watch-list fan-out can't open hundreds of sockets.
        # IN-04: a *blocking* pool — the default one raised
        # MaxConnectionsError on the first command past the ceiling, which
        # incr() turned into a spurious 429 and get() into a cache miss.
        # Now a burst waits up to 2 s for a free connection.
        pool = BlockingConnectionPool.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=3.0,
            socket_keepalive=True,
            retry_on_timeout=True,
            health_check_interval=30,
            max_connections=50,
            timeout=2,
        )
        return cls(Redis.from_pool(pool))

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
    _RELEASE_LOCK_LUA = (
        "if redis.call('GET', KEYS[1]) == ARGV[1] then "
        "  return redis.call('DEL', KEYS[1]) "
        "end "
        "return 0"
    )
    # RV-AI-07: DECR only an existing positive counter — a plain DECR
    # would recreate an expired counter at -1 with no TTL (an extra unit
    # plus a key that never expires). DECR itself keeps the TTL.
    _DECR_FLOOR_LUA = (
        "local n = tonumber(redis.call('GET', KEYS[1])) "
        "if n and n > 0 then "
        "  return redis.call('DECR', KEYS[1]) "
        "end "
        "return 0"
    )
    # Both windows reserve the same request atomically; rejected work and
    # retries cannot consume one window without the other.
    _ROLLING_QUOTA_LUA = """
        local now = tonumber(ARGV[1])
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - 604800)
        if ARGV[5] ~= '' then redis.call('ZREM', KEYS[1], ARGV[5]) end
        local lower = '(' .. tostring(now - 18000)
        local short = redis.call('ZCOUNT', KEYS[1], lower, '+inf')
        local weekly = redis.call('ZCARD', KEYS[1])
        local accepted = 1
        if ARGV[4] ~= '' then
            local existing = redis.call('ZSCORE', KEYS[1], ARGV[4])
            if not existing and (short >= tonumber(ARGV[2]) or weekly >= tonumber(ARGV[3])) then
                accepted = 0
            else
                redis.call('ZADD', KEYS[1], 'NX', now, ARGV[4])
                redis.call('EXPIRE', KEYS[1], 604860)
            end
        end
        short = redis.call('ZCOUNT', KEYS[1], lower, '+inf')
        weekly = redis.call('ZCARD', KEYS[1])
        local short_skip = math.min(math.max(0, short - tonumber(ARGV[2])), math.max(0, short - 1))
        local week_skip = math.min(math.max(0, weekly - tonumber(ARGV[3])),
                                   math.max(0, weekly - 1))
        local short_first = redis.call('ZRANGEBYSCORE', KEYS[1], lower, '+inf',
                                      'WITHSCORES', 'LIMIT', short_skip, 1)
        local week_first = redis.call('ZRANGE', KEYS[1], week_skip, week_skip, 'WITHSCORES')
        local short_reset = 0
        local week_reset = 0
        if #short_first > 0 then short_reset = (tonumber(short_first[2]) + 18000) * 1000 end
        if #week_first > 0 then week_reset = (tonumber(week_first[2]) + 604800) * 1000 end
        return {short, weekly, math.floor(short_reset), math.floor(week_reset), accepted}
    """

    async def rolling_quota(
        self, key: str, *, now: float, short_limit: int, weekly_limit: int,
        request_id: str = "", refund_id: str = "",
    ) -> tuple[int, int, float, float, bool]:
        try:
            short, weekly, short_reset, week_reset, accepted = await self._client.eval(
                self._ROLLING_QUOTA_LUA, 1, key, str(now), short_limit, weekly_limit,
                request_id, refund_id,
            )
            return int(short), int(weekly), short_reset / 1000, week_reset / 1000, bool(accepted)
        except RedisError as exc:
            logger.warning("Redis rolling quota unavailable", exc_info=True)
            raise QuotaStoreUnavailableError from exc

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

    async def decr(self, key: str) -> int:
        """RV-AI-07: undo one ``incr``; never creates a key or goes below 0.

        On Redis failure the refund is simply lost (returns 0) — the
        counter stays charged, which is the safe direction for a quota.
        """
        try:
            return int(await self._client.eval(self._DECR_FLOOR_LUA, 1, key))
        except RedisError:
            logger.warning("Redis decr failed for key=%s", key, exc_info=True)
            return 0

    async def set_if_absent(self, key: str, value: str, ttl: int) -> bool:
        """Atomic ``SET key value NX EX ttl`` (RV-SEC-04).

        Fails closed like ``incr``: on a Redis error nobody gets the key,
        so a budget guarded by it is denied rather than unlimited.
        """
        try:
            return bool(await self._client.set(key, value, ex=ttl, nx=True))
        except RedisError:
            logger.warning("Redis set_if_absent failed for key=%s", key, exc_info=True)
            return False

    async def delete(self, key: str) -> None:
        """Remove a key from Redis. No-op if the key does not exist."""
        try:
            await self._client.delete(key)
        except RedisError:
            logger.warning("Redis delete failed for key=%s", key, exc_info=True)

    async def pipeline_get(self, keys: list[str]) -> list[str | None]:
        """PERF-NEW-4: batched GET over a Redis pipeline."""
        if not keys:
            return []
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                for k in keys:
                    pipe.get(k)
                return await pipe.execute()
        except RedisError:
            logger.warning("Redis pipeline_get failed", exc_info=True)
            return [await self.get(k) for k in keys]

    async def pipeline_hgetall(self, keys: list[str]) -> list[dict[str, str]]:
        """PERF-NEW-4: batched HGETALL over a Redis pipeline."""
        if not keys:
            return []
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                for k in keys:
                    pipe.hgetall(k)
                results = await pipe.execute()
            return [dict(r) if r else {} for r in results]
        except RedisError:
            logger.warning("Redis pipeline_hgetall failed", exc_info=True)
            return [{} for _ in keys]

    async def try_acquire_lock(
        self,
        key: str,
        token: str,
        *,
        ttl_seconds: int,
    ) -> bool | None:
        try:
            return bool(await self._client.set(key, token, ex=ttl_seconds, nx=True))
        except RedisError:
            logger.warning("Redis lock acquire failed for key=%s", key, exc_info=True)
            return None

    async def release_lock(self, key: str, token: str) -> None:
        try:
            await self._client.eval(self._RELEASE_LOCK_LUA, 1, key, token)
        except RedisError:
            logger.warning("Redis lock release failed for key=%s", key, exc_info=True)

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            logger.warning("Redis ping failed", exc_info=True)
            return False
