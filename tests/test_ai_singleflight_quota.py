"""RV-AI-07: one analysis per (user, listing) in flight, and no charge for nothing.

The frontend stops polling after ~90 s while the pipeline may run up to
the 240 s watchdog, so "Повторить" used to start a duplicate task and
spend a second quota unit. These tests pin down:

* a repeat while the task runs returns the same task_id, uncharged —
  also when the repeat lands on another worker (shared cache);
* the unit is refunded on 503, "listing not found", a fallback that
  couldn't be built and a cancelled task;
* the cache/quota primitives the refund relies on.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from api.middleware.telegram_auth import TelegramInitData
from api.schemas import AIAnalysisRequest
from api.services import ai_analysis_pipeline as pipeline
from api.services import ai_task_store
from api.services.account_status import (
    QUOTA_BUCKET_AI,
    consume_quota,
    quota_key,
    refund_quota,
)
from api.services.ai_guards import QuotaCharge
from api.services.ai_task_store import (
    _claim_inflight,
    _get_task,
    _inflight_key,
    _release_inflight,
    _running_inflight_task,
    _set_task,
)
from api.services.cache import MemoryCache, RedisCache

PAYLOAD = {"ad_id": 1, "query": "iphone 14 singleflight"}


# ── Fakes ─────────────────────────────────────────────────────────────


class SharedRedisLikeCache:
    """Thread-safe stand-in for Redis shared by several app instances.

    Two TestClients run their event loops in different threads, the way
    two uvicorn workers share one Redis; only a threading.Lock is used
    so the fake is safe across those loops. ``try_acquire_lock`` /
    ``release_lock`` mirror RedisCache (SET NX EX / compare-and-delete).
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._mutex = threading.Lock()

    def _live(self, key: str) -> tuple[str, float] | None:
        entry = self._data.get(key)
        if entry is not None and entry[1] and entry[1] <= time.monotonic():
            del self._data[key]
            return None
        return entry

    def raw(self, key: str) -> str | None:
        with self._mutex:
            entry = self._live(key)
            return entry[0] if entry else None

    def keys(self, prefix: str) -> list[str]:
        with self._mutex:
            return [key for key in self._data if key.startswith(prefix)]

    async def get(self, key: str) -> str | None:
        return self.raw(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        with self._mutex:
            self._data[key] = (value, time.monotonic() + ttl if ttl else 0.0)

    async def get_json(self, key: str):
        raw = await self.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value, ttl: int | None = None) -> None:
        await self.set(key, json.dumps(value, default=str), ttl)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        with self._mutex:
            entry = self._live(key)
            count = int(entry[0]) + 1 if entry else 1
            expires = entry[1] if entry else (time.monotonic() + ttl if ttl else 0.0)
            self._data[key] = (str(count), expires)
            return count

    async def decr(self, key: str) -> int:
        with self._mutex:
            entry = self._live(key)
            if entry is None or int(entry[0]) <= 0:
                return 0
            count = int(entry[0]) - 1
            self._data[key] = (str(count), entry[1])
            return count

    async def delete(self, key: str) -> None:
        with self._mutex:
            self._data.pop(key, None)

    async def try_acquire_lock(self, key: str, token: str, *, ttl_seconds: int) -> bool:
        with self._mutex:
            if self._live(key) is not None:
                return False
            self._data[key] = (token, time.monotonic() + ttl_seconds)
            return True

    async def release_lock(self, key: str, token: str) -> None:
        with self._mutex:
            entry = self._live(key)
            if entry is not None and entry[0] == token:
                del self._data[key]

    async def ping(self) -> bool:
        return True

    async def pipeline_get(self, keys: list[str]) -> list[str | None]:
        return [self.raw(key) for key in keys]

    async def pipeline_hgetall(self, keys: list[str]) -> list[dict[str, str]]:
        return [{} for _ in keys]


