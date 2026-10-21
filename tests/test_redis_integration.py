"""RV-OPS-04: RedisCache against a real Redis (TEST_REDIS_URL).

Until RV-OPS-04 the app caches under test never talked to Redis: conftest
hard-coded ``localhost:6380`` while CI's service listens on 6379, so the
Lua INCR+EXPIRE behind the AI quotas, the singleflight locks, the SCAN
erasure on consent revoke / account deletion and the pipelined metrics
were first executed in production. These tests drive exactly those paths
through ``RedisCache``.

They skip when nothing answers on TEST_REDIS_URL (local runs without
Redis). CI sets TEST_REDIS_REQUIRED=1, which turns the skip into a
failure — see ``require_test_redis`` in conftest.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.services.account_status import (
    QUOTA_BUCKET_AI,
    consume_quota,
    quota_key,
    quota_snapshot,
    quota_window,
)
from api.services.cache import RedisCache
from tests.conftest import TEST_REDIS_URL

# A fixed instant keeps the quota key and its TTL deterministic.
_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


# ── INCR + EXPIRE (AI quotas) ────────────────────────────────────────────


async def test_consume_quota_counts_in_redis_with_a_single_ttl(redis_cache) -> None:
    client = redis_cache._client
    key = quota_key(QUOTA_BUCKET_AI, 4242, now=_NOW)
    window = quota_window(_NOW)

    first = await consume_quota(
        redis_cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=4242, limit=2, now=_NOW,
    )
    assert (first.used, first.remaining) == (1, 1)
    ttl_after_first = await client.ttl(key)
    assert 0 < ttl_after_first <= window.ttl_seconds

    second = await consume_quota(
        redis_cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=4242, limit=2, now=_NOW,
    )
    assert (second.used, second.remaining) == (2, 0)

    with pytest.raises(HTTPException) as excinfo:
        await consume_quota(
            redis_cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=4242, limit=2, now=_NOW,
        )
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["error"] == "quota_exceeded"

    # RV-AI-07: consume_quota rolls the rejected attempt back (decr), so
    # the counter stays at the limit instead of creeping past it.
    assert await client.get(key) == "2"
    # The Lua script sets EXPIRE only on the first INCR: later calls must
    # not push the reset further out (that would make the quota window
    # slide with every request).
    assert 0 < await client.ttl(key) <= ttl_after_first
    snapshot = await quota_snapshot(
        redis_cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=4242, limit=2, now=_NOW,
    )
    assert (snapshot.used, snapshot.remaining) == (2, 0)


async def test_incr_is_atomic_under_concurrency(redis_cache) -> None:
    # Stays below the pool's max_connections=20; see the xfail below.
    counts = await asyncio.gather(
        *(redis_cache.incr("ops04:concurrent", ttl=60) for _ in range(15))
    )
    assert sorted(counts) == list(range(1, 16))
    assert 0 < await redis_cache._client.ttl("ops04:concurrent") <= 60


@pytest.mark.xfail(
    strict=True,
    reason=(
        "RV-OPS-04 finding: RedisCache.from_url caps the pool at 20 with a "
        "non-blocking ConnectionPool, so the 21st concurrent command raises "
        "MaxConnectionsError — incr() then fails closed (999999 → spurious "
        "429 quota_exceeded), get() misses, locks report 'unavailable'. "
        "Drop this marker once the pool waits for a free connection."
    ),
)
async def test_burst_beyond_pool_size_is_served(redis_cache) -> None:
    counts = await asyncio.gather(
        *(redis_cache.incr("ops04:burst", ttl=60) for _ in range(25))
    )
    assert sorted(counts) == list(range(1, 26))


async def test_incr_without_ttl_never_expires(redis_cache) -> None:
    assert await redis_cache.incr("ops04:no_ttl") == 1
    assert await redis_cache.incr("ops04:no_ttl", ttl=30) == 2
    # -1: exists without expiry. A TTL passed after the first INCR must
    # not be applied retroactively either.
    assert await redis_cache._client.ttl("ops04:no_ttl") == -1


async def test_unreachable_redis_fails_closed() -> None:
    """No Redis needed: a refused connection must deny rate-limited work
    instead of letting it through."""
    cache = RedisCache.from_url("redis://127.0.0.1:1/0")
    try:
        assert await cache.incr("ops04:down", ttl=5) == 999_999
        assert await cache.try_acquire_lock("ops04:down:lock", "t", ttl_seconds=5) is None
        assert await cache.ping() is False
    finally:
        await cache.aclose()


# ── Locks / distributed singleflight ─────────────────────────────────────


async def test_lock_is_exclusive_and_release_checks_the_token(redis_cache) -> None:
    client = redis_cache._client
    key = "kufar:dataset:ops04:singleflight"

    assert await redis_cache.try_acquire_lock(key, "owner", ttl_seconds=30) is True
    assert await redis_cache.try_acquire_lock(key, "intruder", ttl_seconds=30) is False
    assert 0 < await client.ttl(key) <= 30

    # A late release from a worker whose lock already expired and was
    # taken over must not free the new owner's lock.
    await redis_cache.release_lock(key, "intruder")
    assert await client.get(key) == "owner"

    await redis_cache.release_lock(key, "owner")
    assert await client.exists(key) == 0
    assert await redis_cache.try_acquire_lock(key, "next", ttl_seconds=30) is True


async def test_distributed_singleflight_fetches_once_across_workers(
    redis_cache, monkeypatch,
) -> None:
    """Two API workers (two Redis pools) miss the same dataset at once:
    only the lock owner calls Kufar, the other waits for the cached
    response, and the lock is released afterwards."""
    from api.services import query_pipeline

    monkeypatch.setattr(query_pipeline, "_DISTRIBUTED_SINGLEFLIGHT_POLL_SECONDS", 0.01)
    release_fetch = asyncio.Event()
    calls: list[dict] = []

    class _SlowKufar:
        async def search_all_ads(self, **kwargs):
            calls.append(kwargs)
            await release_fetch.wait()
            return {
                "total": 1,
                "ads": [{"ad_id": 7, "subject": "iPhone", "price_byn": 150000, "price_usd": 4700}],
            }

    cache_key = "kufar:dataset:ops04"
    lock_key = query_pipeline._dataset_singleflight_lock_key(cache_key)
    settings = SimpleNamespace(cache_ttl_seconds=60)
    second_worker = RedisCache.from_url(TEST_REDIS_URL)

    def _fetch(cache):
        return query_pipeline._fetch_dataset_response_with_distributed_singleflight(
            query="iphone",
            currency="BYN",
            effective_kwargs={},
            settings=settings,
            client=_SlowKufar(),
            cache=cache,
            cache_key=cache_key,
        )

    try:
        owner = asyncio.create_task(_fetch(redis_cache))
        for _ in range(200):
            if await redis_cache._client.exists(lock_key):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("the owner never took the Redis singleflight lock")
        follower = asyncio.create_task(_fetch(second_worker))
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert not follower.done()

        release_fetch.set()
        owner_response, follower_response = await asyncio.wait_for(
            asyncio.gather(owner, follower), timeout=10,
        )
    finally:
        await second_worker.aclose()

    assert len(calls) == 1
    assert follower_response == owner_response
    assert await redis_cache.get_json(cache_key) == owner_response
    assert await redis_cache._client.exists(lock_key) == 0


# ── SCAN erasure (consent revoke / account deletion) ─────────────────────


_USER = 777001
_OTHER = 777002


async def _seed_user_keys(cache: RedisCache, telegram_user_id: int) -> None:
    client = cache._client
    # More keys than the SCAN COUNT (100) so the cursor loop has to iterate.
    for i in range(250):
        await client.set(f"ai_task:u{telegram_user_id}:t{i}", "{}")
    await client.set(f"ai_listing:u{telegram_user_id}:draft", "{}")
    await client.set(f"ai_rate:{telegram_user_id}:negotiate", "1")
    await client.set(f"ai_daily:{telegram_user_id}", "1")
    await client.set(f"quota:ai:{telegram_user_id}:2026-01-15", "3")
    await client.set(f"quota:assistant:{telegram_user_id}:2026-01-15", "1")
    await client.set(f"auth:blacklist:{telegram_user_id}", "1")
    await client.set(f"auth:replay_warn:{telegram_user_id}", "2")
    await cache.set_json(
        f"auth:initdata:digest-{telegram_user_id}",
        {"ip": "203.0.113.5", "first_seen_ts": 1, "user_id": telegram_user_id},
    )


async def _all_keys(cache: RedisCache) -> set[str]:
    return {key async for key in cache._client.scan_iter(match="*", count=500)}


_ACCOUNT_STATE = {
    f"ai_rate:{_USER}:negotiate",
    f"ai_daily:{_USER}",
    f"quota:ai:{_USER}:2026-01-15",
    f"quota:assistant:{_USER}:2026-01-15",
    f"auth:blacklist:{_USER}",
    f"auth:replay_warn:{_USER}",
}


async def test_account_deletion_scan_erases_only_that_users_keys(redis_cache) -> None:
    from api.services.ai_privacy import clear_user_ai_data

    await _seed_user_keys(redis_cache, _USER)
    await _seed_user_keys(redis_cache, _OTHER)
    # Same digits with a longer id: a prefix match would wipe it too.
    await redis_cache._client.set(f"ai_task:u{_USER}0:t0", "{}")
    others_before = {key for key in await _all_keys(redis_cache) if str(_USER) not in key}

    await clear_user_ai_data(_USER, cache=redis_cache)

    remaining = await _all_keys(redis_cache)
    assert {key for key in remaining if f"{_USER}" in key} == {f"ai_task:u{_USER}0:t0"}
    assert others_before <= remaining


async def test_consent_revoke_scan_keeps_quota_and_auth_state(redis_cache) -> None:
    """RV-SEC-02 against real SCAN: the revoke path wipes AI data but must
    keep the usage counters and auth state, or revoke → grant would
    reset the daily AI quota."""
    from api.services.ai_privacy import clear_user_ai_data

    await _seed_user_keys(redis_cache, _USER)

    await clear_user_ai_data(_USER, cache=redis_cache, keep_account_state=True)

    remaining = await _all_keys(redis_cache)
    assert {key for key in remaining if str(_USER) in key} == _ACCOUNT_STATE


async def test_analysis_cache_eviction_escapes_glob_metacharacters(redis_cache) -> None:
    """The shared analysis cache is keyed by (ad_id, query); eviction
    builds a SCAN MATCH glob from the user's audit rows. Queries are
    free text, so ``*``, ``?``, ``[`` and ``]`` must match literally under
    Redis glob rules — MemoryCache's fnmatch is more forgiving and used
    to hide that a ``]`` made the pattern match nothing at all."""
    from api.database import get_engine, get_session_factory
    from api.models import AIAuditLog, User
    from api.services.ai_privacy import clear_user_ai_data

    engine = get_engine()
    session_factory = get_session_factory(engine)
    query = "iphone [pro] *max?"
    try:
        async with session_factory() as session:
            user = User(telegram_user_id=_USER, first_name="Glob")
            session.add(user)
            await session.flush()
            session.add(AIAuditLog(
                user_id=user.id,
                endpoint="analyze",
                ad_id="123",
                query=query,
                model="test-model",
                created_at=datetime.now(UTC),
            ))
            await session.commit()

        evicted = f"ai_analysis:v9:123:{query}:cat=None"
        # Matches the query read as a live glob ([pro] → "p", * → "x",
        # ? → "x"): must survive, it is another user's cached analysis.
        lookalike = "ai_analysis:v9:123:iphone p xmaxx:cat=None"
        await redis_cache.set_json(evicted, {"verdict": "cached"})
        await redis_cache.set_json(lookalike, {"verdict": "other user"})

        await clear_user_ai_data(
            _USER, cache=redis_cache, session_factory=session_factory, keep_account_state=True,
        )
    finally:
        await engine.dispose()

    assert await redis_cache.get(evicted) is None
    assert await redis_cache.get(lookalike) is not None


# ── Pipelined metrics ────────────────────────────────────────────────────


async def test_metrics_are_aggregated_in_redis_and_read_back_pipelined(redis_cache) -> None:
    from api.metrics import (
        observe_http_request_with_backend,
        observe_query_dataset_event_with_backend,
        render_prometheus_metrics_with_backend,
    )

    for _ in range(3):
        await observe_http_request_with_backend(
            redis_cache,
            method="get",
            path="/api/v1/ops04",
            status_code=200,
            duration_seconds=0.25,
        )
    await observe_query_dataset_event_with_backend(redis_cache, "ops04_event")

    counts = await redis_cache._client.hgetall("metrics:v1:http_requests")
    assert counts == {'["GET","/api/v1/ops04","200"]': "3"}

    lines = (await render_prometheus_metrics_with_backend(redis_cache)).splitlines()
    labels = 'method="GET",path="/api/v1/ops04"'
    assert 'kufar_metrics_backend_info{backend="redis"} 1' in lines
    assert f'kufar_http_requests_total{{{labels},status="200"}} 3' in lines
    assert f"kufar_http_request_duration_seconds_count{{{labels}}} 3" in lines
    assert 'kufar_query_dataset_events_total{event="ops04_event"} 1' in lines


# ── Wiring: the suite itself runs on Redis when it is up ─────────────────


def test_app_and_limiter_use_the_test_redis() -> None:
    """RV-OPS-04: with Redis reachable the app under test must get a
    RedisCache (not the MemoryCache fallback), the rate limiter must
    store its counters in the same test DB, and the autouse reset must
    clear both between tests."""
    from fastapi.testclient import TestClient

    from api import limiter as limiter_module
    from api.main import create_app
    from tests.conftest import require_test_redis, reset_test_redis_state

    require_test_redis()
    assert limiter_module.rate_limiter_degraded is False

    app = create_app()
    with TestClient(app) as client:
        assert isinstance(app.state.cache, RedisCache)
        # Rate-limited (60/minute) and cheap: unknown task id → 404.
        assert client.get("/api/v1/ai/task/ops04-missing").status_code == 404

    import redis

    sync_client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        assert any(sync_client.scan_iter(match="LIMITS:*"))
        sync_client.set("ops04:leftover", "1")
        reset_test_redis_state()
        assert sync_client.dbsize() == 0
    finally:
        sync_client.close()
