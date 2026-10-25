from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from api.middleware.telegram_auth import TelegramInitData
from api.services.account_status import QUOTA_BUCKET_AI, QUOTA_BUCKET_ASSISTANT, quota_key


class FakeAIChatService:
    available = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_json(self, *, system: str, content: str, max_tokens: int) -> dict:
        self.calls.append({"system": system, "content": content, "max_tokens": max_tokens})
        return {
            "opening_line": "Здравствуйте!",
            "counter_offer_text": "Могу предложить 900 BYN.",
            "fallback_text": "Давайте договоримся на 950 BYN.",
            "tips": ["Будьте вежливы"],
            "advice": "neutral",
            "reasoning": "Цена около медианы.",
            "market_context": "Текущий рыночный срез достаточен.",
            "confidence": 0.7,
        }


class FakeListingAIService:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def generate_listing(self, **kwargs) -> dict:
        del kwargs
        self.calls += 1
        return {
            "title_suggestion": "Test listing",
            "description": "Test description",
            "selling_points": ["one"],
            "pricing": {
                "fast": {"label": "Fast", "price_byn": 90, "weeks_to_sell": "1"},
                "market": {"label": "Market", "price_byn": 100, "weeks_to_sell": "2"},
                "patient": {"label": "Patient", "price_byn": 110, "weeks_to_sell": "3"},
                "floor_byn": 80,
            },
            "negotiation_playbook": [],
            "photo_tips": [],
            "competitors": [],
            "market_summary": "ok",
        }


def _put_json_cache(cache, key: str, payload: dict) -> None:
    cache._storage[key] = (json.dumps(payload, default=str), 0.0)


def _telegram_user(telegram_user_id: int):
    def _user() -> TelegramInitData:
        return TelegramInitData(user_id=telegram_user_id, first_name="Quota", raw={})

    return _user


def _seed_user_status(
    telegram_user_id: int,
    *,
    status_code: str,
    ai_limit: int,
    assistant_limit: int,
) -> None:
    from api.database import get_engine, get_session_factory
    from api.models import AccountStatus, User

    async def _seed() -> None:
        engine = get_engine()
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                status = await session.get(AccountStatus, status_code)
                if status is None:
                    status = AccountStatus(
                        code=status_code,
                        display_name=status_code,
                        tagline="test status",
                        accent="gray",
                        sort_order=9000,
                        ai_daily_limit=ai_limit,
                        assistant_daily_limit=assistant_limit,
                    )
                    session.add(status)
                else:
                    status.ai_daily_limit = ai_limit
                    status.assistant_daily_limit = assistant_limit

                user = (
                    await session.execute(
                        select(User).where(User.telegram_user_id == telegram_user_id)
                    )
                ).scalar_one_or_none()
                if user is None:
                    user = User(telegram_user_id=telegram_user_id, first_name="Quota")
                    session.add(user)
                user.account_status_code = status_code
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())


def _app_for_user(telegram_user_id: int):
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = _telegram_user(telegram_user_id)
    return app


def _patch_negotiate(monkeypatch, fake_ai: FakeAIChatService) -> None:
    from api.routers import ai_tools

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)


def _patch_price_advice(monkeypatch, fake_ai: FakeAIChatService) -> None:
    import api.services.query_pipeline as query_pipeline
    from api.routers import ai_tools

    async def _fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            price_stats=SimpleNamespace(
                median=1000.0, count=10, q1=900.0, q3=1100.0, min=800.0, max=1200.0,
            ),
        )

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(query_pipeline, "load_query_dataset", _fake_load_query_dataset)


