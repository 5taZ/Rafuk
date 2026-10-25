from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from api.config import Settings
from api.services.kufar_filters import KUFAR_CONDITION_VALUES, KUFAR_SELLER_VALUES

logger = logging.getLogger(__name__)

KUFAR_BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
USER_AGENT = "Mozilla/5.0 (compatible; KufarAnalytics/1.0; +https://kufar.by)"
MAX_RETRIES = 3
# RV2-BK-01: the cursor parser used to read the ``self`` entry (always a
# null token), so every dataset was a single page of <= 200 ads. The parser
# now finds ``next``; going deeper multiplies upstream load per key (SEC-04)
# and is an owner decision, so the effective depth stays one page until
# this is raised (the old hard cap was 25).
KUFAR_MAX_PAGES = 1
BACKOFF_BASE_SECONDS = 1.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_SECONDS = 30.0
# RV-BE-05: 4xx means "this request is wrong", so it is neither retried
# nor counted by the breaker (one malformed query must not trip it for
# everybody). 408 and 429 are the exceptions: Kufar is slow or wants us
# to back off.
RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})
# RV-BE-05: Retry-After is honoured, but a request is never parked for
# longer than this; a longer server-requested pause fails fast instead
# of retrying before Kufar allows it.
RETRY_AFTER_MAX_SECONDS = 10.0


class KufarAPIError(Exception):
    """Raised when Kufar API remains unavailable after retries."""

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        # Seconds until a retry makes sense, when known (open breaker,
        # Retry-After); the API echoes it in the 503 Retry-After header.
        self.retry_after = retry_after


class KufarUnavailableError(KufarAPIError):
    """Raised without contacting Kufar while the circuit breaker is open.

    RV-BE-05: the breaker used to hand out ``{"ads": [], "total": 0}``.
    Callers cached that stub and treated it as a genuine "nothing found"
    (zero medians, empty tracker baselines, lots marked missing). As a
    ``KufarAPIError`` it is now skipped by the scheduler and answered
    with 503 by the API.
    """


def _is_retryable_status(status_code: object) -> bool:
    if not isinstance(status_code, int):
        return True
    return status_code >= 500 or status_code in RETRYABLE_CLIENT_STATUSES


