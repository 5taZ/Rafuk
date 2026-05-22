"""AI service — OpenAI-compatible API providers (Together AI, Gemini-compatible, etc.).

Configure via .env:
  AI_API_KEY=<key>
  AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
  AI_MODEL=gemini-2.5-flash

Wave 18 (BE-C5 follow-up) — this module used to be ~1700 lines with
most of the volume taken up by inline Russian prompt text and
payload-normalisation helpers. The original split of
``ai_analysis.py`` (Wave 10) established the pattern we repeat here:
pull out concerns into dedicated modules and keep the old import
surface alive via re-exports so callers don't have to move.

Sibling modules (keep the import surface stable):

* ``api.services.ai_prompts``   — prompt templates + JSON schemas
* ``api.services.ai_sanitize``  — sanitize_user_text + injection regex
* ``api.services.ai_dedupe``    — _dedupe_* / dedupe_analysis_payload
* ``api.services.ai_images``    — multimodal image fetch/compression helpers

Importers that depend on back-compat re-exports (do NOT drop any of
the symbols below without updating these):

* ``api.services.ai_analysis_pipeline`` — dedupe_analysis_payload,
  detect_category, get_ai_service, normalize_condition_label
* ``api.services.ai_marketplace``       — detect_category,
  normalize_condition_label
* ``api.routers.ai_analysis``           — get_ai_service (plus the
  late-bound monkeypatch sites anchored here in Wave 10)
* ``api.routers.ai_listing_assistant``  — CATEGORY_HINTS,
  detect_category, normalize_condition_label
* ``api.routers.ai_tools``              — sanitize_user_text
* ``api.main``                          — get_ai_service
* ``tests/test_ai_analysis.py``         — AIService, _clean_photo_notes,
  _normalize_condition_label, dedupe_analysis_payload, detect_category,
  sanitize_user_text, _repair_truncated_json
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
from typing import Any

import httpx

from api.config import get_settings
from api.metrics import observe_ai_provider_call
from api.services.ai_category_data import (  # noqa: F401 — re-export
    _VALID_CONDITION_LABELS,
    CATEGORY_HINTS,
    CATEGORY_KEYWORDS,
    _normalize_condition_label,
    detect_category,
    normalize_condition_label,
)
from api.services.ai_category_profiles import (
    buyer_category_profile_text,
    seller_category_profile_text,
)
from api.services.ai_costs import estimate_ai_cost_usd, extract_ai_usage
from api.services.ai_dedupe import (  # noqa: F401 — re-export
    _dedupe_dict_list,
    _dedupe_listing_payload,
    _dedupe_text_list,
    _is_paraphrase,
    _normalize_for_dedupe,
    dedupe_analysis_payload,
)
from api.services.ai_images import (
    MAX_AI_REDIRECTS,
    compress_image,
    fetch_image_b64,
    fetch_image_bytes,
    is_allowed_image_url,
)
from api.services.ai_prompts import (  # noqa: F401 — re-export
    _CONDITION_RISKS_PROMPT_TEMPLATE,
    _CONDITION_RISKS_SCHEMA,
    _PRICE_MARKET_PROMPT_TEMPLATE,
    _PRICE_MARKET_SCHEMA,
    LISTING_ASSISTANT_PROMPT,
    QUICK_CONDITION_PROMPT,
)
from api.services.ai_sanitize import (  # noqa: F401 — re-export
    _MULTILINE_COLLAPSE_RE,
    _PROMPT_INJECTION_PATTERNS,
    _PROMPT_ROLE_MARKERS,
    _TRIPLE_BACKTICK_RE,
    sanitize_user_text,
    scrub_pii,
)

logger = logging.getLogger(__name__)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After response header into a delay in seconds.

    SEC-09: per RFC 7231 §7.1.3 a Retry-After header can be either an
    HTTP-date OR a delta-seconds integer. Together AI and Gemini both
    emit the integer form, but we parse both to be robust against
    proxy quirks. Returns None when the header is absent, malformed,
    or specifies a clearly bogus delay (negative, or absurdly large).
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    # Integer / float seconds.
    try:
        seconds = float(value)
    except ValueError:
        seconds = None
    if seconds is not None:
        if seconds < 0 or seconds > 600:
            # Treat anything >10 minutes as a probable typo / provider
            # quirk and fall back to our own backoff schedule.
            return None
        return seconds
    # HTTP-date — RFC 1123 or RFC 850. The stdlib helper handles both.
    from email.utils import parsedate_to_datetime
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - now).total_seconds()
    if delta < 0 or delta > 600:
        return None
    return delta


def _is_rate_limit_exception(exc: BaseException) -> bool:
    text = str(exc).upper()
    return "429" in text or "RATE_LIMIT" in text or "RESOURCE_EXHAUSTED" in text


def _entry_price_guidance(
    *,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    similar_listings: list[dict] | None,
) -> tuple[int, int] | None:
    priced = [
        float(item.get("price_byn") or 0)
        for item in (similar_listings or [])
        if float(item.get("price_byn") or 0) > 0
    ]
    priced = sorted(priced)

    low_anchor = market_q1 if market_q1 and market_q1 > 0 else (priced[0] if priced else None)
    high_anchor = market_median if market_median and market_median > 0 else None
    if high_anchor is None and priced:
        high_anchor = priced[min(len(priced) - 1, max(0, len(priced) // 2))]
    if low_anchor is None and market_q3 and market_q3 > 0:
        low_anchor = market_q3 * 0.9
    if high_anchor is None and market_q3 and market_q3 > 0:
        high_anchor = market_q3

    if low_anchor is None or high_anchor is None:
        return None
    if high_anchor < low_anchor:
        low_anchor, high_anchor = high_anchor, low_anchor

    return int(round(low_anchor)), int(round(high_anchor))


def _clean_photo_notes(notes: list[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in notes or []:
        text = re.sub(r"\s+", " ", str(item or "").strip(" .;"))
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"заметка1", "заметка 1", "note1", "note 1"}:
            continue
        if "|" in text and any(token in lowered for token in _VALID_CONDITION_LABELS):
            continue
        key = lowered
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned[:4]


def _repair_truncated_json(text: str) -> dict:
    """Try to repair a truncated JSON response from the AI model.

    When max_tokens cuts off the response mid-JSON, we try to close
    open braces/brackets and parse what we have, preserving as many
    sections as possible.
    """
    start = text.find("{")
    if start == -1:
        return {"summary": text.strip()[:500], "condition": None, "fair_price": None}

    fragment = text[start:]

    # Count open braces/brackets and close them
    open_braces = 0
    open_brackets = 0
    in_string = False
    escape_next = False
    for ch in fragment:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        # C3: removed dead `and not escape_next` guard — escape_next is always
        # False here because the block above consumes it and continues.
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            open_braces += 1
        elif ch == "}":
            open_braces -= 1
        elif ch == "[":
            open_brackets += 1
        elif ch == "]":
            open_brackets -= 1

    # Close any open string
    if in_string:
        fragment += '"'
    # Strip trailing incomplete key/value pairs to make repair viable.
    # After closing the open string we may have: ..., "red_fla"  or
    # ..., "key": 123  without closing brace.  Try progressively
    # aggressive cleanup so we preserve as many valid keys as possible.
    for pattern in (
        r',\s*"[^"]*"\s*:\s*$',            # trailing "key": (no value)
        r',\s*"[^"]*"\s*$',                # trailing "key" (no colon)
        r',\s*"[^"]*$',                     # trailing "incomplete_key
        r':\s*[^}\]"\dtruefalsenul.+-]+\s*$',  # trailing : garbage
    ):
        candidate = re.sub(pattern, "", fragment)
        if candidate != fragment:
            fragment = candidate
            break
    # Close open brackets and braces
    fragment += "]" * max(0, open_brackets)
    fragment += "}" * max(0, open_braces)

    try:
        result = json.loads(fragment)
        logger.info("Repaired truncated JSON: recovered keys=%s", list(result.keys()))
        return result
    except json.JSONDecodeError:
        logger.warning("Could not repair truncated JSON")
        return {"summary": text.strip()[:500], "condition": None, "fair_price": None}


# SEC-05: AI-provider circuit breaker constants. Once the provider has
# returned a hard error N times in a row (after _post_with_retry has
# already done its 3-attempt budget per call), we stop trying for
# AI_CB_OPEN_SECONDS. During that window every new chat call short-
# circuits with a fast RuntimeError so the user's analyse task can
# fall back to its non-AI degraded result instead of waiting on the
# 45s read timeout × 3 retries.
#
# After AI_CB_OPEN_SECONDS we move to half-open: exactly one probe
# request is allowed through. If it succeeds we close the circuit,
# if it fails we re-open for another window.
_AI_CB_THRESHOLD = 4
_AI_CB_OPEN_SECONDS = 60.0


class AIService:
    """AI service using OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.ai_api_key
        # OPUS-5: ``or`` fallback aligns with the Settings default —
        # Google Gemini OpenAI-compat — so an empty AI_BASE_URL
        # doesn't silently route to a provider that can't serve the
        # default model.
        self._base_url = (
            settings.ai_base_url or "https://generativelanguage.googleapis.com/v1beta/openai"
        ).rstrip("/")
        self._model = settings.ai_model or "gemini-2.5-flash"
        self._analysis_model = settings.ai_analysis_model or self._model
        self._listing_assistant_model = (
            settings.ai_listing_assistant_model
            or self._default_listing_assistant_model(self._base_url, self._model)
        )
        self._max_images = settings.ai_max_images
        self._proxy_url = settings.ai_proxy_url
        self._chat_min_interval_seconds = max(
            0.0, float(getattr(settings, "ai_chat_min_interval_seconds", 1.5) or 0.0)
        )
        self._httpx_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._chat_lock: asyncio.Lock | None = None
        self._chat_lock_loop: asyncio.AbstractEventLoop | None = None
        self._last_chat_started_at = 0.0
        # SEC-05: simple per-process circuit breaker. State lives on
        # the singleton AIService instance (returned by get_ai_service).
        # _cb_state ∈ {"closed", "open", "half_open"}.
        #   closed     — normal operation
        #   open       — short-circuit every call until _cb_open_until
        #   half_open  — exactly one probe allowed; success → closed,
        #                failure → open again
        self._cb_state: str = "closed"
        self._cb_consecutive_errors = 0
        self._cb_open_until: float = 0.0
        self._cb_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._api_key is not None

    @property
    def analysis_model(self) -> str:
        return self._analysis_model

    @property
    def listing_assistant_model(self) -> str:
        return self._listing_assistant_model

    async def _get_client(self) -> httpx.AsyncClient:
        if self._httpx_client is None or self._httpx_client.is_closed:
            async with self._client_lock:
                if self._httpx_client is None or self._httpx_client.is_closed:
                    kwargs: dict = {
                        # PERF: pool=20 was 10 — under concurrent users the
                        # parallel sub-calls in ``analyze_listing_parallel``
                        # plus the listing-assistant single-shot can race for
                        # connections; a longer pool-acquire timeout avoids
                        # spurious failures while we wait for an idle slot.
                        "timeout": httpx.Timeout(connect=15, read=45, write=20, pool=20),
                        "max_redirects": MAX_AI_REDIRECTS,
                        # PERF: doubled both caps so a single analysis
                        # (2 sub-calls) plus listing-assistant traffic
                        # don't queue on the connection pool.
                        "limits": httpx.Limits(max_connections=40, max_keepalive_connections=20),
                    }
                    if self._proxy_url:
                        kwargs["proxy"] = self._proxy_url
                    self._httpx_client = httpx.AsyncClient(**kwargs)
        return self._httpx_client

    @property
    def _is_gemini(self) -> bool:
        """Gemini models served via Google's OpenAI-compatible endpoint."""
        return self._is_gemini_model(self._model)

    def _is_gemini_model(self, model: str) -> bool:
        return (
            (model or "").lower().startswith("gemini")
            or "generativelanguage.googleapis.com" in self._base_url
        )

    @staticmethod
    def _default_listing_assistant_model(base_url: str, model: str) -> str:
        if (
            (model or "").strip().lower() == "gemini-2.5-flash"
            and "generativelanguage.googleapis.com" in (base_url or "")
        ):
            return "gemini-2.5-flash-lite"
        return model

    @property
    def _is_gemma_legacy(self) -> bool:
        """Legacy Gemma 4 / Gemma 3 chat-completion via Together AI.

        These models put output in `reasoning` instead of `content` when
        `response_format` is set, so we have to leave it off and parse
        whatever shows up.
        """
        model = self._model.lower()
        return model.startswith(("google/gemma-", "google/gemma_")) or "gemma-3n" in model

    def _get_chat_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._chat_lock is None or self._chat_lock_loop is not loop:
            self._chat_lock = asyncio.Lock()
            self._chat_lock_loop = loop
            self._last_chat_started_at = 0.0
        return self._chat_lock

    async def _cb_check_and_acquire(self) -> None:
        """Raise RuntimeError if the breaker is open; allow probe in half-open.

        SEC-05: called BEFORE every AI HTTP call.
          * closed     → return immediately (normal path).
          * open       → raise if open-until not yet elapsed;
                         otherwise transition to half-open and let
                         exactly THIS caller through as the probe.
          * half_open  → another caller already owns the probe;
                         short-circuit until they finish.

        The state transitions out of half_open are owned by
        _cb_record_success / _cb_record_failure.
        """
        async with self._cb_lock:
            if self._cb_state == "open":
                now = asyncio.get_running_loop().time()
                if now < self._cb_open_until:
                    raise RuntimeError("AI circuit breaker open")
                # Open window expired — this caller owns the probe.
                self._cb_state = "half_open"
                logger.info("AI circuit breaker → half_open (probing)")
                return
            if self._cb_state == "half_open":
                # Another caller is mid-probe; don't pile on.
                raise RuntimeError(
                    "AI circuit breaker half_open (probe in flight)"
                )

    async def _cb_record_success(self) -> None:
        async with self._cb_lock:
            if self._cb_state != "closed":
                logger.info(
                    "AI circuit breaker → closed (was %s)", self._cb_state,
                )
            self._cb_state = "closed"
            self._cb_consecutive_errors = 0
            self._cb_open_until = 0.0

    async def _cb_record_failure(self) -> None:
        async with self._cb_lock:
            self._cb_consecutive_errors += 1
            if self._cb_state == "half_open":
                # Half-open probe failed → re-open for another window.
                self._cb_state = "open"
                self._cb_open_until = (
                    asyncio.get_running_loop().time() + _AI_CB_OPEN_SECONDS
                )
                logger.warning(
                    "AI circuit breaker → open (half-open probe failed)",
                )
                return
            if (
                self._cb_state == "closed"
                and self._cb_consecutive_errors >= _AI_CB_THRESHOLD
            ):
                self._cb_state = "open"
                self._cb_open_until = (
                    asyncio.get_running_loop().time() + _AI_CB_OPEN_SECONDS
                )
                logger.warning(
                    "AI circuit breaker → open (%d consecutive errors)",
                    self._cb_consecutive_errors,
                )

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str],
        json: dict,
    ) -> httpx.Response:
        """POST with bounded exponential-backoff retry on transient AI failures.

        SEC-04 / SEC-09: previously a single 429 / 5xx response was
        propagated straight to the user as a generic error. Together AI
        and Gemini both routinely emit short-lived 429s during traffic
        bursts and the OpenAI-compatible APIs always carry a
        ``Retry-After`` hint — we should honour it instead of failing
        on the first transient blip.

        The retry budget is intentionally small (3 attempts total) so
        a permanently-degraded provider still surfaces inside the AI
        analysis timeout window (90-150s end-to-end). The circuit
        breaker (_cb_*) short-circuits the retry loop entirely when
        the provider has been down for a while — see SEC-05.
        """
        # SEC-05: check the breaker BEFORE we even open a socket.
        await self._cb_check_and_acquire()

        # Status codes that justify a retry — everything else short-
        # circuits straight back. 408 = client read timeout (rare here,
        # but cheap to treat the same). 5xx = upstream blip. 429 = rate
        # limited; we honour Retry-After when supplied.
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        max_attempts = 3
        base_delay = 1.0  # seconds — multiplied by 2**attempt
        max_delay = 8.0
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = await client.post(url, headers=headers, json=json)
            except httpx.HTTPError as e:
                last_exc = e
                logger.warning(
                    "AI _chat attempt %d/%d failed: %s: %s",
                    attempt + 1, max_attempts, type(e).__name__, e,
                )
                if attempt + 1 >= max_attempts:
                    await self._cb_record_failure()
                    raise
                delay = min(max_delay, base_delay * (2 ** attempt))
                await asyncio.sleep(delay)
                continue

            if resp.status_code in retryable_statuses and attempt + 1 < max_attempts:
                retry_after = _parse_retry_after(
                    resp.headers.get("retry-after")
                )
                delay = retry_after if retry_after is not None else min(
                    max_delay, base_delay * (2 ** attempt)
                )
                logger.warning(
                    "AI _chat got %d on attempt %d/%d, sleeping %.1fs",
                    resp.status_code, attempt + 1, max_attempts, delay,
                )
                await asyncio.sleep(delay)
                continue

            # Terminal mapping — these used to live inline in _chat;
            # keeping the surface stable so callers' RuntimeError
            # handlers still match.
            if resp.status_code == 429:
                await self._cb_record_failure()
                raise RuntimeError("429 RATE_LIMITED")
            if resp.status_code == 402:
                # Billing failures aren't a "broken provider" — don't
                # trip the breaker, but propagate so the analyse path
                # can fall back to non-AI output.
                raise RuntimeError("Insufficient balance")
            if resp.status_code >= 500:
                await self._cb_record_failure()
                resp.raise_for_status()
            elif resp.status_code >= 400:
                # 4xx other than 429/402 — bad request, model name
                # typo, schema mismatch. Don't flip the breaker for
                # caller bugs.
                resp.raise_for_status()
            logger.info(
                "AI _chat response: status=%d (attempt %d)",
                resp.status_code, attempt + 1,
            )
            await self._cb_record_success()
            return resp

        # Loop exhausted with only retryable exceptions caught above —
        # re-raise the last one if we have it. The status-code branch
        # explicitly raises inside the loop on the final attempt.
        await self._cb_record_failure()
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("AI _chat retries exhausted")

    async def _chat(
        self,
        *,
        system: str,
        content: list[dict] | str,
        max_tokens: int = 1200,
        reasoning_effort: str | None = None,
        model: str | None = None,
        operation: str = "chat",
    ) -> dict:
        """Call OpenAI-compatible /chat/completions endpoint."""
        request_model = model or self._model
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        body: dict = {
            "model": request_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        # Gemini 2.5 Flash/Pro spend output budget on internal "thinking"
        # tokens before producing the JSON. `reasoning_effort: "medium"`
        # gives enough depth for grounded price comparisons and condition
        # analysis without the 30-60s latency of "high".
        #
        # max_tokens=3200/4000 for analysis, 4800 for listing assistant
        # (larger JSON output: title + description + 3 pricing tiers +
        # negotiation playbook + competitors + photo tips).
        # At medium effort, thinking uses ~300-600 tokens, leaving
        # ~4200+ for the JSON payload — sufficient for all sections.
        if self._is_gemini_model(request_model):
            body["reasoning_effort"] = reasoning_effort or "medium"
            # Gemini honours response_format properly — ask for JSON to
            # cut down on stray markdown fences and prose around the JSON.
            body["response_format"] = {"type": "json_object"}
        api_key = self._api_key.get_secret_value() if self._api_key else ""
        client = await self._get_client()
        logger.info(
            "AI _chat: model=%s, base_url=%s, proxy=%s, content_parts=%d",
            request_model,
            self._base_url,
            "yes" if self._proxy_url else "no",
            len(content) if isinstance(content, list) else 1,
        )
        async with self._get_chat_lock():
            if self._chat_min_interval_seconds > 0:
                loop = asyncio.get_running_loop()
                wait_s = (
                    self._last_chat_started_at
                    + self._chat_min_interval_seconds
                    - loop.time()
                )
                if wait_s > 0:
                    logger.info("AI _chat throttle: sleeping %.1fs before provider call", wait_s)
                    await asyncio.sleep(wait_s)
                self._last_chat_started_at = loop.time()
        try:
            resp = await self._post_with_retry(
                client,
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except Exception:
            observe_ai_provider_call(
                endpoint=operation,
                model=request_model,
                status="error",
            )
            raise
        data = resp.json()
        usage = extract_ai_usage(data)
        observe_ai_provider_call(
            endpoint=operation,
            model=request_model,
            status="success",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            thinking_tokens=usage.thinking_tokens,
            estimated_cost_usd=estimate_ai_cost_usd(request_model, usage),
        )
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"AI returned empty choices (status={resp.status_code})")
        choice = choices[0]
        finish_reason = choice.get("finish_reason", "")
        message = choice.get("message")
        if not message:
            raise RuntimeError("AI returned no message in choice")
        if finish_reason == "length":
            logger.warning(
                "AI _chat: output truncated (finish_reason=length, max_tokens=%d)",
                max_tokens,
            )
        # Some models (Gemma 4 on Together) return output in `reasoning`
        # field while `content` is empty — handle both.
        text = message.get("content") or ""
        if not text.strip() and message.get("reasoning"):
            logger.info(
                "AI _chat: content empty, using reasoning field (%d chars)",
                len(message["reasoning"]),
            )
            # Reasoning may contain the JSON at the end after the thought chain
            reasoning = message["reasoning"]
            # Try to extract JSON from reasoning
            text = reasoning
        return self._parse_json(text)

    # ── Public methods ──────────────────────────────────────────

    async def analyze_listing_parallel(
        self,
        *,
        title: str,
        description: str | None,
        price_byn: float,
        is_negotiable_price: bool = False,
        condition: str | None,
        parameters: list[dict],
        market_median: float | None,
        market_count: int,
        similar_listings: list[dict] | None = None,
        market_q1: float | None = None,
        market_q3: float | None = None,
        market_min: float | None = None,
        market_max: float | None = None,
        seller_type: str | None = None,
        photo_count: int = 0,
        listing_age_days: int | None = None,
        risk_context_summary: str | None = None,
        risk_context_flags: list[str] | None = None,
        anomaly_flags: list[str] | None = None,
        deal_score: float | None = None,
        deal_verdict: str | None = None,
        photo_condition_label: str | None = None,
        photo_condition_notes: list[str] | None = None,
        image_urls: list[str] | None = None,
    ) -> dict:
        """Parallel AI analysis — splits work into 2 concurrent sub-calls.

        Call A (Price & Market): fair_price, resale_potential, market_context,
            negotiation_tips, best_pick
        Call B (Condition & Risks): condition, watch_out, meeting_checklist,
            red_flags, recommendation, summary

        Both calls share the same listing context (text + images) and produce
        different JSON sections, using 3200/4000 max_tokens for Gemini 2.5
        Flash. Running concurrently means wall-clock time ≈ max(A, B), not A+B.
        """
        category = detect_category(title, parameters)
        hints = CATEGORY_HINTS.get(category, CATEGORY_HINTS["default"])
        price_position_label = self._price_position_label(
            price_byn,
            market_median,
            market_q1,
            market_q3,
            is_negotiable_price=is_negotiable_price,
        )
        negotiable_hint = (
            "Цена в объявлении указана как договорная. "
            "Не считай, что цена покупки равна 0 BYN. "
            "Опирайся на рыночный диапазон и похожие объявления."
            if is_negotiable_price
            else ""
        )

        # Build shared context once
        context = self._build_listing_context(
            title=title,
            description=description,
            price_byn=price_byn,
            is_negotiable_price=is_negotiable_price,
            condition=condition,
            parameters=parameters,
            market_median=market_median,
            market_count=market_count,
            market_q1=market_q1,
            market_q3=market_q3,
            market_min=market_min,
            market_max=market_max,
            seller_type=seller_type,
            photo_count=photo_count,
            similar_listings=similar_listings,
            listing_age_days=listing_age_days,
            risk_context_summary=risk_context_summary,
            risk_context_flags=risk_context_flags,
            anomaly_flags=anomaly_flags,
            deal_score=deal_score,
            deal_verdict=deal_verdict,
            photo_condition_label=photo_condition_label,
            photo_condition_notes=photo_condition_notes,
        )

        # Fetch listing images for multimodal analysis (scam detection,
        # photo authenticity, condition assessment). Fetched in parallel
        # to minimise latency before the two AI sub-calls start.
        image_content: list[dict] = []
        if image_urls:
            fetch_tasks = [
                self._fetch_image_b64(url) for url in image_urls[:self._max_images]
            ]
            fetched = await asyncio.gather(*fetch_tasks)
            for img in fetched:
                if img:
                    image_content.append(img)
            if image_content:
                logger.info(
                    "AI analyze: fetched %d/%d images for multimodal analysis",
                    len(image_content),
                    len(image_urls),
                )

        # Build multimodal content: text + images (when available)
        if image_content:
            call_content: list[dict] | str = [
                {"type": "text", "text": context},
                *image_content,
            ]
        else:
            call_content = context

        # Build system prompts for each sub-call
        system_a = (
            _PRICE_MARKET_PROMPT_TEMPLATE.format(
                category_hints=hints["category_hints"],
                bargain_hint=hints["bargain_hint"],
                price_position_label=price_position_label,
            )
            + "\n"
            + negotiable_hint
            + "\n"
            + _PRICE_MARKET_SCHEMA
        )
        system_b = (
            _CONDITION_RISKS_PROMPT_TEMPLATE.format(
                category_hints=hints["category_hints"],
                bargain_hint=hints["bargain_hint"],
                price_position_label=price_position_label,
            )
            + "\n"
            + negotiable_hint
            + "\n"
            + _CONDITION_RISKS_SCHEMA
        )

        # Gemini 2.5 Flash with reasoning_effort="medium" uses ~300-600 thinking
        # tokens. Each sub-call needs enough room for thinking + ~5-6 JSON
        # sections. 3200/4000 tokens per call gives Flash room to reason
        # through price comparisons without truncating the JSON output.
        # (The old 2200/2800 values were tuned for Gemma 4 which had no
        # thinking tokens but was prone to empty content with response_format.)
        # BE-H5 note: the 1-second stagger is implemented as
        # `asyncio.sleep` rather than time.sleep, so it does NOT block
        # the event loop — it just delays Call B's start. While B's
        # coroutine waits, Call A's HTTP request is already in flight.
        # `asyncio.gather` creates tasks for both coroutines under the
        # hood; we don't need a separate `create_task` wrapper.
        async def _run_subcalls(content: list[dict] | str):
            async def _call_a():
                return await self._chat(
                    system=system_a,
                    content=content,
                    max_tokens=3200,
                    model=self._analysis_model,
                    operation="analysis_price_market",
                )

            async def _call_b():
                # Historically we slept 1.0s here to dodge Together AI's
                # concurrent-request 429s. The current provider (Gemini
                # 2.5 Flash via Google's OpenAI-compat endpoint) is happy
                # with concurrent calls, and the global ``_chat_lock`` +
                # ``_chat_min_interval_seconds`` already enforce a small
                # spacing between LLM calls. Dropping the explicit sleep
                # cuts ~1s off every analysis without changing behavior
                # for callers.
                # Call B includes scam_analysis + photo_authenticity — needs more tokens
                return await self._chat(
                    system=system_b,
                    content=content,
                    max_tokens=4000,
                    model=self._analysis_model,
                    operation="analysis_condition_risks",
                )

            return await asyncio.gather(_call_a(), _call_b(), return_exceptions=True)

        logger.warning("AI analyze_listing_parallel: starting staggered sub-calls")
        # Use return_exceptions so one failure doesn't kill the other
        results = await _run_subcalls(call_content)
        if (
            image_content
            and isinstance(results[0], Exception)
            and isinstance(results[1], Exception)
            and _is_rate_limit_exception(results[0])
            and _is_rate_limit_exception(results[1])
        ):
            logger.warning("AI multimodal analyse rate-limited; retrying text-only")
            results = await _run_subcalls(context)

        result_a = results[0] if not isinstance(results[0], Exception) else {}
        result_b = results[1] if not isinstance(results[1], Exception) else {}
        if isinstance(results[0], Exception):
            logger.warning(
                "AI parallel Call A failed: %s: %s",
                type(results[0]).__name__,
                results[0],
            )
        if isinstance(results[1], Exception):
            logger.warning(
                "AI parallel Call B failed: %s: %s",
                type(results[1]).__name__,
                results[1],
            )
        # AI-08 / Wave 29: when both staggered calls fail there is a
        # single failure mode worth grepping for — surface it as one
        # structured log line so on-call sees a single AI_DUAL_FAIL
        # signature instead of having to correlate two per-call
        # WARN lines. Carries both error types and a one-line
        # signature for each so we can tell apart e.g. "both 429"
        # (Together rate-limit) from "A=ReadTimeout, B=BillingError".
        if isinstance(results[0], Exception) and isinstance(results[1], Exception):
            logger.error(
                "AI_DUAL_FAIL parallel analyse: call_a=%s(%s) call_b=%s(%s)",
                type(results[0]).__name__,
                str(results[0])[:200],
                type(results[1]).__name__,
                str(results[1])[:200],
            )
            # Re-raise the first error so the router fallback path kicks in
            raise results[0]

        # Merge: Call B sections take priority for overlapping keys,
        # Call A sections fill in the rest.
        merged: dict = {}
        # fair_price, resale_potential, negotiation_tips, market_context, best_pick
        merged.update(result_a)
        # condition, watch_out, meeting_checklist, red_flags, recommendation, summary
        merged.update(result_b)
        # Ensure all expected keys exist even if a sub-call returned partial data
        merged.setdefault("fair_price", None)
        merged.setdefault("resale_potential", None)
        merged.setdefault("negotiation_tips", [])
        merged.setdefault("market_context", "")
        merged.setdefault("best_pick", {"ad_id": None, "reason": ""})
        merged.setdefault("condition", None)
        merged.setdefault("watch_out", [])
        merged.setdefault("meeting_checklist", [])
        merged.setdefault("red_flags", [])
        merged.setdefault("scam_analysis", None)
        merged.setdefault("photo_authenticity", None)
        merged.setdefault("recommendation", None)
        merged.setdefault("summary", "")
        return dedupe_analysis_payload(merged)

    def _build_listing_assistant_context(
        self,
        *,
        title: str,
        condition: str | None,
        is_negotiable: bool,
        draft_price_byn: float | None,
        extra_notes: str | None,
        market_median: float | None,
        market_q1: float | None,
        market_q3: float | None,
        market_min: float | None,
        market_max: float | None,
        market_count: int,
        similar_listings: list[dict] | None,
        category_hint: str | None,
        category_bargain_hint: str | None,
    ) -> str:
        safe_title = sanitize_user_text(title, max_length=200) or ""
        safe_condition = sanitize_user_text(condition, max_length=64) if condition else None
        safe_notes = sanitize_user_text(extra_notes, max_length=600) if extra_notes else None

        ctx_lines: list[str] = [f"## ТОВАР: {safe_title}"]
        if safe_condition:
            ctx_lines.append(f"Состояние (как видит продавец): {safe_condition}")
        if draft_price_byn and draft_price_byn > 0:
            ctx_lines.append(f"Черновая цена продавца: {int(round(draft_price_byn))} BYN")
        elif is_negotiable:
            ctx_lines.append("Цена черновая: договорная")
        if safe_notes:
            ctx_lines.append(f"Заметки продавца: {safe_notes}")

        category_profile = seller_category_profile_text(
            safe_title,
            [{"label": "condition", "value": safe_condition}] if safe_condition else [],
        )
        if category_profile:
            ctx_lines.append("")
            ctx_lines.append(category_profile)

        ctx_lines.append("")
        ctx_lines.append("## РЫНОК (Kufar.by, BYN)")
        if market_median:
            ctx_lines.append(f"Медиана: {market_median:.0f}")
        if market_q1 and market_q3:
            ctx_lines.append(f"Q1-Q3: {market_q1:.0f}-{market_q3:.0f}")
        if market_min is not None and market_max is not None:
            ctx_lines.append(f"Min-Max: {market_min:.0f}-{market_max:.0f}")
        ctx_lines.append(f"Количество объявлений в выборке: {market_count}")

        if similar_listings:
            ctx_lines.append("")
            ctx_lines.append("## ТОП КОНКУРЕНТОВ (до 6)")
            for item in similar_listings[:6]:
                price = item.get("price_byn") or 0
                cond = item.get("condition") or item.get("condition_label") or ""
                seller = item.get("seller_type") or ""
                title_text = sanitize_user_text(
                    (item.get("title") or "").strip().replace("\n", " "),
                    max_length=120,
                ) or ""
                params_list = item.get("parameters") or []
                bits: list[str] = []
                if price:
                    bits.append(f"{int(round(float(price)))} BYN")
                if cond:
                    bits.append(str(cond))
                if seller:
                    bits.append(str(seller))
                if params_list:
                    bits.append(", ".join(str(p) for p in params_list))
                meta = " · ".join(bits)
                if title_text:
                    ctx_lines.append(f"- {title_text} ({meta})" if meta else f"- {title_text}")
                elif meta:
                    ctx_lines.append(f"- {meta}")

        if category_hint:
            ctx_lines.append("")
            ctx_lines.append("## КАТЕГОРИЯ-ПОДСКАЗКИ")
            ctx_lines.append(category_hint)
        if category_bargain_hint:
            ctx_lines.append("")
            ctx_lines.append("## АРГУМЕНТЫ ТОРГА (категория)")
            ctx_lines.append(category_bargain_hint)

        return "\n".join(ctx_lines)

    async def generate_listing(
        self,
        *,
        title: str,
        condition: str | None,
        is_negotiable: bool,
        draft_price_byn: float | None,
        extra_notes: str | None,
        market_median: float | None,
        market_q1: float | None,
        market_q3: float | None,
        market_min: float | None,
        market_max: float | None,
        market_count: int,
        similar_listings: list[dict] | None,
        category_hint: str | None,
        category_bargain_hint: str | None,
        photo_data_urls: list[str] | None = None,
    ) -> dict:
        """Generate seller-side listing draft (title, description, pricing, playbook).

        The prompt receives concrete market anchors (median + Q1/Q3) so the
        model can produce numerically-grounded fast/market/patient tiers
        instead of guesses.
        """

        user_text = self._build_listing_assistant_context(
            title=title,
            condition=condition,
            is_negotiable=is_negotiable,
            draft_price_byn=draft_price_byn,
            extra_notes=extra_notes,
            market_median=market_median,
            market_q1=market_q1,
            market_q3=market_q3,
            market_min=market_min,
            market_max=market_max,
            market_count=market_count,
            similar_listings=similar_listings,
            category_hint=category_hint,
            category_bargain_hint=category_bargain_hint,
        )

        photos = [
            url
            for url in (photo_data_urls or [])
            if isinstance(url, str) and url.startswith("data:image/")
        ][: self._max_images]

        if photos:
            content: list[dict] = [{"type": "text", "text": user_text}]
            for data_url in photos:
                content.append({"type": "image_url", "image_url": {"url": data_url}})
            result = await self._chat(
                system=LISTING_ASSISTANT_PROMPT,
                content=content,
                max_tokens=4800,
                reasoning_effort="medium",
                model=self._listing_assistant_model,
                operation="listing_assistant",
            )
            return _dedupe_listing_payload(result)

        result = await self._chat(
            system=LISTING_ASSISTANT_PROMPT,
            content=user_text,
            max_tokens=4800,
            reasoning_effort="medium",
            model=self._listing_assistant_model,
            operation="listing_assistant",
        )
        return _dedupe_listing_payload(result)

    async def quick_condition(self, image_urls: list[str]) -> dict:
        """Quick condition assessment from photos only."""
        content: list[dict] = [
            {"type": "text", "text": "Оцени состояние товара на фото."},
        ]
        for url in image_urls[: self._max_images]:
            img = await self._fetch_image_b64(url)
            if img:
                content.append(img)
        if len(content) == 1:
            raise ValueError("Не удалось загрузить фото для анализа")
        result = await self._chat(
            system=QUICK_CONDITION_PROMPT,
            content=content,
            max_tokens=1200,
            model=self._analysis_model,
            operation="quick_condition",
        )
        notes = _clean_photo_notes(result.get("notes"))
        return {
            "condition": normalize_condition_label(result.get("condition")),
            # Dedupe in case the AI rephrased the same observation
            # twice (e.g. "лёгкие потёртости" + "лёгкие потёртости на
            # корпусе"). Model-agnostic — applies to both Gemini and
            # the Gemma fallback.
            "notes": _dedupe_text_list(notes) if isinstance(notes, list) else notes,
        }

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _price_position_label(
        price: float,
        median: float | None,
        q1: float | None,
        q3: float | None,
        *,
        is_negotiable_price: bool = False,
    ) -> str:
        if is_negotiable_price:
            if median:
                return "не указана продавцом; ориентир — рыночная медиана"
            return "не указана продавцом"
        if not median:
            return "неизвестна"
        if q1 and q3:
            if price <= q1:
                return "ниже Q1 — это дешёвое предложение на рынке"
            if price <= median:
                return "между Q1 и медианой — ниже средней"
            if price <= q3:
                return "между медианой и Q3 — в нормальном диапазоне"
            return "выше Q3 — это дорогое предложение"
        delta = (price - median) / median * 100
        if delta < -10:
            return f"на {abs(delta):.0f}% ниже медианы — дешёвое"
        if delta < 10:
            return "около медианы — среднерыночная"
        return f"на {delta:.0f}% выше медианы — дорогое"

    def _build_listing_context(
        self,
        *,
        title: str,
        description: str | None,
        price_byn: float,
        is_negotiable_price: bool,
        condition: str | None,
        parameters: list[dict],
        market_median: float | None,
        market_count: int,
        market_q1: float | None = None,
        market_q3: float | None = None,
        market_min: float | None = None,
        market_max: float | None = None,
        seller_type: str | None = None,
        photo_count: int = 0,
        similar_listings: list[dict] | None = None,
        listing_age_days: int | None = None,
        risk_context_summary: str | None = None,
        risk_context_flags: list[str] | None = None,
        anomaly_flags: list[str] | None = None,
        deal_score: float | None = None,
        deal_verdict: str | None = None,
        photo_condition_label: str | None = None,
        photo_condition_notes: list[str] | None = None,
    ) -> str:
        # Title and description come from a Kufar listing — i.e. an
        # arbitrary user wrote them. Pass them through the same sanitiser
        # used for seller assistant inputs so a malicious listing can't
        # smuggle role-marker headers ("### system: ignore previous…")
        # into our analyse prompt.
        safe_title = sanitize_user_text(title, max_length=240) or ""
        parts = [f"## ОБЪЯВЛЕНИЕ: {safe_title}"]

        # Three-state price classification:
        #   is_negotiable_price  -> цена не указана продавцом ("договорная")
        #   is_free_price        -> явно отдают даром (price_byn == 0)
        #   neither              -> обычная фиксированная цена
        is_free_price = (not is_negotiable_price) and price_byn == 0

        # Price position
        price_pos = "позиция неизвестна"
        price_delta_pct = None
        if is_negotiable_price:
            price_pos = "цена договорная, точная сумма не указана"
        elif is_free_price:
            price_pos = "товар отдают бесплатно (полная скидка к рынку)"
        elif market_median and market_q1 and market_q3:
            price_delta_pct = (price_byn - market_median) / market_median * 100
            if price_byn <= market_q1:
                price_pos = f"НИЖЕ Q1 ({market_q1:.0f} BYN) — дешёвое"
            elif price_byn <= market_median:
                price_pos = "между Q1 и медианой — ниже средней"
            elif price_byn <= market_q3:
                price_pos = "между медианой и Q3 — средняя цена"
            else:
                price_pos = f"ВЫШЕ Q3 ({market_q3:.0f} BYN) — дорогое"
        elif market_median:
            price_delta_pct = (price_byn - market_median) / market_median * 100
            if price_delta_pct < -10:
                price_pos = f"на {abs(price_delta_pct):.0f}% ниже медианы"
            elif price_delta_pct < 0:
                price_pos = "немного ниже медианы"
            elif price_delta_pct < 10:
                price_pos = "около медианы"
            else:
                price_pos = f"на {price_delta_pct:.0f}% выше медианы"

        if is_negotiable_price:
            parts.append(f"Цена: договорная ({price_pos})")
            if market_median:
                parts.append(f"Рыночный ориентир: медиана {market_median:.0f} BYN")
        elif is_free_price:
            parts.append("Цена: 0 BYN — БЕСПЛАТНО (отдают даром)")
            if market_median:
                parts.append(
                    f"Рыночная медиана: {market_median:.0f} BYN — это и есть ориентир выгоды."
                )
        else:
            parts.append(f"Цена: {price_byn:.0f} BYN ({price_pos})")
            if price_delta_pct is not None:
                parts.append(f"Отклонение от медианы: {price_delta_pct:+.0f}%")

        if condition:
            parts.append(f"Состояние (заявлено): {condition}")
        if seller_type:
            seller_label = "магазин/дилер" if seller_type == "shop" else "частное лицо"
            parts.append(f"Продавец: {seller_label}")
        if listing_age_days is not None:
            if listing_age_days == 0:
                parts.append("Опубликовано: сегодня")
            elif listing_age_days == 1:
                parts.append("Опубликовано: вчера")
            else:
                parts.append(f"Опубликовано: {listing_age_days} дн. назад")
        if photo_count:
            parts.append(f"Количество фото: {photo_count}")
        if risk_context_summary:
            parts.append(f"Риск-контекст площадки: {risk_context_summary}")
        category_profile = buyer_category_profile_text(title, parameters)
        if category_profile:
            parts.append(f"\n{category_profile}")
        if photo_condition_label or photo_condition_notes:
            parts.append("\n## БЫСТРЫЙ ФОТО-ОСМОТР")
            if photo_condition_label:
                parts.append(f"Состояние по фото: {photo_condition_label}")
            if photo_condition_notes:
                parts.append(
                    "Наблюдения: "
                    + "; ".join(str(note)[:140] for note in photo_condition_notes[:4])
                )

        # Market statistics
        if market_median:
            parts.append("\n## РЫНОК")
            # Cluster-aware comparison note: when the listing belongs to
            # a specific variant (e.g. "Polo VI поколение"), the median is
            # computed from similar listings only — not from all search
            # results. The count reflects the cluster size.
            if market_count and market_count < 30:
                parts.append(
                    "Сравнение с похожими объявлениями (поколение/модификация), "
                    f"не со всеми результатами поиска ({market_count} шт.)"
                )
            if market_q1 and market_q3:
                parts.append(
                    f"Медиана: {market_median:.0f} BYN | "
                    f"Объявлений: {market_count} | "
                    f"Q1={market_q1:.0f} | Q3={market_q3:.0f} BYN"
                )
            else:
                parts.append(f"Медиана: {market_median:.0f} BYN | Объявлений: {market_count}")
            if market_min and market_max:
                parts.append(f"Диапазон: {market_min:.0f} — {market_max:.0f} BYN")

            # Pre-computed fair range guidance
            if market_q1 and market_q3:
                iqr = market_q3 - market_q1
                if iqr > 0:
                    parts.append(
                        f"Справедливый диапазон (Q1-Q3): {market_q1:.0f} — {market_q3:.0f} BYN"
                    )
                    if is_negotiable_price:
                        parts.append(
                            "Цена не указана, поэтому сравнивай объявление с этим диапазоном "
                            "и оцени, насколько выгодной будет сделка после торга."
                        )
                    elif price_byn < market_q1:
                        parts.append(
                            f"Цена НА {market_q1 - price_byn:.0f} BYN ниже "
                            f"справедливого диапазона — хорошая сделка или есть причины"
                        )
                    elif price_byn > market_q3:
                        parts.append(
                            f"Цена НА {price_byn - market_q3:.0f} BYN выше "
                            f"справедливого диапазона — продавец хочет больше рынка"
                        )

            entry_guidance = _entry_price_guidance(
                market_median=market_median,
                market_q1=market_q1,
                market_q3=market_q3,
                similar_listings=similar_listings,
            )
            if is_negotiable_price and entry_guidance is not None:
                entry_from, entry_to = entry_guidance
                parts.append(f"Разумный вход после торга: {entry_from} — {entry_to} BYN.")
                parts.append(
                    "Для recommendation и negotiation_tips используй этот диапазон как "
                    "рабочий ориентир цены входа, если нет более сильных аналогов."
                )

            # Resale instruction
            if is_negotiable_price:
                parts.append(
                    "\nПЕРЕПРОДАЖА: цена покупки ещё не согласована. "
                    "Оцени resale_potential по рынку и укажи, при какой цене входа "
                    "сделка выглядит разумной."
                )
            elif is_free_price:
                parts.append(
                    "\nПЕРЕПРОДАЖА: товар достаётся бесплатно (0 BYN). "
                    "Любая ненулевая цена перепродажи — это чистая прибыль; "
                    "оцени resale_potential по рынку и обрати внимание прежде "
                    "всего на состояние и логистику самовывоза."
                )
            elif price_byn:
                parts.append(
                    f"\nПЕРЕПРОДАЖА: цена покупки {price_byn:.0f} BYN. "
                    f"Оцени resale_potential — за сколько потенциально можно перепродать."
                )

        # Anomaly flags
        if anomaly_flags:
            parts.append(f"\n## АНОМАЛИИ: {', '.join(anomaly_flags)}")
        if deal_score is not None:
            parts.append(f"Оценка сделки: {deal_score:.0f}/100 ({deal_verdict or '?'})")
        if risk_context_flags:
            parts.append(f"\n## РИСК-СИГНАЛЫ: {'; '.join(risk_context_flags[:4])}")

        # All parameters (not truncated)
        if parameters:
            params_str = ", ".join(
                f"{sanitize_user_text(str(p.get('label', '')), max_length=40) or ''}: "
                f"{sanitize_user_text(str(p.get('value', '')), max_length=60) or ''}"
                for p in parameters
            )
            parts.append(f"\n## ПАРАМЕТРЫ: {params_str}")

        # Full description — sanitised + truncated. Reads as
        # "untrusted UGC", so role markers / injection phrases are stripped.
        if description:
            safe_description = sanitize_user_text(description, max_length=700)
            if safe_description:
                parts.append(f"\n## ОПИСАНИЕ ПРОДАВЦА:\n{safe_description}")

        # Similar listings — enriched with price deltas
        if similar_listings:
            compact_similar = similar_listings[:5]
            parts.append(f"\n## АЛЬТЕРНАТИВЫ ({len(compact_similar)} вариантов):")
            for i, sl in enumerate(compact_similar, 1):
                if is_negotiable_price:
                    if market_median:
                        price_diff = sl.get("price_byn", 0) - market_median
                        if price_diff > 0:
                            diff_str = f"на {price_diff:.0f} BYN выше медианы"
                        elif price_diff < 0:
                            diff_str = f"на {abs(price_diff):.0f} BYN ниже медианы"
                        else:
                            diff_str = "около медианы"
                    else:
                        diff_str = "рыночный ориентир"
                else:
                    price_diff = sl.get("price_byn", 0) - price_byn
                    if price_diff > 0:
                        diff_str = f"на {price_diff:.0f} BYN дороже"
                    elif price_diff < 0:
                        diff_str = f"на {abs(price_diff):.0f} BYN дешевле"
                    else:
                        diff_str = "та же цена"
                age_str = ""
                ad = sl.get("age_days")
                if ad is not None:
                    age_str = f", {ad} дн." if ad > 0 else ", сегодня"
                deal_str = ""
                ds = sl.get("deal_score")
                if ds is not None:
                    deal_str = f", оценка: {ds:.0f}/100"

                # SEC-01: similar_listings come from other Kufar sellers —
                # treat title/description as untrusted UGC and strip
                # role markers / "ignore previous instructions" payloads
                # before splicing them into the analyse prompt. Target
                # listing is already sanitised at line 760/936; alternatives
                # were the missing surface.
                safe_sl_title = sanitize_user_text(
                    str(sl.get("title", "")),
                    max_length=80,
                    context="similar_listing_title",
                ) or ""
                parts.append(
                    f"  {i}. [{sl.get('ad_id')}] "
                    f"{safe_sl_title} — "
                    f"{sl.get('price_byn', 0):.0f} BYN ({diff_str}), "
                    f"{sl.get('condition') or 'не указано'}, "
                    f"{sl.get('seller_type', '?')}{age_str}{deal_str}"
                )
                params = sanitize_user_text(
                    str(sl.get("parameters") or ""), max_length=120
                ) or ""
                if params:
                    parts.append(f"     Параметры: {params[:120]}")
                safe_sl_desc = sanitize_user_text(
                    str(sl.get("description") or ""),
                    max_length=100,
                    context="similar_listing_desc",
                )
                if safe_sl_desc:
                    parts.append(f"     Описание: {safe_sl_desc}")

        return "\n".join(parts)

    async def _fetch_image_b64(self, url: str) -> dict | None:
        return await fetch_image_b64(url, self._get_client)

    async def _fetch_image_bytes(self, url: str) -> bytes | None:
        return await fetch_image_bytes(url, self._get_client)

    @staticmethod
    def _is_allowed_image_url(url: str) -> bool:
        return is_allowed_image_url(url)

    @staticmethod
    def _compress_image(data: bytes, max_dim: int = 768, quality: int = 75) -> bytes | None:
        return compress_image(data, max_dim=max_dim, quality=quality)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Parse JSON from model response, handling reasoning chains and code blocks."""
        text = text.strip()

        # Remove markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

        # Strategy 1: direct parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Strategy 2: parse the last balanced JSON object while respecting
        # braces inside quoted strings.
        for candidate in reversed(AIService._json_object_candidates(text)):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        # Strategy 3: non-greedy regex (original behavior, but non-greedy
        # to avoid capturing across multiple JSON objects)
        json_match = re.search(r"\{.*?\}", text.strip(), flags=re.DOTALL)
        if json_match:
            candidate = json_match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        logger.warning("AI returned non-JSON (%d chars): %s", len(text), text[:500])
        # Try to extract partial data from truncated JSON
        return _repair_truncated_json(text)

    @staticmethod
    def _json_object_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        start: int | None = None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_string:
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif char == "}" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start : index + 1])
                    start = None
        return candidates

    async def close(self) -> None:
        """Close the underlying httpx client, if it was created."""
        if self._httpx_client is not None and not self._httpx_client.is_closed:
            await self._httpx_client.aclose()
            self._httpx_client = None

    async def chat_json(
        self, *, system: str, content: list[dict] | str, max_tokens: int = 1200,
    ) -> dict:
        """Public wrapper around _chat for simple JSON responses."""
        return await self._chat(
            system=system,
            content=content,
            max_tokens=max_tokens,
            model=self._analysis_model,
            operation="chat_json",
        )


@functools.lru_cache(maxsize=1)
def get_ai_service() -> AIService:
    return AIService()
