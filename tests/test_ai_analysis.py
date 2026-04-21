from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData
from api.services.photo_search_service import PhotoSearchResult, PhotoSearchService


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


class FakePhotoSearchService:
    async def identify_from_bytes(
        self,
        image_bytes: bytes,
        mime_type: str,
        ai_service,
    ) -> PhotoSearchResult:
        del image_bytes, mime_type, ai_service
        return PhotoSearchResult(
            query="iphone 14 128gb",
            description="Запрос собран по тексту на фото: iPhone 14, 128GB.",
            source="ocr",
            recognized_text=["iPhone 14", "128GB"],
        )


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search(self, **kwargs) -> dict:
        del kwargs
        return {
            "ads": [
                {
                    "ad_id": 10,
                    "subject": "iPhone 14 128GB",
                    "price_byn": 175000,
                    "ad_link": "https://www.kufar.by/item/10",
                    "list_time": "2026-04-16T10:00:00",
                    "images": [{"path": "adim1/test.jpg"}],
                }
            ]
        }

    async def aclose(self) -> None:
        return None


def test_photo_search_service_extracts_iphone_query() -> None:
    service = PhotoSearchService()

    query = service._build_query(["iPhone 14 Pro", "128GB"])

    assert query == "iphone 14 pro 128gb"


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
            price_stats=SimpleNamespace(median=1590.0, count=24),
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


def test_search_by_photo_endpoint_uses_fallback_service(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis

    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: FakeAIService())
    monkeypatch.setattr(ai_analysis, "get_photo_search_service", lambda: FakePhotoSearchService())
    monkeypatch.setattr(ai_analysis, "KufarClient", FakeKufarClient)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/search-by-photo",
            files={"photo": ("phone.jpg", b"x" * 1024, "image/jpeg")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "ocr"
    assert payload["query"] == "iphone 14 128gb"
    assert payload["recognized_text"] == ["iPhone 14", "128GB"]
    assert payload["listings"][0]["ad_id"] == 10
