from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient


class FakeCurrencyService:
    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": {"USD": 3.2, "EUR": 3.5},
            "source": "test",
            "fetched_at": datetime.now(UTC).isoformat(),
        }


def test_create_app_has_expected_routes() -> None:
    from api.main import create_app

    app = create_app()
    paths = {route.path for route in app.router.routes}
    assert "/api/v1/price-stats" in paths
    assert "/api/v1/listings" in paths
    assert "/api/v1/segments" in paths
    assert "/api/v1/geography" in paths
    assert "/api/v1/currency-rates" in paths
    assert "/api/v1/compare" in paths
    assert "/api/v1/saved-searches" in paths
    assert "/api/v1/opportunity-board" in paths
    assert "/api/v1/leads" in paths
    assert "/api/v1/watchlist" in paths


def test_currency_endpoint_smoke() -> None:
    from api.dependencies import get_currency_service
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/currency-rates")
    assert response.status_code == 200
    assert response.json()["base"] == "BYN"
