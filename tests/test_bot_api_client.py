from __future__ import annotations

from starlette.testclient import TestClient as _OriginalTestClient


def _reset_settings(monkeypatch, *, internal_token: str | None = None):
    from api.config import get_settings
    from bot import api_client

    if internal_token is None:
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", internal_token)
    get_settings.cache_clear()
    api_client._warned_about_legacy_initdata_path = False
    return api_client


def test_build_api_headers_prefers_internal_service_token(monkeypatch) -> None:
    api_client = _reset_settings(monkeypatch, internal_token="svc-secret")

    headers = api_client.build_api_headers(telegram_user_id=123456)

    assert headers == {
        "X-Internal-Service-Token": "svc-secret",
        "X-Acting-Telegram-User-Id": "123456",
    }


def test_build_api_headers_adds_csrf_headers_for_mutations(monkeypatch) -> None:
    api_client = _reset_settings(monkeypatch, internal_token="svc-secret")

    headers = api_client.build_api_headers(telegram_user_id=123456, mutating=True)

    assert headers["X-Internal-Service-Token"] == "svc-secret"
    assert headers["X-Acting-Telegram-User-Id"] == "123456"
    assert headers["Origin"] == "https://kufar-analytics.example.com"
    assert headers["X-Requested-With"] == "XMLHttpRequest"


def test_build_api_headers_keeps_legacy_initdata_fallback(monkeypatch) -> None:
    api_client = _reset_settings(monkeypatch)

    headers = api_client.build_api_headers(telegram_user_id=123456)

    assert "X-Telegram-Init-Data" in headers
    assert "X-Internal-Service-Token" not in headers


def test_bot_mutating_headers_pass_real_csrf_and_service_auth(monkeypatch) -> None:
    api_client = _reset_settings(monkeypatch, internal_token="svc-secret")

    from api.config import get_settings
    from api.main import create_app

    app = create_app()
    headers = api_client.build_api_headers(telegram_user_id=123456, mutating=True)
    with _OriginalTestClient(app) as client:
        response = client.post(
            "/api/v1/leads",
            headers=headers,
            json={
                "query": "iphone 15",
                "ad_id": 960001,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/960001",
                "price_byn": 2000,
                "source": "bot_callback",
            },
        )

    assert response.status_code == 201
    assert response.json()["ad_id"] == 960001
    get_settings.cache_clear()


def test_service_auth_without_mutating_csrf_headers_is_rejected(monkeypatch) -> None:
    api_client = _reset_settings(monkeypatch, internal_token="svc-secret")

    from api.config import get_settings
    from api.main import create_app

    app = create_app()
    headers = api_client.build_api_headers(telegram_user_id=123456)
    with _OriginalTestClient(app) as client:
        response = client.post(
            "/api/v1/leads",
            headers=headers,
            json={
                "query": "iphone 15",
                "ad_id": 960002,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/960002",
                "price_byn": 2000,
                "source": "bot_callback",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"].startswith("CSRF:")
    get_settings.cache_clear()
