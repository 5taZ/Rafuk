from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.cache import MemoryCache
from api.services.currency_service import CurrencyService


@pytest.mark.asyncio
async def test_currency_service_fetches_and_caches_rates() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = [
        {"Cur_Abbreviation": "USD", "Cur_OfficialRate": 3.2, "Cur_Scale": 1},
        {"Cur_Abbreviation": "EUR", "Cur_OfficialRate": 3.5, "Cur_Scale": 1},
    ]
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=response)

    service = CurrencyService(MemoryCache(), http_client=http_client)
    payload = await service.get_rates()
    assert payload["source"] == "nbrb"
    assert payload["rates"]["USD"] == 3.2
    assert payload["rates"]["EUR"] == 3.5


@pytest.mark.asyncio
async def test_currency_service_uses_safe_fallback_when_fetch_fails() -> None:
    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

    service = CurrencyService(MemoryCache(), http_client=http_client)
    payload = await service.get_rates()

    assert payload["source"] == "fallback"
    assert payload["rates"]["USD"] == 3.0


def test_convert_from_byn() -> None:
    service = CurrencyService(MemoryCache())
    assert service.convert_from_byn(3200.0, "USD", {"USD": 3.2}) == 1000.0
    assert service.convert_from_byn(100.0, "BYN", {"USD": 3.2}) == 100.0
