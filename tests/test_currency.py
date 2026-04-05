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


def test_currency_endpoint_returns_rates() -> None:
    from api.dependencies import get_currency_service
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/currency-rates")
    assert response.status_code == 200
    assert response.json()["rates"]["USD"] == 3.2
