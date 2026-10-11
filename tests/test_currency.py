from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
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


def test_currency_endpoint_reports_stale_rate_when_nbrb_fails() -> None:
    from api.dependencies import get_currency_service
    from api.main import create_app
    from api.services.cache import MemoryCache
    from api.services.currency_service import LAST_GOOD_RATES_CACHE_KEY, CurrencyService

    cache = MemoryCache()
    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=httpx.ConnectError("NBRB is down"))
    service = CurrencyService(cache, http_client=http_client)

    app = create_app()
    app.dependency_overrides[get_currency_service] = lambda: service
    with TestClient(app) as client:
        client.portal.call(
            cache.set_json,
            LAST_GOOD_RATES_CACHE_KEY,
            {
                "base": "BYN",
                "rates": {"USD": 3.1},
                "source": "nbrb",
                "fetched_at": "2026-10-01T09:00:00+00:00",
            },
        )
        response = client.get("/api/v1/currency-rates")
    http_client.get.assert_awaited()
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "stale"
    assert payload["rates"]["USD"] == 3.1