def _model_answer() -> dict:
    return {
        "fair_price": {"from": 1500, "to": 1650, "reasoning": "Аналог №1 стоит 1580 BYN."},
        "condition": {"label": "Хорошее", "confidence": 0.8, "notes": ["Корпус без сколов"]},
        "recommendation": {
            "verdict": "think_twice",
            "text": "Торгуйся до 1500 BYN и проверь аккумулятор на месте.",
        },
        "summary": "Цена около рынка; перед покупкой проверь аккумулятор и Face ID.",
    }


class GatedAI:
    """Holds the analysis open until the test releases it."""

    available = True
    analysis_model = "test-model"

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0

    async def analyze_listing_parallel(self, **kwargs) -> dict:
        del kwargs
        self.calls += 1
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return _model_answer()

    async def quick_condition(self, image_urls: list[str]) -> dict:
        del image_urls
        return {"condition": "", "notes": []}


def _ad(ad_id: int, subject: str, price_byn: float) -> dict:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "body": "Обычное состояние, торг уместен.",
        "price_byn": int(price_byn * 100),
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "images": [{"path": f"adim1/{ad_id}.jpg"}],
        "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
    }


def _dataset(*, with_target: bool = True) -> SimpleNamespace:
    ads = [_ad(2, "iPhone 14 128GB", 1580), _ad(3, "iPhone 14 128 ГБ", 1620)]
    if with_target:
        ads.insert(0, _ad(1, "iPhone 14", 1590))
    return SimpleNamespace(
        ads=ads,
        price_stats=SimpleNamespace(
            median=1590.0, count=24, q1=1550.0, q3=1630.0, min=1500.0, max=1700.0,
        ),
    )


def _patch_pipeline(monkeypatch, ai, *, with_target: bool = True) -> None:
    from api.routers import ai_analysis

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return _dataset(with_target=with_target)

    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: ai)
    monkeypatch.setattr(pipeline, "get_ai_service", lambda: ai)
    monkeypatch.setattr(pipeline, "load_query_dataset", fake_load_query_dataset)


def _grant_status(telegram_user_id: int, status_code: str = "scout") -> None:
    from sqlalchemy import select

    from api.database import get_engine, get_session_factory
    from api.models import User

    async def _grant() -> None:
        engine = get_engine()
        try:
            async with get_session_factory(engine)() as session:
                user = (
                    await session.execute(
                        select(User).where(User.telegram_user_id == telegram_user_id)
                    )
                ).scalar_one_or_none()
                if user is None:
                    user = User(telegram_user_id=telegram_user_id, first_name="Flight")
                    session.add(user)
                user.account_status_code = status_code
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_grant())


def _app_for_user(telegram_user_id: int):
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = lambda: TelegramInitData(
        user_id=telegram_user_id, first_name="Flight", raw={},
    )
    return app