def _patch_listing_assistant(monkeypatch, fake_ai: FakeListingAIService) -> None:
    from api.routers import ai_listing_assistant

    async def _fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            ads=[],
            price_stats=SimpleNamespace(median=0.0, count=0, q1=0.0, q3=0.0, min=0.0, max=0.0),
        )

    monkeypatch.setattr(ai_listing_assistant, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(ai_listing_assistant, "load_query_dataset", _fake_load_query_dataset)


def test_ai_endpoint_rejects_bare_search_with_premium_required(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730001
    _seed_user_status(
        telegram_user_id,
        status_code="bare_search",
        ai_limit=0,
        assistant_limit=0,
    )
    fake_ai = FakeAIChatService()
    _patch_negotiate(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        resp = client.post(
            "/api/v1/ai/negotiate",
            json={
                "ad_id": 1,
                "asking_price_byn": 1000,
                "my_offer_byn": 900,
                "query": "quota bare search",
            },
        )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "premium_required"
    assert resp.json()["detail"]["bucket"] == "ai"
    assert fake_ai.calls == []
    assert not [k for k in cache._storage if k.startswith("quota:")]


def test_ai_bucket_limit_one_allows_first_request_then_429(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730002
    _seed_user_status(
        telegram_user_id,
        status_code="quota_ai_one_730002",
        ai_limit=1,
        assistant_limit=1,
    )
    fake_ai = FakeAIChatService()
    _patch_negotiate(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        first = client.post(
            "/api/v1/ai/negotiate",
            json={
                "ad_id": 2,
                "asking_price_byn": 1000,
                "my_offer_byn": 900,
                "query": "quota first",
            },
        )
        second = client.post(
            "/api/v1/ai/negotiate",
            json={
                "ad_id": 3,
                "asking_price_byn": 1000,
                "my_offer_byn": 850,
                "query": "quota second",
            },
        )

    assert first.status_code == 200
    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["error"] == "quota_exceeded"
    assert detail["bucket"] == "ai"
    assert detail["limit"] == 1
    assert len(fake_ai.calls) == 1


def test_cached_negotiate_does_not_consume_second_quota(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730003
    _seed_user_status(
        telegram_user_id,
        status_code="quota_ai_one_730003",
        ai_limit=1,
        assistant_limit=1,
    )
    fake_ai = FakeAIChatService()
    _patch_negotiate(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)

    payload = {
        "ad_id": 4,
        "asking_price_byn": 1000,
        "my_offer_byn": 900,
        "query": "cached negotiate quota",
    }
    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        first = client.post("/api/v1/ai/negotiate", json=payload)
        second = client.post("/api/v1/ai/negotiate", json=payload)
        raw_used = cache._storage[quota_key(QUOTA_BUCKET_AI, telegram_user_id)][0]

    assert first.status_code == 200
    assert second.status_code == 200
    assert raw_used == "1"
    assert len(fake_ai.calls) == 1


def test_cached_price_advice_does_not_consume_second_quota(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730004
    _seed_user_status(
        telegram_user_id,
        status_code="quota_ai_one_730004",
        ai_limit=1,
        assistant_limit=1,
    )
    fake_ai = FakeAIChatService()
    _patch_price_advice(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)

    payload = {"query": "cached price quota", "current_price_byn": 950}
    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        first = client.post("/api/v1/ai/price-advice", json=payload)
        second = client.post("/api/v1/ai/price-advice", json=payload)
        raw_used = cache._storage[quota_key(QUOTA_BUCKET_AI, telegram_user_id)][0]

    assert first.status_code == 200
    assert second.status_code == 200
    assert raw_used == "1"
    assert len(fake_ai.calls) == 1


def test_cached_analyze_still_requires_ai_entitlement(monkeypatch) -> None:
    from api.routers import ai_analysis
    from api.services import ai_analysis_pipeline
    from api.services.cache import MemoryCache

    telegram_user_id = 730007
    _seed_user_status(
        telegram_user_id,
        status_code="bare_search",
        ai_limit=0,
        assistant_limit=0,
    )
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: SimpleNamespace(available=True))
    app = _app_for_user(telegram_user_id)
    cache_key = (
        f"ai_analysis:{ai_analysis_pipeline._AI_CACHE_VERSION}:77:"
        "cached analyze entitlement:cat=None"
    )

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        _put_json_cache(cache, cache_key, {"ad_id": 77, "summary": "cached"})
        resp = client.post(
            "/api/v1/ai/analyze",
            json={"ad_id": 77, "query": "cached analyze entitlement"},
        )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "premium_required"
    assert not [k for k in cache._storage if k.startswith("quota:")]


def test_cached_price_advice_still_requires_ai_entitlement(monkeypatch) -> None:
    from api.routers import ai_tools
    from api.schemas import AIPriceAdviceRequest, AIPriceAdviceResponse
    from api.services.cache import MemoryCache

    telegram_user_id = 730008
    _seed_user_status(
        telegram_user_id,
        status_code="bare_search",
        ai_limit=0,
        assistant_limit=0,
    )
    fake_ai = FakeAIChatService()
    _patch_price_advice(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)
    payload = {"query": "cached shared price advice gate", "current_price_byn": 950}
    cache_key = ai_tools._ai_price_advice_cache_key(AIPriceAdviceRequest(**payload))

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        _put_json_cache(
            cache,
            cache_key,
            AIPriceAdviceResponse(advice="neutral").model_dump(mode="json"),
        )
        resp = client.post("/api/v1/ai/price-advice", json=payload)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "premium_required"
    assert fake_ai.calls == []
    assert not [k for k in cache._storage if k.startswith("quota:")]


def test_cached_negotiate_still_requires_current_ai_entitlement(monkeypatch) -> None:
    from api.schemas import AINegotiateRequest, AINegotiateResponse
    from api.services.cache import MemoryCache, digest_cache_key

    telegram_user_id = 730009
    _seed_user_status(
        telegram_user_id,
        status_code="bare_search",
        ai_limit=0,
        assistant_limit=0,
    )
    fake_ai = FakeAIChatService()
    _patch_negotiate(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)
    payload = {
        "ad_id": 78,
        "asking_price_byn": 1000,
        "my_offer_byn": 900,
        "query": "cached negotiate entitlement",
    }
    request_model = AINegotiateRequest(**payload)
    cache_key = digest_cache_key(
        "ai_negotiate",
        {
            "u": telegram_user_id,
            "ad_id": request_model.ad_id,
            "my_offer_byn": request_model.my_offer_byn,
            "asking_price_byn": request_model.asking_price_byn,
            "query": request_model.query,
            "condition": request_model.condition,
        },
    )

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        _put_json_cache(
            cache,
            cache_key,
            AINegotiateResponse(counter_offer_text="cached").model_dump(mode="json"),
        )
        resp = client.post("/api/v1/ai/negotiate", json=payload)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "premium_required"
    assert fake_ai.calls == []
    assert not [k for k in cache._storage if k.startswith("quota:")]


def test_cached_listing_assistant_still_requires_current_entitlement(monkeypatch) -> None:
    from api.routers.ai_listing_assistant import _listing_assistant_cache_key
    from api.schemas import AIListingAssistantRequest, AIListingAssistantResponse
    from api.services.cache import MemoryCache

    telegram_user_id = 730010
    _seed_user_status(
        telegram_user_id,
        status_code="bare_search",
        ai_limit=0,
        assistant_limit=0,
    )
    fake_ai = FakeListingAIService()
    _patch_listing_assistant(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)
    payload = {"title": "cached listing entitlement", "draft_price_byn": 100}
    request_model = AIListingAssistantRequest(**payload)
    cache_key = _listing_assistant_cache_key(request_model, [], user_id=telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        _put_json_cache(
            cache,
            cache_key,
            AIListingAssistantResponse(title_suggestion="cached").model_dump(mode="json"),
        )
        resp = client.post("/api/v1/ai/listing-assistant", json=payload)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "premium_required"
    assert fake_ai.calls == 0
    assert not [k for k in cache._storage if k.startswith("quota:")]


def test_listing_assistant_uses_assistant_bucket(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730005
    _seed_user_status(
        telegram_user_id,
        status_code="quota_assistant_only_730005",
        ai_limit=0,
        assistant_limit=1,
    )
    fake_ai = FakeListingAIService()
    _patch_listing_assistant(monkeypatch, fake_ai)
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        resp = client.post(
            "/api/v1/ai/listing-assistant",
            json={"title": "assistant bucket listing", "draft_price_byn": 100},
        )

    assert resp.status_code == 200, resp.text
    assert cache._storage[quota_key(QUOTA_BUCKET_ASSISTANT, telegram_user_id)][0] == "1"
    assert quota_key(QUOTA_BUCKET_AI, telegram_user_id) not in cache._storage
    assert fake_ai.calls == 1


def test_ai_and_assistant_quota_buckets_are_independent(monkeypatch) -> None:
    """RV-AI-07: the rejected second AI call no longer stays on the
    counter (it used to read "2" with limit 1); otherwise a later refund
    would only cancel the overshoot instead of returning a usable unit."""
    from api.services.cache import MemoryCache

    telegram_user_id = 730006
    _seed_user_status(
        telegram_user_id,
        status_code="quota_split_one_730006",
        ai_limit=1,
        assistant_limit=1,
    )
    chat_ai = FakeAIChatService()
    listing_ai = FakeListingAIService()
    _patch_negotiate(monkeypatch, chat_ai)
    _patch_listing_assistant(monkeypatch, listing_ai)
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        first_ai = client.post(
            "/api/v1/ai/negotiate",
            json={
                "ad_id": 5,
                "asking_price_byn": 1000,
                "my_offer_byn": 900,
                "query": "independent ai first",
            },
        )
        assistant = client.post(
            "/api/v1/ai/listing-assistant",
            json={"title": "independent assistant", "draft_price_byn": 100},
        )
        second_ai = client.post(
            "/api/v1/ai/negotiate",
            json={
                "ad_id": 6,
                "asking_price_byn": 1000,
                "my_offer_byn": 850,
                "query": "independent ai second",
            },
        )

    assert first_ai.status_code == 200
    assert assistant.status_code == 200, assistant.text
    assert second_ai.status_code == 429
    assert second_ai.json()["detail"]["bucket"] == "ai"
    assert cache._storage[quota_key(QUOTA_BUCKET_AI, telegram_user_id)][0] == "1"
    assert cache._storage[quota_key(QUOTA_BUCKET_ASSISTANT, telegram_user_id)][0] == "1"


# ── RV2-AI-12 / RV2-AI2-08: refunds when nothing is delivered ──────────


class FailingChatService(FakeAIChatService):
    async def chat_json(self, *, system: str, content: str, max_tokens: int) -> dict:
        await super().chat_json(system=system, content=content, max_tokens=max_tokens)
        raise RuntimeError("AI circuit breaker open")


class FailingListingAIService(FakeListingAIService):
    async def generate_listing(self, **kwargs) -> dict:
        await super().generate_listing(**kwargs)
        raise RuntimeError("AI circuit breaker open")


def _negotiate_body(query: str) -> dict:
    return {"ad_id": 7, "asking_price_byn": 1000, "my_offer_byn": 900, "query": query}


def test_negotiate_provider_failure_is_503_and_refunded(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730011
    _seed_user_status(
        telegram_user_id, status_code="quota_refund_730011", ai_limit=1, assistant_limit=1,
    )
    _patch_negotiate(monkeypatch, FailingChatService())
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        failed = client.post("/api/v1/ai/negotiate", json=_negotiate_body("refund failed"))
        _patch_negotiate(monkeypatch, FakeAIChatService())
        retried = client.post("/api/v1/ai/negotiate", json=_negotiate_body("refund retry"))

    assert failed.status_code == 503
    assert retried.status_code == 200
    assert cache._storage[quota_key(QUOTA_BUCKET_AI, telegram_user_id)][0] == "1"


def test_price_advice_market_data_error_is_refunded(monkeypatch) -> None:
    import api.services.query_pipeline as query_pipeline
    from api.services.cache import MemoryCache

    telegram_user_id = 730012
    _seed_user_status(
        telegram_user_id, status_code="quota_refund_730012", ai_limit=1, assistant_limit=1,
    )
    fake_ai = FakeAIChatService()
    _patch_price_advice(monkeypatch, fake_ai)

    async def _broken_dataset(**kwargs):
        del kwargs
        raise RuntimeError("search backend exploded")

    monkeypatch.setattr(query_pipeline, "load_query_dataset", _broken_dataset)
    app = _app_for_user(telegram_user_id)

    with TestClient(app, raise_server_exceptions=False) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        resp = client.post(
            "/api/v1/ai/price-advice",
            json={"query": "refund price advice", "current_price_byn": 950},
        )

    assert resp.status_code == 500
    assert fake_ai.calls == []
    assert cache._storage[quota_key(QUOTA_BUCKET_AI, telegram_user_id)][0] == "0"


def test_listing_assistant_fallback_is_marked_and_charged_like_analyze(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730013
    _seed_user_status(
        telegram_user_id, status_code="quota_refund_730013", ai_limit=0, assistant_limit=1,
    )
    _patch_listing_assistant(monkeypatch, FailingListingAIService())
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        resp = client.post(
            "/api/v1/ai/listing-assistant",
            json={"title": "fallback listing", "draft_price_byn": 100},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "fallback"
    # A delivered market draft stays charged, as on /analyze (owner question 2).
    assert cache._storage[quota_key(QUOTA_BUCKET_ASSISTANT, telegram_user_id)][0] == "1"


def test_listing_assistant_error_is_refunded(monkeypatch) -> None:
    from api.routers import ai_listing_assistant
    from api.services.cache import MemoryCache

    telegram_user_id = 730014
    _seed_user_status(
        telegram_user_id, status_code="quota_refund_730014", ai_limit=0, assistant_limit=1,
    )
    fake_ai = FakeListingAIService()
    _patch_listing_assistant(monkeypatch, fake_ai)

    async def _broken_dataset(**kwargs):
        del kwargs
        raise ValueError("unexpected search failure")

    monkeypatch.setattr(ai_listing_assistant, "load_query_dataset", _broken_dataset)
    app = _app_for_user(telegram_user_id)

    with TestClient(app, raise_server_exceptions=False) as client:
        cache = MemoryCache()
        client.app.state.cache = cache
        resp = client.post(
            "/api/v1/ai/listing-assistant",
            json={"title": "broken listing", "draft_price_byn": 100},
        )

    assert resp.status_code == 500
    assert fake_ai.calls == 0
    assert cache._storage[quota_key(QUOTA_BUCKET_ASSISTANT, telegram_user_id)][0] == "0"


def test_listing_assistant_ai_answer_is_marked_ai(monkeypatch) -> None:
    from api.services.cache import MemoryCache

    telegram_user_id = 730015
    _seed_user_status(
        telegram_user_id, status_code="quota_refund_730015", ai_limit=0, assistant_limit=1,
    )
    _patch_listing_assistant(monkeypatch, FakeListingAIService())
    app = _app_for_user(telegram_user_id)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        resp = client.post(
            "/api/v1/ai/listing-assistant",
            json={"title": "ai listing source", "draft_price_byn": 100},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "ai"


# ── RV2-SC-02: quota lookup must not resurrect an erased user ─────────


def test_quota_lookup_does_not_recreate_erased_user() -> None:
    import time

    import pytest
    from fastapi import HTTPException

    from api.database import get_engine, get_session_factory
    from api.models import User
    from api.services.account_erasure import mark_account_deleted, remember_request_principal
    from api.services.ai_guards import _status_quota_limit
    from api.services.cache import MemoryCache

    telegram_user_id = 730016
    now = int(time.time())
    cache = MemoryCache()
    stale_session = TelegramInitData(
        user_id=telegram_user_id, first_name="Quota", raw={"auth_date": str(now - 60)},
    )

    async def _scenario() -> list[User]:
        engine = get_engine()
        try:
            session_factory = get_session_factory(engine)
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(session_factory=session_factory)),
            )
            await mark_account_deleted(cache, telegram_user_id, deleted_at=now, ttl_seconds=60)

            async def _lookup() -> int:
                remember_request_principal(stale_session, cache)
                return await _status_quota_limit(request, telegram_user_id, bucket=QUOTA_BUCKET_AI)

            with pytest.raises(HTTPException) as exc:
                await asyncio.create_task(_lookup())
            assert exc.value.status_code == 410
            async with session_factory() as session:
                return list((await session.execute(
                    select(User).where(User.telegram_user_id == telegram_user_id)
                )).scalars())
        finally:
            await engine.dispose()

    assert asyncio.run(_scenario()) == []
