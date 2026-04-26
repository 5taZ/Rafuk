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

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._settings.kufar_timeout)
        return self._http_client

    async def aclose(self) -> None:
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
                response = await client.get(KUFAR_BASE_URL, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                logger.warning(
                    "Kufar request failed on attempt %s/%s for query=%s: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    query,
                    exc,
                )
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
