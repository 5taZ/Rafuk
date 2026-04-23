from __future__ import annotations

from time import monotonic, sleep
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData
from api.services.ai_service import AIService


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


class FakeAIService:
    available = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def analyze_listing(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {
            "condition": {
                "label": "Хорошее",
                "confidence": 0.88,
                "notes": ["Корпус выглядит аккуратно"],
            },
            "fair_price": {
                "from": 1500,
                "to": 1650,
                "reasoning": "Ориентир по рынку и состоянию",
            },
            "watch_out": [{"point": "Аккумулятор", "why": "Проверьте остаточную ёмкость"}],
            "recommendation": {
                "verdict": "think_twice",
                "text": "Попросите диагностику и торгуйтесь.",
            },
            "summary": "Выглядит адекватно, но нужен осмотр.",
        }


class FakeOutlierAIService:
    available = True

    async def analyze_listing(self, **kwargs) -> dict:
        del kwargs
        return {
            "fair_price": {
                "from": 170000,
                "to": 210000,
                "reasoning": "Это рестайлинг 4M 2022 года.",
            },
            "resale_potential": {
                "fast_price": {"label": "Быстро", "price_byn": 160000, "reasoning": "ниже рынка"},
                "market_price": {"label": "По рынку", "price_byn": 185000, "reasoning": "средняя"},
                "optimal_price": {
                    "label": "Оптимально",
                    "price_byn": 205000,
                    "reasoning": "максимум",
                },
                "reasoning": "Высокий спрос",
            },
            "recommendation": {
                "verdict": "think_twice",
                "text": "Ориентируйтесь на 180 000 BYN.",
            },
            "market_context": "Реальная стоимость около 180 000 BYN.",
            "summary": "Реальная стоимость около 180 000 BYN, а не 44 000 BYN.",
        }


def _wait_for_task_result(client: TestClient, task_id: str, timeout_s: float = 5.0) -> dict:
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        response = client.get(f"/api/v1/ai/task/{task_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == "done":
            return payload["result"]
        if payload["status"] == "error":
            raise AssertionError(payload.get("error") or "AI task failed")
        sleep(0.05)
    raise AssertionError("AI task did not finish in time")


def test_ai_analyze_endpoint_returns_payload(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            ads=[
                {
                    "ad_id": 1,
                    "subject": "iPhone 14",
                    "body": "Отличное состояние",
                    "price_byn": 160000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "images": [{"path": "adim1/test.jpg"}],
                    "ad_parameters": [
                        {"p": "condition", "vl": "Б/у"},
                        {"p": "memory", "pl": "Память", "vl": "128 Гб"},
                    ],
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 14 128GB",
                    "price_byn": 158000,
                    "ad_link": "https://www.kufar.by/item/2",
                    "images": [{"path": "adim1/test2.jpg"}],
                    "ad_parameters": [],
                },
            ],
            price_stats=SimpleNamespace(
                median=1590.0,
                count=24,
                q1=1550.0,
                q3=1630.0,
                min=1500.0,
                max=1700.0,
            ),
        )

    fake_ai = FakeAIService()
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)
    monkeypatch.setattr(ai_analysis, "load_query_dataset", fake_load_query_dataset)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/analyze",
            json={"ad_id": 1, "query": "iphone 14"},
        )
        assert response.status_code == 200
        start_payload = response.json()
        assert start_payload["task_id"]

        payload = _wait_for_task_result(client, start_payload["task_id"])

    assert payload["ad_id"] == 1
    assert payload["condition"]["label"] == "Хорошее"
    assert payload["fair_price"]["from_price"] == 1500
    assert payload["best_alternative"]["ad_id"] == 2
    assert fake_ai.calls
    assert fake_ai.calls[0]["is_negotiable_price"] is False


def test_ai_negotiable_price_context_does_not_use_zero_as_real_price() -> None:
    service = AIService()

    context = service._build_listing_context(
        title="iPhone 14",
        description="Цена договорная",
        price_byn=0,
        is_negotiable_price=True,
        condition="Б/у",
        parameters=[{"label": "Память", "value": "128 Гб"}],
        market_median=1600,
        market_count=12,
        market_q1=1500,
        market_q3=1700,
        market_min=1400,
        market_max=1800,
        seller_type="private",
        photo_count=3,
        similar_listings=[
            {
                "ad_id": 2,
                "title": "iPhone 14 128GB",
                "price_byn": 1580,
                "condition": "Б/у",
                "seller_type": "private",
                "age_days": 2,
            }
        ],
    )

    assert "Цена: договорная" in context
    assert "цена покупки 0 BYN" not in context
    assert "на 1580 BYN дороже" not in context
    assert "ниже медианы" in context


def test_ai_guardrails_clamp_outlier_price_for_negotiable_financing_bait(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            ads=[
                {
                    "ad_id": 10,
                    "subject": "БЕЗ ВЗНОСА НА 10 ЛЕТ AUDI Q7 4M Рестайлинг",
                    "body": "Цена договорная, возможен кредит.",
                    "price_byn": 0,
                    "ad_link": "https://www.kufar.by/item/10",
                    "images": [{"path": "adim1/test.jpg"}],
                    "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
                },
                {
                    "ad_id": 11,
                    "subject": "Audi Q7 3.0 tdi",
                    "price_byn": 4469200,
                    "ad_link": "https://www.kufar.by/item/11",
                    "images": [{"path": "adim1/test2.jpg"}],
                    "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
                },
                {
                    "ad_id": 12,
                    "subject": "Audi Q7 (4L) Рестайлинг",
                    "price_byn": 4005400,
                    "ad_link": "https://www.kufar.by/item/12",
                    "images": [{"path": "adim1/test3.jpg"}],
                    "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
                },
            ],
            price_stats=SimpleNamespace(
                median=44411.0,
                count=18,
                q1=40054.0,
                q3=44692.0,
                min=39000.0,
                max=47000.0,
            ),
        )

    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: FakeOutlierAIService())
    monkeypatch.setattr(ai_analysis, "load_query_dataset", fake_load_query_dataset)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/analyze",
            json={"ad_id": 10, "query": "audi q7"},
        )
        assert response.status_code == 200
        start_payload = response.json()
        payload = _wait_for_task_result(client, start_payload["task_id"])

    assert payload["fair_price"]["from_price"] == 40054
    assert payload["fair_price"]["to_price"] == 44692
    assert payload["resale_potential"]["market_price"]["price_byn"] == 42373
    assert "180 000" not in payload["summary"]
    assert "40" in payload["market_context"]
