from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from api.config import Settings

logger = logging.getLogger(__name__)

KUFAR_BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
USER_AGENT = "Mozilla/5.0 (compatible; KufarAnalytics/1.0; +https://kufar.by)"
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0


class KufarAPIError(Exception):
    """Raised when Kufar API remains unavailable after retries."""


class KufarClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http_client = http_client
        self._owns_client = http_client is None
        self._closed = False
        self._last_request_time: float = 0.0
        # Serialises _enforce_delay so concurrent search() calls (e.g.
        # the parallel category-totals fan-out) read+write
        # _last_request_time atomically. Without this two coroutines
        # could both observe the previous timestamp, both decide they
        # don't need to wait, and fire requests inside Kufar's
        # configured rate-limit window. The semaphore in
        # query_pipeline already caps concurrency to 2, but the lock
        # makes the spacing correct regardless of how many callers race.
        self._delay_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        # Limit parallel HTTP requests across all KufarClient users
        self._semaphore = asyncio.Semaphore(settings.kufar_parallel_semaphore)
        # Simple circuit breaker: after 5 consecutive errors open the
        # circuit for 30 seconds and return empty results.
        #
        # _circuit_lock serialises read-modify-write of _consecutive_errors
        # / _circuit_open_until. Without it, two coroutines can each
        # observe an old counter, both decide their failure is the
        # 5th-in-a-row, and either step on each other (counter overshoots)
        # or — worse — a success path resets the counter to 0 between
        # a failure path's read and its compare, so the circuit never
        # opens despite sustained errors.
        self._circuit_lock = asyncio.Lock()
        self._consecutive_errors = 0
        self._circuit_open_until: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise RuntimeError("KufarClient is closed")
        if self._http_client is None or self._http_client.is_closed:
            async with self._client_lock:
                if self._closed:
                    raise RuntimeError("KufarClient is closed")
                if self._http_client is None or self._http_client.is_closed:
                    # Bound the connection pool. Without limits a burst
                    # (e.g. the parallel category-totals fan-out racing
                    # with a watchlist refresh) can open hundreds of
                    # TCP sockets, exhaust file descriptors and slow
                    # everything down. The semaphore in this class
                    # already caps concurrency, but the pool limit
                    # protects us if anyone bypasses it (tests,
                    # background tasks, or future callers).
                    self._http_client = httpx.AsyncClient(
                        timeout=self._settings.kufar_timeout,
                        limits=httpx.Limits(
                            max_connections=20,
                            max_keepalive_connections=10,
                        ),
                    )
        return self._http_client

    async def aclose(self) -> None:
        async with self._client_lock:
            self._closed = True
            if self._owns_client and self._http_client is not None:
                await self._http_client.aclose()

    async def _enforce_delay(self) -> None:
        delay = self._settings.kufar_request_delay
        if delay <= 0:
            return
        async with self._delay_lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_request_time
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request_time = loop.time()

    async def search(
        self,
        query: str,
        size: int = 200,
        currency: str = "USD",
        sort: str = "lst.d",
        cursor: str | None = None,
        region: int | None = None,
        condition: str | None = None,
        seller_type: str | None = None,
        category: int | None = None,
        bypass_delay: bool = False,
    ) -> dict[str, Any]:
        # Circuit breaker check.
        # Reading _circuit_open_until is a single 64-bit float load —
        # CPython makes that atomic, so we don't need the lock here.
        # (We only need the lock around READ-MODIFY-WRITE on the
        # counter, see below.)
        now = asyncio.get_running_loop().time()
        if now < self._circuit_open_until:
            logger.warning("Circuit breaker open for query=%s; returning empty stub", query)
            return {"ads": [], "total": 0}

        params: dict[str, Any] = {
            "query": query,
            "size": size,
            "cur": currency,
            "sort": sort,
        }
        if cursor:
            params["cursor"] = cursor
        if region is not None:
            params["rgn"] = region
        if condition:
            params["cnd"] = condition
        if category is not None:
            params["cat"] = category
        # NOTE: Kufar API no longer accepts the "otype" parameter (422 since 2026).
        # Seller type filtering is done client-side after fetching results.

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.kufar.by/",
        }

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                if not bypass_delay:
                    await self._enforce_delay()
                client = await self._get_client()
                async with self._semaphore:
                    response = await client.get(KUFAR_BASE_URL, params=params, headers=headers)
                response.raise_for_status()
                # Success — reset error counter under the lock so a
                # concurrent failure path can't observe an old (high)
                # counter mid-update.
                async with self._circuit_lock:
                    self._consecutive_errors = 0
                return response.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                # Read-modify-write of the error counter MUST be atomic
                # vs. a concurrent success-reset; otherwise two failures
                # racing with a success could either overshoot the
                # threshold or never reach it.
                async with self._circuit_lock:
                    self._consecutive_errors += 1
                    consecutive = self._consecutive_errors
                    if consecutive >= 5:
                        self._circuit_open_until = (
                            asyncio.get_running_loop().time() + 30.0
                        )
                logger.warning(
                    "Kufar request failed on attempt %s/%s for query=%s: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    query,
                    exc,
                )
                if consecutive >= 5:
                    logger.error("Circuit breaker opened after 5 consecutive errors")
                    break
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(BACKOFF_BASE_SECONDS * (2**attempt))

        raise KufarAPIError(
            f"Kufar API request failed after {MAX_RETRIES} attempts"
        ) from last_error

    async def search_all_ads(
        self,
        query: str,
        size: int = 200,
        currency: str = "USD",
        sort: str = "lst.d",
        region: int | None = None,
        condition: str | None = None,
        seller_type: str | None = None,
        category: int | None = None,
    ) -> dict[str, Any]:
        response = await self.search(
            query=query,
            size=size,
            currency=currency,
            sort=sort,
            region=region,
            condition=condition,
            category=category,
        )
        ads = list(response.get("ads", []))
        total = self.extract_total(response) or len(ads)
        cursor = self.extract_next_cursor(response)
        pages_fetched = 1

        # Safety cap: max 25 pages or configured max ads (whichever comes first)
        max_ads = self._settings.kufar_max_ads_per_query
        while cursor and pages_fetched < 25 and len(ads) < max_ads:
            page = await self.search(
                query=query,
                size=size,
                currency=currency,
                sort=sort,
                cursor=cursor,
                region=region,
                condition=condition,
                category=category,
            )
            ads.extend(page.get("ads", []))
            cursor = self.extract_next_cursor(page)
            pages_fetched += 1
            if total and len(ads) >= total:
                break

        return {
            **response,
            "ads": ads,
            "total": total,
            "fetched_count": len(ads),
        }

    @staticmethod
    def extract_next_cursor(response: dict[str, Any]) -> str | None:
        try:
            pages = response["pagination"]["pages"]
        except (KeyError, TypeError):
            return None
        if not pages:
            return None
        return pages[0].get("token")

    @staticmethod
    def extract_total(response: dict[str, Any]) -> int | None:
        try:
            total = response.get("total")
        except AttributeError:
            return None
        if total is None:
            return None
        try:
            return int(total)
        except (TypeError, ValueError):
            return None