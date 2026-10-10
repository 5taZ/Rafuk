from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from api.services.cache import MemoryCache
from api.services.currency_service import (
    FALLBACK_TTL_SECONDS,
    LAST_GOOD_RATES_CACHE_KEY,
    RATES_CACHE_KEY,
    CurrencyService,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def nbrb_response(rate: float) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = [
        {"Cur_Abbreviation": "USD", "Cur_OfficialRate": rate, "Cur_Scale": 1},
    ]
    return response


@pytest.mark.asyncio
async def test_currency_service_fetches_and_caches_rates() -> None:
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=nbrb_response(3.2))

    service = CurrencyService(MemoryCache(), http_client=http_client)
    payload = await service.get_rates()
    assert payload["source"] == "nbrb"
    assert payload["rates"]["USD"] == 3.2

    assert (await service.get_rates())["rates"]["USD"] == 3.2
    assert http_client.get.await_count == 1


@pytest.mark.asyncio
async def test_currency_service_uses_safe_fallback_when_fetch_fails() -> None:
    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

    service = CurrencyService(MemoryCache(), http_client=http_client)
    payload = await service.get_rates()

    assert payload["source"] == "fallback"
    assert payload["rates"]["USD"] == 3.0


@pytest.mark.asyncio
async def test_fallback_rate_is_cached_briefly_and_nbrb_is_retried() -> None:
    clock = FakeClock()
    cache = MemoryCache(clock=clock)
    http_client = MagicMock()
    http_client.get = AsyncMock(
        side_effect=[httpx.ConnectError("down"), nbrb_response(2.85)],
    )
    service = CurrencyService(cache, http_client=http_client)

    first = await service.get_rates()
    assert first["source"] == "fallback"
    # Within the short TTL the fallback is served from cache without hitting NBRB again.
    assert (await service.get_rates())["source"] == "fallback"
    assert http_client.get.await_count == 1

    clock.now += FALLBACK_TTL_SECONDS + 1
    recovered = await service.get_rates()
    assert recovered["source"] == "nbrb"
    assert recovered["rates"]["USD"] == 2.85
    assert http_client.get.await_count == 2


@pytest.mark.asyncio
async def test_last_known_good_rate_is_served_as_stale(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    cache = MemoryCache(clock=clock)
    http_client = MagicMock()
    http_client.get = AsyncMock(
        side_effect=[nbrb_response(3.25), httpx.ConnectError("down")],
    )
    service = CurrencyService(cache, http_client=http_client)

    fresh = await service.get_rates()
    assert fresh["source"] == "nbrb"

    clock.now += 3601  # the regular rate entry expires
    stale = await service.get_rates()
    assert stale["source"] == "stale"
    assert stale["rates"]["USD"] == 3.25
    assert stale["fetched_at"] == fresh["fetched_at"]
    assert "NBRB rates request failed" in caplog.text


@pytest.mark.asyncio
async def test_last_known_good_rate_survives_service_restart() -> None:
    cache = MemoryCache()
    await cache.set_json(
        LAST_GOOD_RATES_CACHE_KEY,
        {"base": "BYN", "rates": {"USD": 3.1}, "source": "nbrb", "fetched_at": "x"},
    )
    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=httpx.ReadTimeout("slow"))

    payload = await CurrencyService(cache, http_client=http_client).get_rates()
    assert payload["source"] == "stale"
    assert payload["rates"]["USD"] == 3.1


@pytest.mark.asyncio
async def test_corrupt_cached_rates_are_ignored() -> None:
    cache = MemoryCache()
    await cache.set_json(RATES_CACHE_KEY, {"rates": {"USD": "nan"}})
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=nbrb_response(3.3))

    payload = await CurrencyService(cache, http_client=http_client).get_rates()
    assert payload["source"] == "nbrb"
    assert payload["rates"]["USD"] == 3.3


def test_convert_from_byn() -> None:
    service = CurrencyService(MemoryCache())
    assert service.convert_from_byn(3200.0, "USD", {"USD": 3.2}) == 1000.0
    assert service.convert_from_byn(100.0, "BYN", {"USD": 3.2}) == 100.0


@pytest.mark.parametrize("currency", ["EUR", "usd", ""])
def test_convert_from_byn_rejects_unsupported_currency(currency: str) -> None:
    service = CurrencyService(MemoryCache())
    with pytest.raises(ValueError):
        service.convert_from_byn(100.0, currency, {"USD": 3.2, "EUR": 3.5})


def test_convert_from_byn_rejects_missing_rate() -> None:
    service = CurrencyService(MemoryCache())
    with pytest.raises(ValueError):
        service.convert_from_byn(100.0, "USD", {})
