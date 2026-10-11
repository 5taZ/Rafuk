from __future__ import annotations

import asyncio
import logging
import math
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from api.config import Settings

logger = logging.getLogger(__name__)

KUFAR_BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
USER_AGENT = "Mozilla/5.0 (compatible; KufarAnalytics/1.0; +https://kufar.by)"
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0
# Upper bound for a server-provided Retry-After, so one 429 cannot stall a request for minutes.
MAX_RETRY_AFTER_SECONDS = 10.0
# Statistics are computed from price_byn only, so every request asks Kufar for BYN prices.
KUFAR_PRICE_CURRENCY = "BYN"
# Pagination guards for search_all_ads: 25 pages of 200 ads, and a deadline that stays below
# nginx's 60 s proxy timeout.
DEFAULT_MAX_PAGES = 25
DEFAULT_DEADLINE_SECONDS = 40.0
# Bounded pacer queue: a request that would wait behind more than this many queued requests
# (or behind more than MAX_PACER_BACKLOG_SECONDS of paced starts) fails at once instead of
# piling up behind a backlog it cannot get through before its deadline.
MAX_PACER_WAITERS = 64
MAX_PACER_BACKLOG_SECONDS = 30.0


async def _backoff_sleep(seconds: float) -> None:
    # Separate hook so tests can skip retry backoff without patching asyncio.sleep globally.
    await asyncio.sleep(seconds)


async def _pacer_sleep(seconds: float) -> None:
    # Separate hook so tests can skip a shared pause without patching asyncio.sleep globally.
    await asyncio.sleep(seconds)


class KufarAPIError(Exception):
    """Raised when Kufar API remains unavailable after retries."""


class KufarBusyError(KufarAPIError):
    """Raised at once when too many Kufar requests are already queued in this process."""


class _RequestPacer:
    """Process-wide pacing of Kufar requests.

    Bounded concurrency, a minimum gap between request starts, a shared pause that every
    request honours after Kufar answers 429 / Retry-After, and a bounded waiting queue.
    """

    def __init__(self, max_in_flight: int, *, max_waiting: int | None = None) -> None:
        self._semaphore = asyncio.Semaphore(max(1, max_in_flight))
        self._lock = asyncio.Lock()
        self._next_start = 0.0
        self._paused_until = 0.0
        self._waiting = 0
        self._max_waiting = max_waiting

    @property
    def waiting(self) -> int:
        """Requests that entered the pacer and have not started yet."""
        return self._waiting

    def waiting_limit(self, delay: float) -> int:
        if self._max_waiting is not None:
            return max(0, self._max_waiting)
        if delay <= 0:
            return MAX_PACER_WAITERS
        return max(1, min(MAX_PACER_WAITERS, int(MAX_PACER_BACKLOG_SECONDS / delay)))

    def pause(self, seconds: float) -> None:
        """Hold back every request of this pacer for `seconds` (Kufar asked to slow down)."""
        if not math.isfinite(seconds) or seconds <= 0:
            return
        until = asyncio.get_running_loop().time() + seconds
        if until > self._paused_until:
            self._paused_until = until
            logger.warning("Pausing Kufar requests for %.1f s after a rate-limit answer", seconds)

    @asynccontextmanager
    async def slot(self, delay: float) -> AsyncIterator[None]:
        if self._waiting >= self.waiting_limit(delay):
            raise KufarBusyError(
                f"Too many Kufar requests are queued ({self._waiting}), try again later"
            )
        self._waiting += 1
        waiting = True
        try:
            async with self._semaphore:
                try:
                    await self._wait_for_start(max(0.0, delay))
                finally:
                    self._waiting -= 1
                    waiting = False
                yield
        finally:
            if waiting:
                self._waiting -= 1

    async def _wait_for_start(self, delay: float) -> None:
        loop = asyncio.get_running_loop()
        while True:
            # Reserve the start time under the lock and sleep outside of it, so concurrent
            # callers queue up at `delay` intervals instead of passing the check together.
            async with self._lock:
                now = loop.time()
                start_at = max(now, self._next_start, self._paused_until)
                self._next_start = start_at + delay
            wait = start_at - now
            if wait > 0:
                await _pacer_sleep(wait)
            # A pause that began while this request slept moves its start past the pause.
            if self._paused_until <= start_at:
                return