def _retry_after_seconds(response: object) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP-date) from *response*."""
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


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
        # circuit for 30 seconds and fail fast with KufarUnavailableError
        # (RV-BE-05: it used to return an empty result instead).
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
        # SEC-06: half-open state. After _circuit_open_until elapses,
        # the FIRST request to grab the lock transitions to half_open
        # and is allowed through as a probe. Concurrent callers see
        # half_open and short-circuit so the upstream isn't slammed
        # with a "thundering herd" the moment the open window expires.
        # Probe outcome decides the next state:
        #   success → closed (counter reset)
        #   failure → open again for another window
        # State ∈ {"closed", "open", "half_open"}.
        self._circuit_state: str = "closed"

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

    def _open_remaining(self) -> float | None:
        if self._circuit_state != "open":
            return None
        return max(0.0, self._circuit_open_until - asyncio.get_running_loop().time())

    async def _enter_circuit(self, query: str) -> bool:
        """Admit one search through the breaker; True if it is the half-open probe.

        SEC-06: 3-state circuit breaker (closed / open / half_open). The
        state machine lives behind _circuit_lock so concurrent callers
        can't race through the half-open probe in lockstep.
        """
        async with self._circuit_lock:
            now = asyncio.get_running_loop().time()
            if self._circuit_state == "open":
                if now < self._circuit_open_until:
                    logger.warning("Kufar circuit breaker open; rejecting query=%s", query)
                    raise KufarUnavailableError(
                        "Kufar circuit breaker is open",
                        retry_after=self._circuit_open_until - now,
                    )
                # Open window expired — this caller becomes the probe.
                self._circuit_state = "half_open"
                logger.info("Kufar circuit breaker → half_open (probing)")
                return True
            if self._circuit_state == "half_open":
                # Another caller already owns the probe; don't pile on.
                logger.warning("Kufar circuit breaker half_open; rejecting query=%s", query)
                raise KufarUnavailableError("Kufar circuit breaker is probing upstream")
            return False

    async def _record_success(self) -> None:
        # Success — reset error counter under the lock so a concurrent
        # failure path can't observe an old (high) counter mid-update.
        # SEC-06: also close the breaker if we were the half-open probe.
        async with self._circuit_lock:
            self._consecutive_errors = 0
            if self._circuit_state != "closed":
                logger.info("Kufar circuit breaker → closed (was %s)", self._circuit_state)
            self._circuit_state = "closed"
            self._circuit_open_until = 0.0

    async def _record_failure(self) -> bool:
        """Count one retryable failure; True when the breaker is open afterwards."""
        # Read-modify-write of the error counter MUST be atomic vs. a
        # concurrent success-reset; otherwise two failures racing with a
        # success could either overshoot the threshold or never reach it.
        async with self._circuit_lock:
            self._consecutive_errors += 1
            if self._circuit_state == "half_open":
                # SEC-06: half-open probe failed → re-open for another
                # full window so we don't immediately send another probe.
                logger.warning("Kufar circuit breaker → open (half_open probe failed)")
                self._open_circuit()
            elif self._consecutive_errors >= CIRCUIT_FAILURE_THRESHOLD:
                if self._circuit_state != "open":
                    logger.error(
                        "Kufar circuit breaker → open after %s consecutive errors",
                        self._consecutive_errors,
                    )
                self._open_circuit()
            return self._circuit_state == "open"

    async def _record_rejection(self, *, is_probe: bool) -> None:
        # RV-BE-05: a 4xx proves Kufar is reachable, so it settles a
        # half-open probe; otherwise it leaves the counter alone, or one
        # user's malformed queries could trip (or keep resetting) the
        # breaker that every other request shares.
        if not is_probe:
            return
        async with self._circuit_lock:
            if self._circuit_state == "half_open":
                logger.info("Kufar circuit breaker → closed (probe answered with 4xx)")
                self._consecutive_errors = 0
                self._circuit_state = "closed"
                self._circuit_open_until = 0.0

    def _open_circuit(self) -> None:
        self._circuit_state = "open"
        self._circuit_open_until = asyncio.get_running_loop().time() + CIRCUIT_OPEN_SECONDS

    def _abandon_probe(self) -> None:
        # RV-BE-05: the probe finished without an outcome (cancelled
        # request, unexpected error). Left alone the breaker would stay
        # half_open forever and every call would fail fast — the AI-02
        # failure mode. Re-open with an expired window so the next caller
        # probes again. Synchronous on purpose: it runs from ``finally``,
        # including during task cancellation.
        if self._circuit_state == "half_open":
            self._circuit_state = "open"
            self._circuit_open_until = 0.0

    async def search(
        self,
        query: str,
        size: int = 200,
        currency: str = "USD",
        sort: str = "lst.d",
        cursor: str | None = None,
        region: int | None = None,
        area: int | None = None,
        condition: str | None = None,
        seller_type: str | None = None,
        price_range: str | None = None,
        category: int | None = None,
        bypass_delay: bool = False,
    ) -> dict[str, Any]:
        is_probe = await self._enter_circuit(query)

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
        if area is not None:
            params["ar"] = area
        if condition:
            params["cnd"] = KUFAR_CONDITION_VALUES.get(condition, condition)
        if seller_type:
            seller_value = KUFAR_SELLER_VALUES.get(seller_type, seller_type)
            if seller_value in {"0", "1"}:
                params["cmp"] = seller_value
        if price_range:
            params["prc"] = price_range
            # BE-15: Kufar reads ``prc`` in the ``cur`` currency, but the
            # range is always built from BYN (kufar_price_range). The API
            # only consumes ``price_byn`` from the response, so asking in
            # BYN changes nothing else.
            params["cur"] = "BYN"
        if category is not None:
            params["cat"] = category

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.kufar.by/",
        }

        last_error: Exception | None = None
        attempts = 0
        settled = False
        try:
            for attempt in range(MAX_RETRIES):
                if attempt and self._circuit_state != "closed":
                    # RV-BE-05: the breaker tripped while we were backing
                    # off; more attempts would only add load to a failing
                    # upstream.
                    raise KufarUnavailableError(
                        "Kufar circuit breaker opened while retrying",
                        retry_after=self._open_remaining(),
                    ) from last_error
                attempts += 1
                retry_after: float | None = None
                try:
                    if not bypass_delay:
                        await self._enforce_delay()
                    client = await self._get_client()
                    async with self._semaphore:
                        response = await client.get(
                            KUFAR_BASE_URL, params=params, headers=headers,
                        )
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    status_code = getattr(exc.response, "status_code", None)
                    if not _is_retryable_status(status_code):
                        await self._record_rejection(is_probe=is_probe)
                        settled = True
                        raise KufarAPIError(
                            f"Kufar API rejected the request with HTTP {status_code}"
                        ) from exc
                    last_error = exc
                    retry_after = _retry_after_seconds(exc.response)
                except (httpx.HTTPError, ValueError) as exc:
                    # ValueError: a 200 whose body is not JSON (an anti-bot
                    # or maintenance page) is an upstream failure, not data.
                    last_error = exc
                else:
                    await self._record_success()
                    settled = True
                    return payload

                breaker_open = await self._record_failure()
                settled = True
                logger.warning(
                    "Kufar request failed on attempt %s/%s for query=%s: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    query,
                    last_error,
                )
                if breaker_open or attempt == MAX_RETRIES - 1:
                    break
                delay = BACKOFF_BASE_SECONDS * (2**attempt)
                if retry_after is not None:
                    if retry_after > RETRY_AFTER_MAX_SECONDS:
                        raise KufarAPIError(
                            f"Kufar asked to retry after {retry_after:.0f}s",
                            retry_after=retry_after,
                        ) from last_error
                    delay = max(delay, retry_after)
                await asyncio.sleep(delay)
        finally:
            if is_probe and not settled:
                self._abandon_probe()

        raise KufarAPIError(
            f"Kufar API request failed after {attempts} attempt{'' if attempts == 1 else 's'}",
            retry_after=self._open_remaining(),
        ) from last_error

    async def search_all_ads(
        self,
        query: str,
        size: int = 200,
        currency: str = "USD",
        sort: str = "lst.d",
        region: int | None = None,
        area: int | None = None,
        condition: str | None = None,
        seller_type: str | None = None,
        price_range: str | None = None,
        category: int | None = None,
    ) -> dict[str, Any]:
        response = await self.search(
            query=query,
            size=size,
            currency=currency,
            sort=sort,
            region=region,
            area=area,
            condition=condition,
            seller_type=seller_type,
            price_range=price_range,
            category=category,
        )
        ads = list(response.get("ads", []))
        total = self.extract_total(response) or len(ads)
        cursor = self.extract_next_cursor(response)
        pages_fetched = 1
        seen_cursors: set[str] = set()
        seen_ids = {ad.get("ad_id") for ad in ads if isinstance(ad, dict)}

        # Safety cap: KUFAR_MAX_PAGES pages or configured max ads (whichever comes first)
        max_ads = self._settings.kufar_max_ads_per_query
        while (
            cursor and cursor not in seen_cursors
            and pages_fetched < KUFAR_MAX_PAGES and len(ads) < max_ads
        ):
            seen_cursors.add(cursor)
            page = await self.search(
                query=query,
                size=size,
                currency=currency,
                sort=sort,
                cursor=cursor,
                region=region,
                area=area,
                condition=condition,
                seller_type=seller_type,
                price_range=price_range,
                category=category,
            )
            for ad in page.get("ads", []):
                ad_id = ad.get("ad_id") if isinstance(ad, dict) else None
                if ad_id is not None and ad_id in seen_ids:
                    continue  # the feed shifted under us between pages
                seen_ids.add(ad_id)
                ads.append(ad)
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
        # RV2-BK-01: Kufar lists ``self`` (token null) first, then ``next``,
        # numbered pages, ``prev``/``first`` — only ``next`` advances.
        if not isinstance(pages, list):
            return None
        nxt = next((p for p in pages if isinstance(p, dict) and p.get("label") == "next"), None)
        return (nxt.get("token") or None) if nxt else None

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