def _wait_for_task(client: TestClient, task_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/ai/task/{task_id}").json()
        if payload["status"] in {"done", "error"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("AI task did not finish in time")


def _claims_left_after_release(keys, timeout_s: float = 5.0) -> list[str]:
    """RV-AI-07: the claim is released in ``_run_analysis``'s ``finally``, a
    beat after the task status flips to done, so a poll can see "done"
    first (it did under full-suite load). Give the background task that
    beat instead of asserting on the very next line."""
    deadline = time.monotonic() + timeout_s
    while True:
        left = keys()
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.02)


def _quota_used(cache, telegram_user_id: int) -> str | None:
    key = quota_key(QUOTA_BUCKET_AI, telegram_user_id)
    if isinstance(cache, MemoryCache):
        entry = cache._storage.get(key)
        return entry[0] if entry else None
    return cache.raw(key)


# ── Endpoint behaviour ────────────────────────────────────────────────


def test_repeat_while_running_returns_same_task_without_second_charge(monkeypatch) -> None:
    telegram_user_id = 760001
    ai = GatedAI()
    _patch_pipeline(monkeypatch, ai)
    _grant_status(telegram_user_id)
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        first = client.post("/api/v1/ai/analyze", json=PAYLOAD)
        assert first.status_code == 200, first.text
        task_id = first.json()["task_id"]
        assert ai.started.wait(5)

        repeat = client.post("/api/v1/ai/analyze", json=PAYLOAD)
        assert repeat.status_code == 200, repeat.text
        assert repeat.json() == {"task_id": task_id, "deduplicated": True}
        assert _quota_used(cache, telegram_user_id) == "1"

        ai.release.set()
        status = _wait_for_task(client, task_id)
        assert status["status"] == "done"
        assert status["result"]["source"] == "ai"
        # The claim goes with the task; the next ask is a free cache hit.
        assert not _claims_left_after_release(
            lambda: [key for key in cache._storage if key.startswith("ai_inflight:")]
        )
        after = client.post("/api/v1/ai/analyze", json=PAYLOAD)
        assert after.json()["cached"] is True

    assert ai.calls == 1
    assert _quota_used(cache, telegram_user_id) == "1"


def test_repeat_on_another_worker_returns_same_task(monkeypatch) -> None:
    telegram_user_id = 760002
    ai = GatedAI()
    _patch_pipeline(monkeypatch, ai)
    _grant_status(telegram_user_id)
    shared = SharedRedisLikeCache()

    with (
        TestClient(_app_for_user(telegram_user_id)) as worker_one,
        TestClient(_app_for_user(telegram_user_id)) as worker_two,
    ):
        worker_one.app.state.cache = shared
        worker_two.app.state.cache = shared
        first = worker_one.post("/api/v1/ai/analyze", json=PAYLOAD)
        assert first.status_code == 200, first.text
        task_id = first.json()["task_id"]
        assert ai.started.wait(5)

        repeat = worker_two.post("/api/v1/ai/analyze", json=PAYLOAD)
        assert repeat.status_code == 200, repeat.text
        assert repeat.json() == {"task_id": task_id, "deduplicated": True}
        assert _quota_used(shared, telegram_user_id) == "1"

        ai.release.set()
        # Polled through the worker that did not run the task.
        assert _wait_for_task(worker_two, task_id)["status"] == "done"
        assert not _claims_left_after_release(lambda: shared.keys("ai_inflight:"))

    assert ai.calls == 1


def test_listing_not_found_refunds_quota(monkeypatch) -> None:
    telegram_user_id = 760003
    ai = GatedAI()
    ai.release.set()
    _patch_pipeline(monkeypatch, ai, with_target=False)
    _grant_status(telegram_user_id)

    with TestClient(_app_for_user(telegram_user_id)) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        start = client.post("/api/v1/ai/analyze", json=PAYLOAD)
        assert start.status_code == 200, start.text
        status = _wait_for_task(client, start.json()["task_id"])

    assert status["status"] == "error"
    assert status["error"] == "Объявление не найдено"
    assert _quota_used(cache, telegram_user_id) == "0"
    assert ai.calls == 0
    assert not _claims_left_after_release(
        lambda: [key for key in cache._storage if key.startswith("ai_inflight:")]
    )


def test_spawn_refusal_503_refunds_quota_and_frees_claim(monkeypatch) -> None:
    from api.routers import ai_analysis

    telegram_user_id = 760004
    ai = GatedAI()
    _patch_pipeline(monkeypatch, ai)
    _grant_status(telegram_user_id)

    def refuse(coro, *, name=None):
        del coro, name
        raise RuntimeError("Too many background tasks")

    monkeypatch.setattr(ai_analysis, "_spawn_bg_task", refuse)

    with TestClient(_app_for_user(telegram_user_id)) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        resp = client.post("/api/v1/ai/analyze", json=PAYLOAD)

    assert resp.status_code == 503
    assert _quota_used(cache, telegram_user_id) == "0"
    assert not _claims_left_after_release(
        lambda: [key for key in cache._storage if key.startswith("ai_inflight:")]
    )
    tasks = [json.loads(value) for key, (value, _) in cache._storage.items()
             if key.startswith(f"ai_task:u{telegram_user_id}:")]
    assert [task["status"] for task in tasks] == ["error"]


def test_quota_rejection_releases_claim(monkeypatch) -> None:
    """A 429 must not leave a claim that dedupes the next try onto a dead task."""
    telegram_user_id = 760005
    ai = GatedAI()
    _patch_pipeline(monkeypatch, ai)
    _grant_status(telegram_user_id)

    with TestClient(_app_for_user(telegram_user_id)) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        cache._storage[quota_key(QUOTA_BUCKET_AI, telegram_user_id)] = ("10", 0.0)
        resp = client.post("/api/v1/ai/analyze", json=PAYLOAD)

    assert resp.status_code == 429
    assert not _claims_left_after_release(
        lambda: [key for key in cache._storage if key.startswith("ai_inflight:")]
    )
    assert _quota_used(cache, telegram_user_id) == "10"
    assert ai.calls == 0


# ── Pipeline: refunds on cancel / unbuildable fallback ────────────────


class HangingAI:
    available = True

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def analyze_listing_parallel(self, **kwargs) -> dict:
        del kwargs
        self.started.set()
        await asyncio.sleep(3600)
        return {}

    async def quick_condition(self, image_urls: list[str]) -> dict:
        del image_urls
        return {}


async def _charged_task(cache, telegram_user_id: int, task_id: str) -> tuple[QuotaCharge, str]:
    charged_at = datetime.now(UTC)
    await consume_quota(
        cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=telegram_user_id, limit=5,
        now=charged_at,
    )
    key = _inflight_key(telegram_user_id, f"analysis-{task_id}")
    await _set_task(cache, task_id, {
        "status": "pending", "progress": 0, "result": None, "error": None,
        "_telegram_user_id": telegram_user_id,
    })
    assert await _claim_inflight(cache, key, task_id, user_id=telegram_user_id) is None
    charge = QuotaCharge(
        bucket=QUOTA_BUCKET_AI, telegram_user_id=telegram_user_id, charged_at=charged_at,
    )
    return charge, key


async def test_watchdog_cancelled_task_is_closed_refunded_and_unclaimed(monkeypatch) -> None:
    telegram_user_id = 760006
    cache = MemoryCache()
    ai = HangingAI()

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return _dataset()

    monkeypatch.setattr(pipeline, "get_ai_service", lambda: ai)
    monkeypatch.setattr(pipeline, "load_query_dataset", fake_load_query_dataset)
    monkeypatch.setattr(ai_task_store, "_BG_TASK_TIMEOUT", 0.3)
    charge, key = await _charged_task(cache, telegram_user_id, "cancelled-task")

    with pytest.raises(TimeoutError):
        await ai_task_store._bg_task_watchdog(
            pipeline._run_analysis(
                "cancelled-task",
                AIAnalysisRequest(**PAYLOAD),
                SimpleNamespace(ai_photo_precheck_enabled=False),
                cache,
                None,
                user_id=telegram_user_id,
                quota_charge=charge,
                inflight_key=key,
            )
        )

    assert ai.started.is_set()
    task = await _get_task(cache, "cancelled-task", user_id=telegram_user_id)
    assert task["status"] == "error"
    assert "не засчитан" in task["error"]
    assert _quota_used(cache, telegram_user_id) == "0"
    assert await cache.get(key) is None


async def test_unbuildable_fallback_refunds_quota(monkeypatch) -> None:
    telegram_user_id = 760007
    cache = MemoryCache()

    class FailingAI:
        available = True

        async def analyze_listing_parallel(self, **kwargs):
            del kwargs
            raise RuntimeError("provider down")

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return _dataset()

    def broken_fallback(**kwargs):
        del kwargs
        raise ValueError("fallback exploded")

    monkeypatch.setattr(pipeline, "get_ai_service", lambda: FailingAI())
    monkeypatch.setattr(pipeline, "load_query_dataset", fake_load_query_dataset)
    monkeypatch.setattr(pipeline, "_build_fallback_response", broken_fallback)
    charge, key = await _charged_task(cache, telegram_user_id, "no-fallback-task")

    await pipeline._run_analysis(
        "no-fallback-task",
        AIAnalysisRequest(**PAYLOAD),
        SimpleNamespace(ai_photo_precheck_enabled=False),
        cache,
        None,
        user_id=telegram_user_id,
        quota_charge=charge,
        inflight_key=key,
    )

    task = await _get_task(cache, "no-fallback-task", user_id=telegram_user_id)
    assert task["status"] == "error"
    assert _quota_used(cache, telegram_user_id) == "0"
    assert await cache.get(key) is None


async def test_delivered_fallback_keeps_the_charge(monkeypatch) -> None:
    """The user did get a (labelled) market draft — only empty-handed ends refund."""
    telegram_user_id = 760008
    cache = MemoryCache()

    class FailingAI:
        available = True

        async def analyze_listing_parallel(self, **kwargs):
            del kwargs
            raise RuntimeError("provider down")

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return _dataset()

    monkeypatch.setattr(pipeline, "get_ai_service", lambda: FailingAI())
    monkeypatch.setattr(pipeline, "load_query_dataset", fake_load_query_dataset)
    charge, key = await _charged_task(cache, telegram_user_id, "fallback-task")

    await pipeline._run_analysis(
        "fallback-task",
        AIAnalysisRequest(**PAYLOAD),
        SimpleNamespace(ai_photo_precheck_enabled=False),
        cache,
        None,
        user_id=telegram_user_id,
        quota_charge=charge,
        inflight_key=key,
    )

    task = await _get_task(cache, "fallback-task", user_id=telegram_user_id)
    assert task["status"] == "done"
    assert task["result"]["source"] == "fallback"
    assert _quota_used(cache, telegram_user_id) == "1"
    assert await cache.get(key) is None


# ── Singleflight claim primitives ─────────────────────────────────────


@pytest.mark.parametrize("cache_factory", [MemoryCache, SharedRedisLikeCache])
async def test_claim_returns_live_owner_and_takes_over_stale_claims(cache_factory) -> None:
    cache = cache_factory()
    user_id = 760100
    key = _inflight_key(user_id, "ai_analysis:v:1:q:cat=None")

    await _set_task(cache, "owner", {"status": "processing", "_telegram_user_id": user_id})
    assert await _claim_inflight(cache, key, "owner", user_id=user_id) is None
    assert await _claim_inflight(cache, key, "second", user_id=user_id) == "owner"
    assert await _running_inflight_task(cache, key, user_id=user_id) == "owner"

    # Release is compare-and-delete: only the owner can drop the claim.
    await _release_inflight(cache, key, "second")
    assert await cache.get(key) == "owner"

    # A finished owner leaves a stale claim; the next request takes over.
    await _set_task(cache, "owner", {"status": "done", "_telegram_user_id": user_id})
    assert await _running_inflight_task(cache, key, user_id=user_id) is None
    assert await _claim_inflight(cache, key, "third", user_id=user_id) is None
    assert await cache.get(key) == "third"

    await _release_inflight(cache, key, "third")
    assert await cache.get(key) is None


async def test_claim_without_a_task_record_is_not_live() -> None:
    cache = MemoryCache()
    key = _inflight_key(1, "analysis")
    await cache.set(key, "ghost")
    assert await _running_inflight_task(cache, key, user_id=1) is None
    assert await _claim_inflight(cache, key, "fresh", user_id=1) is None
    assert await cache.get(key) == "fresh"


async def test_claim_degrades_to_unshared_when_backend_cannot_tell() -> None:
    class BrokenLockCache(SharedRedisLikeCache):
        async def try_acquire_lock(self, key, token, *, ttl_seconds):
            del key, token, ttl_seconds
            return None  # RedisCache returns None on RedisError

    cache = BrokenLockCache()
    assert await _claim_inflight(cache, "k", "task", user_id=1) is None


def test_inflight_key_is_scoped_per_user_and_analysis() -> None:
    assert _inflight_key(1, "a") != _inflight_key(2, "a")
    assert _inflight_key(1, "a") != _inflight_key(1, "b")
    assert _inflight_key(7, "a").startswith("ai_inflight:u7:")


# ── Quota and cache primitives ────────────────────────────────────────


async def test_memory_cache_decr_never_creates_or_goes_negative() -> None:
    cache = MemoryCache()
    assert await cache.decr("missing") == 0
    assert "missing" not in cache._storage

    await cache.incr("counter", ttl=60)
    await cache.incr("counter", ttl=60)
    expires_at = cache._storage["counter"][1]
    assert await cache.decr("counter") == 1
    assert cache._storage["counter"] == ("1", expires_at)
    assert await cache.decr("counter") == 0
    assert await cache.decr("counter") == 0
    assert cache._storage["counter"][0] == "0"

    cache._storage["stale"] = ("3", time.monotonic() - 1)
    assert await cache.decr("stale") == 0
    assert "stale" not in cache._storage


async def test_redis_cache_decr_uses_floor_script_and_swallows_errors() -> None:
    calls: list[tuple] = []

    class FakeClient:
        async def eval(self, script, numkeys, *args):
            calls.append((script, numkeys, *args))
            return 4

    cache = RedisCache(FakeClient())
    assert await cache.decr("quota:ai:1:2026-10-21") == 4
    assert calls == [(RedisCache._DECR_FLOOR_LUA, 1, "quota:ai:1:2026-10-21")]

    class BrokenClient:
        async def eval(self, *args):
            del args
            raise RedisError("down")

    assert await RedisCache(BrokenClient()).decr("quota:ai:1:2026-10-21") == 0


async def test_redis_decr_floor_script_against_real_redis(redis_cache) -> None:
    # RV-OPS-04: the conftest fixture follows TEST_REDIS_URL (6379 in CI)
    # and flushes the DB before each test; a hard-coded :6380 never ran in CI.
    cache = redis_cache
    assert await cache.decr("rv-ai-07:decr") == 0
    assert await cache.get("rv-ai-07:decr") is None
    await cache.incr("rv-ai-07:decr", ttl=60)
    assert await cache.decr("rv-ai-07:decr") == 0
    assert await cache.decr("rv-ai-07:decr") == 0
    assert await cache.get("rv-ai-07:decr") == "0"
    assert 0 < await cache._client.ttl("rv-ai-07:decr") <= 60


async def test_rejected_attempt_does_not_stay_on_the_counter() -> None:
    from fastapi import HTTPException

    cache = MemoryCache()
    now = datetime.fromisoformat("2026-05-18T12:00:00+03:00")
    await consume_quota(cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=9, limit=1, now=now)
    for _ in range(3):
        with pytest.raises(HTTPException) as exc:
            await consume_quota(
                cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=9, limit=1, now=now,
            )
        assert exc.value.status_code == 429
    key = quota_key(QUOTA_BUCKET_AI, 9, now=now)
    assert cache._storage[key][0] == "1"

    # So one refund gives back a usable unit.
    await refund_quota(cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=9, now=now)
    snapshot = await consume_quota(
        cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=9, limit=1, now=now,
    )
    assert snapshot.used == 1


async def test_refund_goes_to_the_day_the_unit_was_charged() -> None:
    cache = MemoryCache()
    before_midnight = datetime.fromisoformat("2026-05-18T23:59:30+03:00")
    after_midnight = before_midnight + timedelta(minutes=2)
    await consume_quota(
        cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=10, limit=3, now=before_midnight,
    )
    await consume_quota(
        cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=10, limit=3, now=after_midnight,
    )

    await refund_quota(
        cache, bucket=QUOTA_BUCKET_AI, telegram_user_id=10, now=before_midnight,
    )

    assert cache._storage[quota_key(QUOTA_BUCKET_AI, 10, now=before_midnight)][0] == "0"
    assert cache._storage[quota_key(QUOTA_BUCKET_AI, 10, now=after_midnight)][0] == "1"
