from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


class FakeAIService:
    available = True

    async def analyze_listing(self, **kwargs) -> dict:
        del kwargs
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
                median=1590.0, count=24, q1=1550.0, q3=1630.0, min=1500.0, max=1700.0
            ),
        )

    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: FakeAIService())
    monkeypatch.setattr(ai_analysis, "load_query_dataset", fake_load_query_dataset)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/analyze",
            json={"ad_id": 1, "query": "iphone 14"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ad_id"] == 1
    assert payload["condition"]["label"] == "Хорошее"
    assert payload["fair_price"]["from"] == 1500
    assert payload["best_alternative"]["ad_id"] == 2
