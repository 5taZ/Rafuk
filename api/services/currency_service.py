from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

import httpx

from api.services.cache import CacheBackend

logger = logging.getLogger(__name__)

NBRB_URL = "https://api.nbrb.by/exrates/rates?periodicity=0"
DEFAULT_USD_RATE = 3.0
SUPPORTED_CURRENCIES = ("BYN", "USD")

RATES_CACHE_KEY = "currency:rates"
LAST_GOOD_RATES_CACHE_KEY = "currency:rates:last-good"
RATES_TTL_SECONDS = 3600
# A stale or hardcoded rate is only cached briefly so NBRB is retried soon.
FALLBACK_TTL_SECONDS = 60
LAST_GOOD_TTL_SECONDS = 30 * 24 * 3600


def _usd_rate(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return None
    try:
        rate = float(rates.get("USD", 0.0))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rate) or rate <= 0:
        return None
    return rate


class CurrencyService:
    def __init__(self, cache: CacheBackend, http_client: httpx.AsyncClient | None = None) -> None:
        self._cache = cache
        self._http_client = http_client
        self._owns_client = http_client is None
        self._last_good: dict[str, Any] | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=3.0)
        return self._http_client

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()

    async def get_rates(self) -> dict[str, Any]:
        """Return BYN-based rates.

        `source` is "nbrb" for a fresh official rate, "stale" for the last rate NBRB returned
        before the current failure, and "fallback" for the hardcoded rate when nothing is known.
        """
        cached = await self._cache.get_json(RATES_CACHE_KEY)
        if _usd_rate(cached) is not None:
            return cached

        try:
            client = await self._get_client()
            response = await client.get(NBRB_URL)
            response.raise_for_status()
            rates = self._extract_rates(response.json())
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            ZeroDivisionError,
            RuntimeError,
        ):
            logger.warning("NBRB rates request failed", exc_info=True)
        else:
            payload = {
                "base": "BYN",
                "rates": rates,
                "source": "nbrb",
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            self._last_good = payload
            await self._cache.set_json(RATES_CACHE_KEY, payload, ttl=RATES_TTL_SECONDS)
            await self._cache.set_json(
                LAST_GOOD_RATES_CACHE_KEY,
                payload,
                ttl=LAST_GOOD_TTL_SECONDS,
            )
            return payload

        last_good = self._last_good
        if _usd_rate(last_good) is None:
            last_good = await self._cache.get_json(LAST_GOOD_RATES_CACHE_KEY)
        if _usd_rate(last_good) is not None:
            # fetched_at keeps the time of the last successful NBRB response.
            payload = {**last_good, "source": "stale"}
        else:
            logger.warning("No known NBRB rate, using the hardcoded fallback rate")
            payload = {
                "base": "BYN",
                "rates": {"USD": DEFAULT_USD_RATE},
                "source": "fallback",
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        await self._cache.set_json(RATES_CACHE_KEY, payload, ttl=FALLBACK_TTL_SECONDS)
        return payload

    def convert_from_byn(self, amount_byn: float, currency: str, rates: dict[str, float]) -> float:
        if currency == "BYN":
            return round(amount_byn, 2)
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency: {currency!r}")
        rate = rates.get(currency)
        if not rate or rate <= 0:
            raise ValueError(f"No exchange rate for currency {currency!r}")
        return round(amount_byn / rate, 2)

    @staticmethod
    def _extract_rates(items: list[dict[str, Any]]) -> dict[str, float]:
        rates: dict[str, float] = {}
        for item in items:
            code = item.get("Cur_Abbreviation")
            official_rate = item.get("Cur_OfficialRate")
            scale = item.get("Cur_Scale", 1)
            if code == "USD" and official_rate:
                rates[code] = float(official_rate) / float(scale)
        if "USD" not in rates:
            raise ValueError("USD rate is missing")
        if not math.isfinite(rates["USD"]) or rates["USD"] <= 0:
            raise ValueError("USD rate is invalid")
        return rates
