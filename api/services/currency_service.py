from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from api.services.cache import CacheBackend

NBRB_URL = "https://api.nbrb.by/exrates/rates?periodicity=0"
DEFAULT_USD_RATE = 3.0


class CurrencyService:
    def __init__(self, cache: CacheBackend, http_client: httpx.AsyncClient | None = None) -> None:
        self._cache = cache
        self._http_client = http_client
        self._owns_client = http_client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=3.0)
        return self._http_client

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()

    async def get_rates(self) -> dict[str, Any]:
        cached = await self._cache.get_json("currency:rates")
        cached_usd = float(cached.get("rates", {}).get("USD", 0.0)) if cached else 0.0
        if cached and cached_usd > 0:
            return cached

        rates = {
            "USD": DEFAULT_USD_RATE,
        }
        source = "fallback"
        fetched_at = datetime.now(UTC).isoformat()

        try:
            client = await self._get_client()
            response = await client.get(NBRB_URL)
            response.raise_for_status()
            items = response.json()
            rates = self._extract_rates(items)
            source = "nbrb"
        except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError):
            # Keep fallback payload shape stable for the API.
            pass

        payload = {
            "base": "BYN",
            "rates": rates,
            "source": source,
            "fetched_at": fetched_at,
        }
        await self._cache.set_json("currency:rates", payload, ttl=3600)
        return payload

    def convert_from_byn(self, amount_byn: float, currency: str, rates: dict[str, float]) -> float:
        if currency == "BYN":
            return round(amount_byn, 2)
        rate = rates.get(currency)
        if not rate:
            return round(amount_byn, 2)
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
        return rates
