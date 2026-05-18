from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


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
            "tips": ["Упомяните аналоги", "Будьте вежливы"],
            "advice": "buy_now",
            "reasoning": "Цена ниже медианы на 15%.",
            "price_trend": "stable",
            "historical_context": "Цены стабильны последний месяц.",
            "confidence": 0.82,
        }


async def _noop_async(*a, **kw) -> None:
    pass


def test_negotiate_endpoint_returns_response(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis, ai_tools

    fake_ai = FakeAIChatService()

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(ai_tools, "_check_ai_consent", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_ai_entitlement", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_rate_limit", _noop_async)
    monkeypatch.setattr(ai_tools, "_log_ai_audit", _noop_async)
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ai/negotiate",
            json={
                "ad_id": 42,
                "asking_price_byn": 1000,
                "my_offer_byn": 800,
                "query": "iphone 14",
                "condition": "Б/у",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["opening_line"]
        assert body["counter_offer_text"]
        assert "disclaimer" in body


def test_negotiate_cache_hit_does_not_consume_quota(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis, ai_tools

    fake_ai = FakeAIChatService()
    rate_calls: list[str] = []
    audit_calls: list[dict] = []

    async def _fake_rate_limit(request, user_id, *, endpoint="default"):
        del request, user_id
        rate_calls.append(endpoint)

    async def _fake_audit(*args, **kwargs):
        del args
        audit_calls.append(kwargs)

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(ai_tools, "_check_ai_consent", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_ai_entitlement", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_rate_limit", _fake_rate_limit)
    monkeypatch.setattr(ai_tools, "_log_ai_audit", _fake_audit)
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    payload = {
        "ad_id": 970001,
        "asking_price_byn": 1000,
        "my_offer_byn": 800,
        "query": "iphone 14 wave97",
        "condition": "Б/у",
    }

    with TestClient(app) as client:
        first = client.post("/api/v1/ai/negotiate", json=payload)
        second = client.post("/api/v1/ai/negotiate", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert rate_calls == ["negotiate"]
    assert len(fake_ai.calls) == 1
    assert [call.get("cached") for call in audit_calls] == [None, True]


def test_negotiate_cache_does_not_cross_users(monkeypatch) -> None:
    """PR-03: two distinct users supplying the same negotiate payload
    must NOT share a cached AI response. The previous cache key
    omitted user_id and the reply text (which echoes the buyer's
    offer + condition + market_context) was being served from the
    first caller's cache entry.
    """
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis, ai_tools

    fake_ai = FakeAIChatService()
    rate_calls: list[str] = []
    audit_calls: list[dict] = []

    async def _fake_rate_limit(request, user_id, *, endpoint="default"):
        del request, user_id
        rate_calls.append(endpoint)

    async def _fake_audit(*args, **kwargs):
        del args
        audit_calls.append(kwargs)

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(ai_tools, "_check_ai_consent", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_ai_entitlement", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_rate_limit", _fake_rate_limit)
    monkeypatch.setattr(ai_tools, "_log_ai_audit", _fake_audit)
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)

    app = create_app()
    payload = {
        "ad_id": 970099,
        "asking_price_byn": 1000,
        "my_offer_byn": 800,
        "query": "iphone 14 wave134",
        "condition": "Б/у",
    }

    def _user_a() -> TelegramInitData:
        return TelegramInitData(user_id=111111, first_name="A", raw={})

    def _user_b() -> TelegramInitData:
        return TelegramInitData(user_id=222222, first_name="B", raw={})

    with TestClient(app) as client:
        app.dependency_overrides[get_telegram_user] = _user_a
        first = client.post("/api/v1/ai/negotiate", json=payload)
        app.dependency_overrides[get_telegram_user] = _user_b
        second = client.post("/api/v1/ai/negotiate", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    # Each user pays one quota point — no cross-user cache hit.
    assert rate_calls == ["negotiate", "negotiate"]
    # AI was hit twice — once per user.
    assert len(fake_ai.calls) == 2
    # Both audit rows are "fresh" (cached=None), not "cached=True".
    assert [call.get("cached") for call in audit_calls] == [None, None]


def test_price_advice_endpoint_returns_response(monkeypatch) -> None:
    import api.services.query_pipeline as _qp_mod
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis, ai_tools

    fake_ai = FakeAIChatService()

    async def _fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            price_stats=SimpleNamespace(
                median=1000.0, count=10, q1=900.0, q3=1100.0, min=800.0, max=1200.0,
            ),
        )

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(ai_tools, "_check_ai_consent", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_ai_entitlement", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_rate_limit", _noop_async)
    monkeypatch.setattr(ai_tools, "_log_ai_audit", _noop_async)
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)
    monkeypatch.setattr(_qp_mod, "load_query_dataset", _fake_load_query_dataset)

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import FakeKufarClient

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ai/price-advice",
            json={
                "query": "iphone 14",
                "current_price_byn": 950,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["advice"] in ("buy_now", "wait", "neutral")
        assert body["reasoning"]
        assert "market_context" in body
        assert "медиана" in body["market_context"].lower()
        assert "price_trend" not in body
        assert "historical_context" not in body
        assert "disclaimer" in body
        assert fake_ai.calls
        assert "текущий рыночный срез" in fake_ai.calls[0]["system"].lower()
        assert "исторические данные" not in fake_ai.calls[0]["system"].lower()


def test_price_advice_cache_hit_does_not_consume_quota(monkeypatch) -> None:
    import api.services.query_pipeline as _qp_mod
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis, ai_tools

    fake_ai = FakeAIChatService()
    rate_calls: list[str] = []
    audit_calls: list[dict] = []

    async def _fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            price_stats=SimpleNamespace(
                median=1000.0, count=10, q1=900.0, q3=1100.0, min=800.0, max=1200.0,
            ),
        )

    async def _fake_rate_limit(request, user_id, *, endpoint="default"):
        del request, user_id
        rate_calls.append(endpoint)

    async def _fake_audit(*args, **kwargs):
        del args
        audit_calls.append(kwargs)

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(ai_tools, "_check_ai_consent", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_ai_entitlement", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_rate_limit", _fake_rate_limit)
    monkeypatch.setattr(ai_tools, "_log_ai_audit", _fake_audit)
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)
    monkeypatch.setattr(_qp_mod, "load_query_dataset", _fake_load_query_dataset)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    payload = {
        "query": "iphone 14 wave97",
        "current_price_byn": 950,
    }

    with TestClient(app) as client:
        first = client.post("/api/v1/ai/price-advice", json=payload)
        second = client.post("/api/v1/ai/price-advice", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert rate_calls == ["price_advice"]
    assert len(fake_ai.calls) == 1
    assert [call.get("cached") for call in audit_calls] == [None, True]
