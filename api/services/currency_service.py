from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from api.services.cache import CacheBackend

logger = logging.getLogger(__name__)

NBRB_URL = "https://api.nbrb.by/exrates/rates?periodicity=0"
DEFAULT_USD_RATE = 3.0
# B-07: fallback covers all supported currencies so convert_from_byn
# never silently returns BYN-amount labelled as EUR/RUB.
FALLBACK_RATES: dict[str, float] = {"USD": DEFAULT_USD_RATE, "EUR": 3.3, "RUB": 0.033}


async def get_rates_safe(currency_service: Any) -> dict[str, Any]:
    """``currency_service.get_rates()`` that never raises.

    CC-10: six routers each caught the failure and fell back to
    ``{"BYN": 1.0}`` — silently, and losing the USD/EUR/RUB fallback B-07
    asked for. A module function rather than a method so duck-typed
    services (tests, stubs) only need ``get_rates``.
    """
    try:
        return await currency_service.get_rates()
    except Exception:  # noqa: BLE001 — rates must never 500 a market endpoint
        logger.warning("currency_service: get_rates failed; serving fallback rates", exc_info=True)
        return {
            "base": "BYN",
            "rates": dict(FALLBACK_RATES),
            "source": "fallback",
            "fetched_at": datetime.now(UTC).isoformat(),
        }


class CurrencyService:
    def __init__(self, cache: CacheBackend, http_client: httpx.AsyncClient | None = None) -> None:
        self._cache = cache
        self._http_client = http_client
        self._owns_client = http_client is None
        self._client_lock = asyncio.Lock()
        self._fetch_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            async with self._client_lock:
                if self._http_client is None or self._http_client.is_closed:
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

        async with self._fetch_lock:
            cached = await self._cache.get_json("currency:rates")
            cached_usd = float(cached.get("rates", {}).get("USD", 0.0)) if cached else 0.0
            if cached and cached_usd > 0:
                return cached

            rates = dict(FALLBACK_RATES)
            source = "fallback"
            fetched_at = datetime.now(UTC).isoformat()

            try:
                # L2: The NBRB HTTP fetch blocks all concurrent callers
                # to get_rates() because there is no per-key lock —
                # every coroutine awaits the same single HTTP client.
                # Mitigated by the 3-second httpx timeout; a stuck
                # request falls through to fallback rates.
                client = await self._get_client()
                response = await client.get(NBRB_URL)
                response.raise_for_status()
                items = response.json()
                # B-07 follow-up: partial NBRB response (e.g. only USD)
                # must not discard fallback EUR/RUB — backfill missing.
                fetched_rates = self._extract_rates(items)
                for code, fallback_rate in rates.items():
                    fetched_rates.setdefault(code, fallback_rate)
                rates = fetched_rates
                source = "nbrb"
            except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                # BE-H6: previously the except body was a silent `pass`,
                # so transient NBRB outages and parsing regressions never
                # showed up in logs — the only signal was downstream
                # users noticing stale rates. Log with traceback at
                # WARNING; the fallback rate continues to serve traffic.
                logger.warning(
                    "currency_service: NBRB rate refresh failed (%s: %s); "
                    "using fallback rates",
                    type(exc).__name__, exc,
                    exc_info=True,
                )

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
            if code in ("USD", "EUR", "RUB") and official_rate:
                rates[code] = float(official_rate) / float(scale)
        if "USD" not in rates:
            raise ValueError("USD rate is missing")
        return rates