# asyncio primitives are bound to one event loop, so the shared pacer is kept per running loop.
_PACERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, _RequestPacer]] = (
    weakref.WeakKeyDictionary()
)


def reset_request_pacers() -> None:
    """Drop every shared pacer, so the next request starts with no backlog (used by tests)."""
    _PACERS.clear()


def _get_pacer(max_in_flight: int) -> _RequestPacer:
    loop = asyncio.get_running_loop()
    pacers = _PACERS.setdefault(loop, {})
    pacer = pacers.get(max_in_flight)
    if pacer is None:
        pacer = _RequestPacer(max_in_flight)
        pacers[max_in_flight] = pacer
    return pacer


def _is_retryable_status(status_code: Any) -> bool:
    return isinstance(status_code, int) and (status_code == 429 or status_code >= 500)


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    try:
        raw_value = response.headers.get("Retry-After")
    except AttributeError:
        return None
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    raw_value = raw_value.strip()
    try:
        seconds = float(raw_value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _ad_id_key(ad: dict[str, Any]) -> int | None:
    value = ad.get("ad_id")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


class KufarClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http_client = http_client
        self._owns_client = http_client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._settings.kufar_timeout)
        return self._http_client

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()

    def _pacer(self) -> _RequestPacer:
        return _get_pacer(int(self._settings.kufar_parallel_semaphore))

    @asynccontextmanager
    async def _request_slot(self) -> AsyncIterator[None]:
        async with self._pacer().slot(float(self._settings.kufar_request_delay)):
            yield

    async def search(
        self,
        query: str,
        size: int = 200,
        currency: str = KUFAR_PRICE_CURRENCY,
        sort: str = "lst.d",
        cursor: str | None = None,
        region: int | None = None,
        condition: str | None = None,
        seller_type: str | None = None,
    ) -> dict[str, Any]:
        # `currency` is kept for backward compatibility only: prices are always read from
        # price_byn and converted by CurrencyService, so Kufar is always asked for BYN.
        del currency
        params: dict[str, Any] = {
            "query": query,
            "size": size,
            "cur": KUFAR_PRICE_CURRENCY,
            "sort": sort,
        }
        if cursor:
            params["cursor"] = cursor
        if region is not None:
            params["rgn"] = region
        if condition:
            params["cnd"] = condition
        if seller_type:
            params["otype"] = seller_type

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.kufar.by/",
        }

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            retry_after: float | None = None
            try:
                async with self._request_slot():
                    client = await self._get_client()
                    response = await client.get(KUFAR_BASE_URL, params=params, headers=headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if not _is_retryable_status(status_code):
                    raise KufarAPIError(f"Kufar API responded with HTTP {status_code}") from exc
                last_error = exc
                retry_after = _retry_after_seconds(exc.response)
                self._log_failure(attempt, query, exc)
                # Kufar asked to slow down: every request of the process waits, not just this
                # one, so parallel paginations do not burn their retries on the same limit.
                if retry_after is not None:
                    self._pacer().pause(retry_after)
                elif status_code == 429:
                    self._pacer().pause(BACKOFF_BASE_SECONDS * (2**attempt))
            except httpx.HTTPError as exc:
                # Network errors and timeouts (httpx.TimeoutException is an HTTPError).
                last_error = exc
                self._log_failure(attempt, query, exc)
            else:
                return self._decode_payload(response)

            if attempt < MAX_RETRIES - 1:
                backoff = BACKOFF_BASE_SECONDS * (2**attempt)
                if retry_after is not None:
                    backoff = max(backoff, retry_after)
                await _backoff_sleep(backoff)

        raise KufarAPIError(
            f"Kufar API request failed after {MAX_RETRIES} attempts"
        ) from last_error

    @staticmethod
    def _log_failure(attempt: int, query: str, exc: Exception) -> None:
        logger.warning(
            "Kufar request failed on attempt %s/%s for query=%s: %s",
            attempt + 1,
            MAX_RETRIES,
            query,
            exc,
        )

    @staticmethod
    def _decode_payload(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            # A 200 with an HTML body (captcha, maintenance page) is not retried.
            raise KufarAPIError("Kufar API returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise KufarAPIError("Kufar API returned an unexpected payload")
        return payload

    async def search_all_ads(
        self,
        query: str,
        size: int = 200,
        currency: str = KUFAR_PRICE_CURRENCY,
        sort: str = "lst.d",
        region: int | None = None,
        condition: str | None = None,
        seller_type: str | None = None,
        max_pages: int | None = DEFAULT_MAX_PAGES,
        deadline_seconds: float | None = DEFAULT_DEADLINE_SECONDS,
    ) -> dict[str, Any]:
        """Follow the pagination cursor and collect unique ads.

        The loop stops at `max_pages` pages, after `deadline_seconds`, or when a later page
        still fails after its retries; in all these cases the ads collected so far are
        returned with `truncated: True` (only a failure of the first page raises). A repeated
        cursor or a page without new ads ends the loop as well (protection against a cursor
        that never advances).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + deadline_seconds if deadline_seconds is not None else None
        search_params: dict[str, Any] = {
            "query": query,
            "size": size,
            "currency": currency,
            "sort": sort,
            "region": region,
            "condition": condition,
            "seller_type": seller_type,
        }

        try:
            response = await self._search_before(deadline, **search_params)
        except TimeoutError as exc:
            raise KufarAPIError("Kufar API did not respond before the deadline") from exc

        ads: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        def add_page(page: dict[str, Any]) -> int:
            page_ads = page.get("ads")
            if not isinstance(page_ads, list):
                return 0
            added = 0
            for ad in page_ads:
                if not isinstance(ad, dict):
                    continue
                ad_id = _ad_id_key(ad)
                if ad_id is not None:
                    if ad_id in seen_ids:
                        continue
                    seen_ids.add(ad_id)
                    added += 1
                # Ads without a usable id are kept here and dropped by the dataset pipeline.
                ads.append(ad)
            return added

        add_page(response)
        total = self.extract_total(response)
        cursor = self.extract_next_cursor(response)
        seen_cursors: set[str] = set()
        pages_fetched = 1
        truncated = False

        while cursor:
            if total is not None and len(ads) >= total:
                break
            if cursor in seen_cursors:
                logger.warning("Kufar returned a repeated cursor for query=%s", query)
                break
            if max_pages is not None and pages_fetched >= max_pages:
                truncated = True
                break
            if deadline is not None and loop.time() >= deadline:
                truncated = True
                break
            seen_cursors.add(cursor)
            try:
                page = await self._search_before(deadline, cursor=cursor, **search_params)
            except TimeoutError:
                truncated = True
                break
            except KufarAPIError as exc:
                # The pages already fetched are still a useful (partial) sample.
                logger.warning(
                    "Kufar page %s failed for query=%s, keeping %s ads: %s",
                    pages_fetched + 1,
                    query,
                    len(ads),
                    exc,
                )
                truncated = True
                break
            pages_fetched += 1
            if add_page(page) == 0:
                break
            cursor = self.extract_next_cursor(page)

        if truncated:
            logger.warning(
                "Kufar pagination truncated for query=%s after %s pages (%s ads)",
                query,
                pages_fetched,
                len(ads),
            )

        return {
            **response,
            "ads": ads,
            "total": total,
            "fetched_count": len(ads),
            "truncated": truncated,
        }

    async def _search_before(self, deadline: float | None, **params: Any) -> dict[str, Any]:
        if deadline is None:
            return await self.search(**params)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        return await asyncio.wait_for(self.search(**params), timeout=remaining)

    @staticmethod
    def extract_next_cursor(response: dict[str, Any]) -> str | None:
        try:
            pages = response["pagination"]["pages"]
        except (KeyError, TypeError):
            return None
        if not isinstance(pages, list):
            return None
        entries = [page for page in pages if isinstance(page, dict)]
        if not entries:
            return None

        def token_of(page: dict[str, Any]) -> str | None:
            token = page.get("token")
            return token if isinstance(token, str) and token else None

        for page in entries:
            label = page.get("label")
            if isinstance(label, str) and label.strip().lower() == "next":
                return token_of(page)
        if any(isinstance(page.get("label"), str) for page in entries):
            # Labelled pagination without a "next" entry: this is the last page.
            return None
        return token_of(entries[0])

    @staticmethod
    def extract_total(response: dict[str, Any]) -> int | None:
        try:
            total = response.get("total")
        except AttributeError:
            return None
        if total is None or isinstance(total, bool):
            return None
        try:
            return int(total)
        except (TypeError, ValueError, OverflowError):
            return None
