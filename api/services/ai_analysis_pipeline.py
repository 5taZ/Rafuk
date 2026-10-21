"""AI analysis pipeline — stages, fallback logic, and response builders."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time as _time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from api.schemas import (
    AIAnalysisRequest,
    AIAnalysisResponse,
    AIResalePotential,
    AIResalePrice,
)
from api.services.aggregator import (
    cluster_price_stats,
    compute_category_price_stats,
    compute_price_stats,
    extract_prices,
    filter_ads_for_accessory_category,
    normalize_price_byn,
    product_cluster_key,
    resolve_price_reference,
)
from api.services.ai_guardrails import apply_ai_market_guardrails
from api.services.ai_guards import QuotaCharge, _refund_quota
from api.services.ai_marketplace import (
    BestAlternativeDecision,
    MarketplaceRiskContext,
    RiskFloor,
    apply_floor_red_flags,
    build_fallback_analysis_result,
    build_market_context_fallback,
    build_marketplace_risk_context,
    build_risk_floor,
    choose_best_alternative,
    collect_similar_listings_from_cohorts,
    complete_analysis_sections,
    enforce_risk_floor,
    finalize_red_flags,
    normalize_photo_authenticity,
    normalize_scam_analysis,
)
from api.services.ai_quality import sanitize_public_ai_payload
from api.services.ai_sanitize import scrub_model_payload, strip_html_in_payload
from api.services.ai_service import (
    dedupe_analysis_payload,
    detect_category,
    get_ai_service,
    normalize_condition_label,
)
from api.services.ai_task_store import _get_task, _release_inflight, _task_ttl, _update_task
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.listing_mapper import SENSITIVE_AD_PARAMETER_KEYS
from api.services.market_signals import anomaly_labels, detect_anomaly_flags
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import compute_deal_score


# AI-HIGH (issues §3.1): the previous cache key hard-coded a "v5" tag,
# so any change to ``ai_prompts.py`` (or the AIAnalysisResponse schema)
# could leave stale results in cache for the full TTL window. We now
# derive the cache version from a SHA256 of the prompt + schema source
# files at import — every meaningful prompt edit lands a fresh version
# tag automatically. The hash is truncated to 10 chars so the cache
# key stays readable in Redis CLI.
def _compute_ai_cache_version() -> str:
    sources = (
        Path(__file__),
        Path(__file__).parent / "ai_prompts.py",
        Path(__file__).parent.parent / "schemas.py",
    )
    h = hashlib.sha256()
    for src in sources:
        try:
            h.update(src.read_bytes())
        except OSError:
            # Source missing in some unusual deploy — fall back to a
            # static tag so we don't crash on import.
            h.update(b"missing")
    return f"v6-{h.hexdigest()[:10]}"


# L8: cache version is computed once at import time. During a rolling
# update, old workers continue serving stale cache results under the
# previous version tag until they are replaced. This is acceptable
# because the AI cache TTL is short and the prompt changes are
# infrequent; a brief window of mixed versions is harmless.
_AI_CACHE_VERSION = _compute_ai_cache_version()

logger = logging.getLogger(__name__)


def _analysis_cache_key(payload: AIAnalysisRequest) -> str:
    return (
        f"ai_analysis:{_AI_CACHE_VERSION}:{payload.ad_id}:"
        f"{payload.query}:cat={payload.category}"
    )


_AI_ANALYSIS_ERRORS = (
    httpx.HTTPError,
    KufarAPIError,
    RuntimeError,
    ValidationError,
    ValueError,
    TypeError,
    KeyError,
)

DISCLAIMER = (
    "AI-анализ носит исключительно информационно-справочный характер и не является "
    "финансовой, инвестиционной или юридической консультацией; гарантией прибыли, "
    "рыночной стоимости или ликвидности товара; рекомендацией к совершению или отказу "
    "от сделки; профессиональной оценкой товара. Все решения пользователь принимает "
    "самостоятельно на свой страх и риск. Рыночные данные основаны на открытых "
    "объявлениях kufar.by и могут не отражать реальные цены сделок."
)


def _parse_list_age_days(list_time_str: str | None) -> int | None:
    """Parse Kufar list_time to days-since-publication."""
    if not list_time_str:
        return None
    try:
        dt = datetime.fromisoformat(list_time_str.replace("Z", "+00:00"))
        # Make naive datetimes timezone-aware (assume UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt
        return max(0, delta.days)
    except (ValueError, TypeError):
        return None


def _build_resale_potential(resale_data: Any) -> AIResalePotential | None:
    """Build the AIResalePotential pydantic model from a raw dict.

    Returns None when the input doesn't look like a valid resale block.
    Used in both the success and TimeoutError fallback paths to avoid
    duplicating ~20 lines of nested conditionals.
    """
    if not isinstance(resale_data, dict):
        return None
    try:
        return AIResalePotential(
            fast_price=AIResalePrice(**resale_data["fast_price"])
            if isinstance(resale_data.get("fast_price"), dict)
            else None,
            market_price=AIResalePrice(**resale_data["market_price"])
            if isinstance(resale_data.get("market_price"), dict)
            else None,
            optimal_price=AIResalePrice(**resale_data["optimal_price"])
            if isinstance(resale_data.get("optimal_price"), dict)
            else None,
            reasoning=resale_data.get("reasoning", ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _build_fallback_response(
    *,
    payload: AIAnalysisRequest,
    price_byn: float,
    is_negotiable_price: bool,
    median: float | None,
    q1: float | None,
    q3: float | None,
    similar: list[dict[str, Any]],
    risk_context: MarketplaceRiskContext | None,
    reference: Any,
    photo_condition_label: str | None,
    photo_condition_notes: list[str],
    title: str,
    parameters: list[dict[str, Any]],
    risk_floor: RiskFloor | None = None,
) -> AIAnalysisResponse:
    """Build a fallback AIAnalysisResponse when the AI call fails.

    Uses rule-based analysis instead of AI, so the user always gets
    a useful result rather than a raw error message.
    """
    if risk_context is None:
        # RV-AI-12: hot_words is a required field — without it this branch
        # raised TypeError and the (RV-AI-03) fallback never reached the user.
        risk_context = MarketplaceRiskContext(summary="", flags=[], score=0.0, hot_words=[])
    floor = risk_floor or RiskFloor()
    fallback_best_decision = choose_best_alternative(
        similar,
        target_price=price_byn,
        is_negotiable_price=is_negotiable_price,
        ai_best_pick_ad_id=None,
    )
    fallback_best_alt = fallback_best_decision.item
    if fallback_best_alt:
        fallback_best_alt = {
            "ad_id": fallback_best_alt["ad_id"],
            "title": fallback_best_alt["title"],
            "price_byn": fallback_best_alt["price_byn"],
            "image_url": fallback_best_alt.get("image_url"),
            "link": fallback_best_alt.get("link", ""),
            "deal_score": fallback_best_alt.get("deal_score", 0.0),
            "condition": fallback_best_alt.get("condition"),
            "ai_note": fallback_best_decision.reason,
        }
    # RV-AI-04: the floor's flags go in before the summary is written so
    # "Главный риск" names them, and its verdict rule binds the fallback
    # too (the rules alone call anything under Q1 "worth_it").
    fallback_red_flags = apply_floor_red_flags(finalize_red_flags([], risk_context), floor)
    fallback_result = build_fallback_analysis_result(
        title=title or "",
        parameters=parameters,
        price_byn=price_byn,
        is_negotiable_price=is_negotiable_price,
        market_median=median,
        market_q1=q1,
        market_q3=q3,
        best_alternative=fallback_best_alt,
        similar_listings=similar,
        risk_context=risk_context,
        photo_condition_label=photo_condition_label or None,
        photo_condition_notes=photo_condition_notes or [],
        red_flags=fallback_red_flags,
    )
    fallback_result = dedupe_analysis_payload(fallback_result)
    fallback_result, fallback_red_flags = enforce_risk_floor(
        fallback_result,
        floor=floor,
        red_flags=fallback_red_flags,
        title=title or "",
        price_byn=price_byn,
        market_median=median,
        best_alternative=fallback_best_alt,
        is_negotiable_price=is_negotiable_price,
    )
    ref_scope = getattr(reference, "scope", None) or "query"
    ref_label = getattr(reference, "label", None) or ""
    fallback_market_ctx = build_market_context_fallback(
        price_byn=price_byn,
        is_negotiable_price=is_negotiable_price,
        market_median=median,
        similar_listings=similar,
        risk_context=risk_context,
        ai_market_context="",
        price_reference_scope=ref_scope,
        price_reference_label=ref_label,
    )
    response = AIAnalysisResponse(
        ad_id=payload.ad_id,
        condition=fallback_result.get("condition"),
        fair_price=fallback_result.get("fair_price"),
        resale_potential=None,
        watch_out=fallback_result.get("watch_out", []),
        recommendation=fallback_result.get("recommendation"),
        similar_listings=similar,
        best_alternative=fallback_best_alt,
        meeting_checklist=fallback_result.get("meeting_checklist", []),
        negotiation_tips=fallback_result.get("negotiation_tips", []),
        red_flags=fallback_red_flags,
        market_context=fallback_market_ctx,
        price_reference_scope=ref_scope,
        price_reference_label=ref_label,
        best_pick_reason=fallback_best_decision.reason,
        summary=fallback_result.get("summary", ""),
        source="fallback",
        disclaimer=DISCLAIMER,
    )
    response.resale_potential = _build_resale_potential(
        fallback_result.get("resale_potential")
    )
    return response


async def _deliver_fallback_result(
    cache: Any,
    task_id: str,
    *,
    user_id: int | None = None,
    payload: AIAnalysisRequest,
    price_byn: float,
    is_negotiable_price: bool,
    median: float | None,
    q1: float | None,
    q3: float | None,
    similar: list[dict[str, Any]],
    risk_context: MarketplaceRiskContext | None,
    reference: Any,
    photo_condition_label: str | None,
    photo_condition_notes: list[str] | None,
    title: str,
    parameters: list[dict[str, Any]],
    warning: str | None = None,
    risk_floor: RiskFloor | None = None,
) -> bool:
    """Hand the rule-based result to the requesting user's task only.

    RV-AI-03: the fallback used to be written into the shared analysis
    cache (30 min, and with no marker on timeouts), so everyone who
    opened the same listing got the rule-based draft as if it were the
    AI analysis. It now lives in this task alone, labelled
    ``source="fallback"`` with an ``_ai_warning`` the UI shows.
    Returns False when not even the fallback could be built.
    """
    try:
        response = _build_fallback_response(
            payload=payload,
            price_byn=price_byn,
            is_negotiable_price=is_negotiable_price,
            median=median,
            q1=q1,
            q3=q3,
            similar=similar,
            risk_context=risk_context,
            reference=reference,
            photo_condition_label=photo_condition_label,
            photo_condition_notes=photo_condition_notes,
            title=title,
            parameters=parameters,
            risk_floor=risk_floor,
        )
        fallback_serialized = sanitize_public_ai_payload(
            strip_html_in_payload(response.model_dump(by_alias=True))
        )
        fallback_serialized["_ai_warning"] = warning or _GENERIC_FALLBACK_WARNING
        await _update_task(
            cache, task_id, user_id=user_id,
            status="done", progress=100, stage="done",
            result=fallback_serialized, error=None,
        )
        return True
    except Exception as fallback_exc:
        logger.error(
            "Fallback build also failed for task %s: %s",
            task_id, fallback_exc, exc_info=True,
        )
        err_msg = "AI сервис недоступен. Попробуйте позже."
        await _update_task(
            cache, task_id, user_id=user_id,
            status="error", stage="error", error=err_msg,
        )
        return False


# ── Analysis state and stages ──────────────────────────────────────


# BE-M4: cap the fan-out of parallel Kufar searches that ``_stage_search``
# fires per AI task (strict_category / broad_category / broad_query).
# KufarClient already has its own ``kufar_parallel_semaphore`` for HTTP
# concurrency across all callers, but without this AI-pipeline-side
# cap a single task can submit 3 search jobs that immediately saturate
# the client semaphore — pushing any concurrent /listings or /analytics
# request behind them. Bounding to 2 here keeps a slot free for the
# rest of the API surface while still letting a slow first attempt
# overlap with its fallback. Lazily initialised so the semaphore binds
# to whichever event loop is currently running (tests routinely spin
# up fresh loops via pytest-asyncio).
_PIPELINE_SEARCH_LIMIT = 2
_pipeline_search_semaphore: asyncio.Semaphore | None = None
_pipeline_search_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_pipeline_search_semaphore() -> asyncio.Semaphore:
    # H5: track event loop so test runs that create fresh loops don't
    # inherit the previous loop's semaphore.
    global _pipeline_search_semaphore, _pipeline_search_semaphore_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if (
        _pipeline_search_semaphore is None
        or _pipeline_search_semaphore_loop is not loop
    ):
        _pipeline_search_semaphore = asyncio.Semaphore(_PIPELINE_SEARCH_LIMIT)
        _pipeline_search_semaphore_loop = loop
    return _pipeline_search_semaphore


# INF-02: concurrency limit for the entire AI analysis pipeline.
#
# ``_BG_TASK_LIMIT`` (in ai_task_store) gates *registered* tasks; this
# semaphore gates how many of them are actually doing AI work at the
# same instant. The two layers are complementary:
#   - _BG_TASK_LIMIT prevents abusive request rates from piling up
#     half-finished tasks and growing memory without bound;
#   - _ANALYSIS_RUN_LIMIT keeps Gemini/proxy quotas from turning one
#     user-visible analyse action into a burst of provider calls.
#
# Lazily bound to the running loop so test runs that create fresh
# loops don't inherit the previous loop's semaphore (TEST-05 covers
# the loop-bound bug class).
_ANALYSIS_RUN_LIMIT = 1
_analysis_run_semaphore: asyncio.Semaphore | None = None
_analysis_run_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_analysis_run_semaphore() -> asyncio.Semaphore:
    global _analysis_run_semaphore, _analysis_run_semaphore_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if (
        _analysis_run_semaphore is None
        or _analysis_run_semaphore_loop is not loop
    ):
        _analysis_run_semaphore = asyncio.Semaphore(_ANALYSIS_RUN_LIMIT)
        _analysis_run_semaphore_loop = loop
    return _analysis_run_semaphore


class _AnalysisComplete(Exception):  # noqa: N818
    """Signal early completion of analysis (e.g. target ad not found)."""


class _AC:
    """Mutable state bag shared between _run_analysis stages."""


def _init_analysis_state(
    task_id: str,
    payload: AIAnalysisRequest,
    settings: Any,
    cache: Any,
    kufar_client: KufarClient,
    user_id: int | None,
) -> _AC:
    """Create and pre-initialize the analysis context."""
    c = _AC()
    c.task_id = task_id
    c.payload = payload
    c.settings = settings
    c.cache = cache
    c.kufar_client = kufar_client
    c.user_id = user_id
    c.ai = get_ai_service()
    c.analysis_timeout = getattr(settings, "ai_analysis_timeout", 150)
    c.photo_precheck_timeout = getattr(settings, "ai_photo_precheck_timeout", 30)
    c.photo_precheck_enabled = bool(getattr(settings, "ai_photo_precheck_enabled", False))
    c.started_at = _time.monotonic()
    # RV-AI-07: the unit this task was charged and the singleflight claim
    # it holds; both are released whatever way the task ends.
    c.quota_charge = None
    c.inflight_key = None
    # Read by the fallback path, which can run before the stages set them.
    c.target_ad = None
    c.target_anomaly_flags = []
    c.failed_sections = []
    c.images_analyzed = 0
    # Defaults assume "negotiable" (price unknown) — the safer fallback if
    # the extract stage never runs (e.g. early failure). Free items must
    # set is_free_price=True explicitly in _stage_extract.
    c.price_byn = 0.0
    c.is_negotiable_price = True
    c.is_free_price = False
    c.median = None
    c.q1 = None
    c.q3 = None
    c.similar = []
    c.risk_context = None
    c.reference = None
    c.photo_condition_label = None
    c.photo_condition_notes = None
    c.title = ""
    c.parameters = []
    return c


def _cluster_stats_usable_for_ai(
    *,
    target_title: str,
    query: str,
    reference_stats: Any,
    cluster_stats: Any,
) -> bool:
    """Decide whether exact-title stats are stable enough to replace query stats."""
    if cluster_stats is None or not getattr(cluster_stats, "median", 0):
        return False
    if getattr(cluster_stats, "count", 0) < 3:
        return False

    if product_cluster_key(target_title, query):
        return True

    ref_median = float(getattr(reference_stats, "median", 0) or 0)
    if ref_median <= 0:
        return True

    cluster_median = float(cluster_stats.median)
    ref_q1 = float(getattr(reference_stats, "q1", 0) or ref_median * 0.85)
    ref_q3 = float(getattr(reference_stats, "q3", 0) or ref_median * 1.15)
    cluster_count = int(getattr(cluster_stats, "count", 0) or 0)

    outside_reference = (
        (ref_q1 > 0 and cluster_median < ref_q1 * 0.75)
        or (ref_q3 > 0 and cluster_median > ref_q3 * 1.25)
    )
    if outside_reference and cluster_count < 10:
        return False

    cluster_q1 = float(getattr(cluster_stats, "q1", 0) or 0)
    cluster_q3 = float(getattr(cluster_stats, "q3", 0) or 0)
    spread = (
        (cluster_q3 - cluster_q1) / cluster_median
        if cluster_median > 0 and cluster_q3 >= cluster_q1
        else float("inf")
    )
    return not (cluster_count < 5 and spread > 0.35)


def _risk_floor(c: _AC) -> RiskFloor:
    return build_risk_floor(c.target_ad, anomaly_flags=c.target_anomaly_flags)


async def _refund_task_quota(c: _AC) -> None:
    """RV-AI-07: give the unit back when the user ends up with nothing."""
    charge, c.quota_charge = c.quota_charge, None
    await _refund_quota(c.cache, charge)


async def _analysis_fallback(c: _AC, exc: BaseException | None = None) -> None:
    """Deliver a fallback result on timeout or AI errors."""
    if isinstance(exc, TimeoutError):
        # RV-AI-03: the timeout path used to be called without the
        # exception and so was never logged at all.
        logger.warning(
            "AI task %s: timed out after %.1fs (limit %ss) for ad_id=%d — "
            "delivering fallback",
            c.task_id, _time.monotonic() - c.started_at, c.analysis_timeout, c.payload.ad_id,
        )
    elif exc is not None:
        logger.error(
            "AI async task %s failed: [%s] %s",
            c.task_id, type(exc).__name__, exc,
        )
    delivered = await _deliver_fallback_result(
        c.cache, c.task_id, user_id=c.user_id,
        payload=c.payload,
        price_byn=c.price_byn,
        is_negotiable_price=c.is_negotiable_price,
        median=c.median,
        q1=c.q1,
        q3=c.q3,
        similar=c.similar,
        risk_context=c.risk_context,
        reference=c.reference,
        photo_condition_label=c.photo_condition_label,
        photo_condition_notes=c.photo_condition_notes,
        title=c.title,
        parameters=c.parameters,
        warning=_analysis_fallback_warning(exc),
        risk_floor=_risk_floor(c),
    )
    if not delivered:
        await _refund_task_quota(c)
    elif exc is not None and not isinstance(exc, TimeoutError):
        logger.warning(
            "AI task %s: fallback result delivered (error: %s) for ad_id=%d",
            c.task_id, type(exc).__name__, c.payload.ad_id,
        )


def _is_ai_rate_limit_error(exc: BaseException | None) -> bool:
    text = str(exc or "").upper()
    return "429" in text or "RATE_LIMIT" in text or "RESOURCE_EXHAUSTED" in text


_GENERIC_FALLBACK_WARNING = "AI не ответил. Показан рыночный черновик по данным рынка."
# RV-AI-03: what the UI banner (api_ai_render.js, _ai_warning) says when
# the result isn't a complete model answer.
_PARTIAL_WARNINGS = {
    "price_market": (
        "AI ответил не полностью: цена, ценовые ориентиры и советы по торгу "
        "рассчитаны по данным рынка."
    ),
    "condition_risks": (
        "AI ответил не полностью: вердикт, состояние и риски рассчитаны по данным рынка."
    ),
}


def _analysis_fallback_warning(exc: BaseException | None) -> str:
    if isinstance(exc, TimeoutError):
        return (
            "AI не успел ответить вовремя. Показан рыночный черновик по данным рынка — "
            "полный анализ можно повторить позже."
        )
    if _is_ai_rate_limit_error(exc):
        return (
            "AI сейчас на лимите. Показан рыночный черновик по данным рынка — "
            "полный анализ можно повторить чуть позже."
        )
    return _GENERIC_FALLBACK_WARNING


async def _stage_search(c: _AC) -> None:
    """Search for market data and locate the target ad."""
    await _update_task(
        c.cache,
        c.task_id,
        user_id=c.user_id,
        status="processing",
        progress=10,
        stage="loading_market_data",
        error=None,
    )
    logger.info("AI task %s stage=loading_market_data ad_id=%d", c.task_id, c.payload.ad_id)

    search_attempts = [
        {"strict_search": True, "category": c.payload.category},
        {"strict_search": False, "category": c.payload.category},
    ]
    if c.payload.category is not None:
        search_attempts.append({"strict_search": False, "category": None})

    # BE-M4: bound parallel Kufar fan-out via the module-level semaphore
    # so one runaway AI task can't starve the rest of the API. With the
    # default cap of 2, the third search attempt (broad_query fallback)
    # only kicks in once one of the first two finishes — which is
    # almost always what we want anyway since attempt 1 usually finds
    # the target.
    search_sem = _get_pipeline_search_semaphore()

    async def _search_one(attempt: dict):
        async with search_sem:
            ds = await load_query_dataset(
                query=c.payload.query,
                currency="BYN",
                settings=c.settings,
                client=c.kufar_client,
                **attempt,
            )
            target = next(
                (ad for ad in ds.ads if int(ad.get("ad_id", 0)) == c.payload.ad_id),
                None,
            )
            return ds, target

    results = await asyncio.gather(
        *[_search_one(a) for a in search_attempts],
        return_exceptions=True,
    )

    await _update_task(c.cache, c.task_id, user_id=c.user_id, progress=30, stage="search_ready")
    logger.info("AI task %s stage=search_ready", c.task_id)

    datasets_by_cohort: list[tuple[str, Any]] = []
    cohort_keys = [
        "strict_category",
        "broad_category",
        "broad_query",
    ]
    dataset = None
    target_ad = None
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("Search strategy %d failed: %s", i, result)
            continue
        ds, target = result
        if i < len(cohort_keys):
            datasets_by_cohort.append((cohort_keys[i], ds))
        if target and target_ad is None:
            dataset = ds
            target_ad = target

    if dataset is None:
        for result in results:
            if isinstance(result, Exception):
                continue
            ds, _ = result
            dataset = ds
            break

    if not target_ad:
        await _update_task(
            c.cache,
            c.task_id,
            user_id=c.user_id,
            status="error",
            stage="target_missing",
            error="Объявление не найдено",
        )
        # RV-AI-07: nothing was analysed — don't bill for it.
        await _refund_task_quota(c)
        raise _AnalysisComplete()

    c.dataset = dataset
    c.target_ad = target_ad
    c.datasets_by_cohort = datasets_by_cohort


def _stage_extract(c: _AC) -> None:
    """Extract target ad fields and compute market statistics."""
    c.title = c.target_ad.get("subject", "") or c.target_ad.get("title", "")
    c.description = c.target_ad.get("body", "") or c.target_ad.get("description", "")
    # Pass the full ad dict so detect_price_type can distinguish "free"
    # (genuine giveaway, normalize -> 0.0) from "negotiable" (unknown
    # price, normalize -> None). Conflating them was a silent bug:
    # genuine free items were being announced to the AI as "цена не
    # указана", which wrecked price guidance and red-flag analysis.
    raw_normalized = normalize_price_byn(c.target_ad.get("price_byn"), c.target_ad)
    c.is_negotiable_price = raw_normalized is None
    c.is_free_price = raw_normalized == 0.0
    # Downstream code expects price_byn as float for math safety. Use 0.0
    # for both negotiable and free; the boolean flags carry the semantic
    # distinction so callers (especially AI prompts) can render them
    # differently.
    c.price_byn = raw_normalized if raw_normalized is not None else 0.0

    c.condition = None
    for param in c.target_ad.get("ad_parameters", []):
        if param.get("p") == "condition":
            c.condition = param.get("vl") or param.get("v")
            break

    c.parameters = []
    for param in c.target_ad.get("ad_parameters", []):
        param_key = param.get("p", "")
        if param_key in SENSITIVE_AD_PARAMETER_KEYS or param_key in {
            "condition", "currency", "price",
        }:
            continue
        label = param.get("pl") or param_key
        value = param.get("vl") or str(param.get("v", ""))
        if label and value:
            c.parameters.append({"label": label, "value": value})

    is_company = c.target_ad.get("company_ad", False)
    c.seller_type = "shop" if is_company else "private"
    c.risk_context = build_marketplace_risk_context(c.target_ad)
    all_images = c.target_ad.get("images") or []
    c.photo_count = len(all_images)
    c.listing_age_days = _parse_list_age_days(c.target_ad.get("list_time"))

    c.images = []
    for img in all_images[:3]:
        path = img.get("path", "")
        if path:
            c.images.append(f"https://rms.kufar.by/v1/gallery/{path}")

    stats = c.dataset.price_stats
    # Detect the product category so we can filter out irrelevant
    # listings (e.g. phones mixed into a "чехол" search).
    detected_cat = detect_category(c.title, c.parameters)
    # When the detected category is an accessory type, filter ads
    # by price cap so that phone-level prices don't skew the median.
    # This prevents a 20 BYN case being compared against 2000 BYN phones.
    filtered_ads = filter_ads_for_accessory_category(c.dataset.ads, detected_cat)
    if filtered_ads is not c.dataset.ads:
        # Recompute stats on the filtered subset
        filtered_prices = extract_prices(filtered_ads)
        if filtered_prices:
            stats = compute_price_stats(filtered_prices)
    # Compute category-aware price stats so that accessories (e.g. phone
    # cases) are compared against other accessories, not against phones.
    category_price_stats = compute_category_price_stats(filtered_ads)
    c.reference = resolve_price_reference(
        c.target_ad, stats, category_price_stats
    )
    effective_stats = c.reference.stats
    # Cluster-aware median: compare against listings that share
    # the same variant tokens (generation, body type, etc.) as
    # the target ad.  Falls back to the broad median when the
    # cluster is too small (< 3 similar listings).
    cluster_stats = cluster_price_stats(
        str(c.target_ad.get("subject", "")), filtered_ads,
    )
    if _cluster_stats_usable_for_ai(
        target_title=str(c.target_ad.get("subject", "")),
        query=c.payload.query,
        reference_stats=effective_stats,
        cluster_stats=cluster_stats,
    ):
        effective_stats = cluster_stats
    c.effective_stats = effective_stats
    c.median = effective_stats.median if effective_stats else None
    c.count = effective_stats.count if effective_stats else 0
    c.q1 = effective_stats.q1 if effective_stats else None
    c.q3 = effective_stats.q3 if effective_stats else None
    c.price_min = effective_stats.min if effective_stats else None
    c.price_max = effective_stats.max if effective_stats else None

    c.similar = collect_similar_listings_from_cohorts(
        cohorts=c.datasets_by_cohort or [("broad_category", c.dataset)],
        query=c.payload.query,
        target_ad=c.target_ad,
        target_ad_id=c.payload.ad_id,
        target_price=c.price_byn,
        market_median=c.median,
    )

    c.ai_similar_for_comparison = [
        {
            "ad_id": s["ad_id"],
            "title": s["title"],
            "price_byn": s["price_byn"],
            "price_delta_byn": (
                round(s["price_byn"] - c.price_byn, 0) if not c.is_negotiable_price else None
            ),
            "condition": s.get("condition"),
            "description": s.get("description", ""),
            "seller_type": s.get("seller_type"),
            "parameters": s.get("parameters", ""),
            "age_days": s.get("age_days"),
            "deal_score": round(s.get("deal_score", 0), 1),
        }
        for s in c.similar
    ]


def _stage_scoring(c: _AC) -> None:
    """Compute anomaly flags and deal score."""
    c.target_anomaly_labels: list[str] = []
    c.target_deal_score = 0.0
    c.target_deal_verdict = ""
    c.photo_condition_label = ""
    c.photo_condition_notes: list[str] = []
    if c.effective_stats:
        raw_flags: list[Any] = []
        try:
            raw_flags = detect_anomaly_flags(c.target_ad, c.effective_stats)
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning(
                "Failed to compute anomaly flags for ad_id=%d",
                c.payload.ad_id,
                exc_info=True,
            )
        c.target_anomaly_flags = raw_flags
        c.target_anomaly_labels = anomaly_labels(raw_flags)
        try:
            deal = compute_deal_score(
                c.target_ad, query=c.payload.query, market_stats=c.effective_stats,
            )
            c.target_deal_score = deal.score
            c.target_deal_verdict = deal.verdict
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning(
                "Failed to compute deal score for ad_id=%d",
                c.payload.ad_id,
                exc_info=True,
            )


async def _stage_photo(c: _AC) -> None:
    """AI photo condition precheck."""
    if c.images and getattr(c, "photo_precheck_enabled", True):
        await _update_task(
            c.cache, c.task_id, user_id=c.user_id,
            progress=40, stage="photo_precheck",
        )
        logger.info("AI task %s stage=photo_precheck images=%d", c.task_id, len(c.images))
        try:
            quick_photo = await asyncio.wait_for(
                c.ai.quick_condition(c.images[:3]), timeout=c.photo_precheck_timeout
            )
            # RV-AI-04: these notes describe seller photos and end up in
            # the response, so links/contacts read off a photo go too.
            quick_photo = scrub_model_payload(quick_photo)
            c.photo_condition_label = str(quick_photo.get("condition") or "").strip()
            c.photo_condition_notes = [
                str(note).strip()
                for note in (quick_photo.get("notes") or [])
                if str(note).strip()
            ][:4]
            logger.info(
                "AI task %s photo_precheck ok label=%r notes=%d",
                c.task_id,
                c.photo_condition_label,
                len(c.photo_condition_notes),
            )
        except TimeoutError:
            logger.warning(
                "AI task %s photo_precheck timed out (30s) — will use text-only condition",
                c.task_id,
            )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning(
                "AI task %s photo_precheck failed [%s]: %s — will use text-only condition",
                c.task_id,
                type(exc).__name__,
                exc,
            )


async def _stage_ai(c: _AC) -> None:
    """Execute the main AI analysis with parallel sub-calls."""
    await _update_task(c.cache, c.task_id, user_id=c.user_id, progress=50, stage="calling_ai")
    logger.info(
        "AI task %s stage=calling_ai title=%r images=%d similar=%d",
        c.task_id,
        c.title[:60],
        len(c.images),
        len(c.ai_similar_for_comparison),
    )

    _t0 = _time.monotonic()
    logger.info(
        "AI async task %s: calling ai.analyze_listing_parallel for '%s' (%d imgs, %d similar)",
        c.task_id,
        c.title[:50],
        len(c.images),
        len(c.ai_similar_for_comparison),
    )
    # Wrap the AI call so that httpx transport-level timeouts
    # (ReadTimeout, ConnectTimeout) are converted to TimeoutError.
    # Without this, they fall into the generic _AI_ANALYSIS_ERRORS handler
    # and the user sees "AI сервис недоступен" instead of a fallback result.
    async def _run_parallel_with_progress():
        # Fire a background progress updater while the AI calls run.
        # Smoother steps prevent the bar from stalling at any
        # single milestone — the AI call typically takes 30-90s,
        # so we creep through (55, 60, 65, 70, 74, 78) over ~42s
        # with a final hold at 80 to avoid passing 85 prematurely.
        async def _progress_pump():
            steps: list[tuple[int, float]] = [
                (55, 6.0),
                (60, 6.0),
                (65, 7.0),
                (70, 7.0),
                (74, 8.0),
                (78, 8.0),
                (80, 12.0),
            ]
            for pct, delay in steps:
                await asyncio.sleep(delay)
                await _update_task(
                    c.cache, c.task_id, user_id=c.user_id,
                    progress=pct, stage="calling_ai",
                )

        pump_task = asyncio.create_task(_progress_pump())
        try:
            return await c.ai.analyze_listing_parallel(
                title=c.title,
                description=c.description,
                price_byn=c.price_byn,
                is_negotiable_price=c.is_negotiable_price,
                condition=c.condition,
                parameters=c.parameters,
                market_median=c.median,
                market_count=c.count,
                market_q1=c.q1,
                market_q3=c.q3,
                market_min=c.price_min,
                market_max=c.price_max,
                seller_type=c.seller_type,
                photo_count=c.photo_count,
                listing_age_days=c.listing_age_days,
                similar_listings=c.ai_similar_for_comparison,
                risk_context_summary=c.risk_context.summary,
                risk_context_flags=c.risk_context.flags,
                anomaly_flags=c.target_anomaly_labels,
                deal_score=c.target_deal_score,
                deal_verdict=c.target_deal_verdict,
                photo_condition_label=c.photo_condition_label or None,
                photo_condition_notes=c.photo_condition_notes or None,
                image_urls=c.images,
                user_goal=None,
            )
        finally:
            pump_task.cancel()

    try:
        c.result = await asyncio.wait_for(
            _run_parallel_with_progress(), timeout=c.analysis_timeout
        )
    except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        logger.warning(
            "AI task %s: httpx %s — converting to TimeoutError",
            c.task_id,
            type(exc).__name__,
        )
        raise TimeoutError(str(exc)) from exc
    c.failed_sections = list(c.result.pop("_failed_sections", None) or [])
    c.images_analyzed = int(c.result.pop("_images_analyzed", len(c.images)) or 0)
    if c.failed_sections:
        logger.warning(
            "AI task %s: partial answer, failed sections=%s",
            c.task_id, ",".join(c.failed_sections),
        )
    # RV-AI-04: nothing the model wrote may carry links or contacts.
    c.result = scrub_model_payload(c.result)
    c.result = apply_ai_market_guardrails(
        c.result,
        title=c.title,
        description=c.description,
        market_median=c.median,
        market_q1=c.q1,
        market_q3=c.q3,
        market_count=c.count,
        similar_listings=c.similar,
        is_negotiable_price=c.is_negotiable_price,
    )
    logger.info("AI async task %s: AI done in %.1fs", c.task_id, _time.monotonic() - _t0)


async def _stage_response(c: _AC) -> None:
    """Build the final response, cache it, and update the task."""
    await _update_task(
        c.cache, c.task_id, user_id=c.user_id, progress=85, stage="building_response",
    )
    logger.info("AI task %s stage=building_response", c.task_id)

    # Build response
    best_pick_ad_id = None
    best_pick_reason = ""
    bp = c.result.get("best_pick") or {}
    if isinstance(bp, dict):
        best_pick_ad_id = bp.get("ad_id")
        best_pick_reason = bp.get("reason", "")

    best_decision: BestAlternativeDecision = choose_best_alternative(
        c.similar,
        target_price=c.price_byn,
        is_negotiable_price=c.is_negotiable_price,
        ai_best_pick_ad_id=best_pick_ad_id,
    )
    best_alternative = best_decision.item
    if not best_pick_reason and best_decision.reason:
        best_pick_reason = best_decision.reason

    if best_alternative:
        best_alternative = {
            "ad_id": best_alternative["ad_id"],
            "title": best_alternative["title"],
            "price_byn": best_alternative["price_byn"],
            "image_url": best_alternative.get("image_url"),
            "link": best_alternative.get("link", ""),
            "deal_score": best_alternative.get("deal_score", 0.0),
            "condition": best_alternative.get("condition"),
            "ai_note": best_pick_reason,
        }

    if c.risk_context is None:
        c.risk_context = MarketplaceRiskContext(summary="", flags=[], score=0.0, hot_words=[])
    final_red_flags = finalize_red_flags(c.result.get("red_flags", []), c.risk_context)
    final_market_context = build_market_context_fallback(
        price_byn=c.price_byn,
        is_negotiable_price=c.is_negotiable_price,
        market_median=c.median,
        similar_listings=c.similar,
        risk_context=c.risk_context,
        ai_market_context=c.result.get("market_context", ""),
        price_reference_scope=c.reference.scope,
        price_reference_label=c.reference.label,
    )
    c.result = complete_analysis_sections(
        result=c.result,
        title=c.title,
        parameters=c.parameters,
        price_byn=c.price_byn,
        market_median=c.median,
        best_alternative=best_alternative,
        risk_context=c.risk_context,
        photo_condition_label=c.photo_condition_label,
        photo_condition_notes=c.photo_condition_notes,
        is_negotiable_price=c.is_negotiable_price,
        red_flags=final_red_flags,
        listing_condition=c.condition,
        market_q1=c.q1,
        market_q3=c.q3,
    )
    # complete_analysis_sections re-merges photo_condition_notes into
    # condition.notes (and into watch_out via _build_fallback_watch_out)
    # using only exact-match dedup. The AI often paraphrases the same
    # observation ("ЛКП имеет блеск" + "ЛКП имеет блеск, без вмятин"),
    # so we re-run the paraphrase-aware dedupe here as a final pass.
    c.result = dedupe_analysis_payload(c.result)
    # SEC-NEW-4: defence-in-depth strip of HTML tags in AI output before cache/return.
    c.result = strip_html_in_payload(c.result)
    c.result["scam_analysis"] = normalize_scam_analysis(c.result.get("scam_analysis"))
    c.result["photo_authenticity"] = normalize_photo_authenticity(
        c.result.get("photo_authenticity"), images_analyzed=c.images_analyzed > 0,
    )
    c.result, final_red_flags = enforce_risk_floor(
        c.result,
        floor=_risk_floor(c),
        red_flags=final_red_flags,
        title=c.title,
        price_byn=c.price_byn,
        market_median=c.median,
        best_alternative=best_alternative,
        is_negotiable_price=c.is_negotiable_price,
    )
    # RV-AI-03: a half-answered analysis is completed from market data
    # above; say so instead of passing it off as a full AI answer.
    source = "partial" if c.failed_sections else "ai"

    resale_potential = _build_resale_potential(c.result.get("resale_potential"))

    response = AIAnalysisResponse(
        ad_id=c.payload.ad_id,
        condition=(
            c.result.get("condition")
            or (
                {
                    "label": normalize_condition_label(c.photo_condition_label),
                    "confidence": 0.72 if c.photo_condition_label else 0.0,
                    "notes": c.photo_condition_notes,
                }
                if c.photo_condition_label or c.photo_condition_notes
                else None
            )
        ),
        fair_price=c.result.get("fair_price"),
        resale_potential=resale_potential,
        watch_out=c.result.get("watch_out", []),
        recommendation=c.result.get("recommendation"),
        similar_listings=c.similar,
        best_alternative=best_alternative,
        meeting_checklist=c.result.get("meeting_checklist", []),
        negotiation_tips=c.result.get("negotiation_tips", []),
        red_flags=final_red_flags,
        scam_analysis=c.result.get("scam_analysis"),
        photo_authenticity=c.result.get("photo_authenticity"),
        market_context=final_market_context,
        price_reference_scope=c.reference.scope,
        price_reference_label=c.reference.label,
        best_pick_reason=best_pick_reason,
        summary=c.result.get("summary", ""),
        source=source,
        disclaimer=DISCLAIMER,
    )

    # Brief intermediate progress so the bar doesn't stall at 85→100
    await _update_task(
        c.cache, c.task_id, user_id=c.user_id, progress=95, stage="building_response",
    )

    serialized = sanitize_public_ai_payload(
        strip_html_in_payload(response.model_dump(by_alias=True))
    )
    if source == "ai":
        # Only complete model answers are shared through the analysis cache.
        await c.cache.set_json(_analysis_cache_key(c.payload), serialized, ttl=_task_ttl())
    else:
        serialized["_ai_warning"] = " ".join(
            _PARTIAL_WARNINGS.get(section, _GENERIC_FALLBACK_WARNING)
            for section in c.failed_sections
        )

    await _update_task(
        c.cache,
        c.task_id,
        user_id=c.user_id,
        status="done",
        progress=100,
        stage="done",
        result=serialized,
        error=None,
    )
    logger.info("AI task %s stage=done", c.task_id)


async def _run_analysis(
    task_id: str,
    payload: AIAnalysisRequest,
    settings: Any,
    cache: Any,
    kufar_client: KufarClient,
    *,
    user_id: int | None = None,
    quota_charge: QuotaCharge | None = None,
    inflight_key: str | None = None,
) -> None:
    """Background coroutine: does the full analysis and updates the task store.

    RV-AI-07: ``quota_charge`` is refunded when the user ends up with
    nothing (listing not found, fallback build failure, cancellation),
    and the ``inflight_key`` singleflight claim taken by the router is
    released however the task ends.
    """
    c = _init_analysis_state(task_id, payload, settings, cache, kufar_client, user_id)
    c.quota_charge = quota_charge
    c.inflight_key = inflight_key
    safe_query = payload.query.replace("\n", " ")[:80]
    logger.info(
        "AI async task %s: starting for ad_id=%d query=%s",
        task_id, payload.ad_id, safe_query,
    )
    try:
        # INF-02: gate concurrent pipelines. Tasks past _BG_TASK_LIMIT
        # were already refused before we got here; this semaphore makes
        # sure the ones that DID get in don't all hit the AI provider at
        # the same instant. Acquire status is reported back as "queued"
        # via the task store so the client polling /ai/task/<id> doesn't
        # see a stuck "pending" while we wait for a slot.
        run_sem = _get_analysis_run_semaphore()
        if run_sem.locked():
            try:
                await _update_task(
                    c.cache, c.task_id,
                    user_id=c.user_id,
                    stage="queued",
                    progress=0,
                )
            except Exception:  # noqa: BLE001 — task store is best-effort
                logger.debug("AI task %s: queued-stage update failed", task_id, exc_info=True)
        try:
            async with run_sem:
                await _stage_search(c)
                _stage_extract(c)
                _stage_scoring(c)
                await _stage_photo(c)
                await _stage_ai(c)
                await _stage_response(c)
        except _AnalysisComplete:
            return
        except TimeoutError as exc:
            await _analysis_fallback(c, exc)
        except _AI_ANALYSIS_ERRORS as exc:
            await _analysis_fallback(c, exc)
    except asyncio.CancelledError:
        # RV-AI-07: the bg-task watchdog (or shutdown) cancelled us. The
        # task used to stay "processing" until its TTL with the quota
        # spent; close it out and give the unit back.
        await _abort_cancelled_analysis(c)
        raise
    finally:
        if c.inflight_key:
            await _release_inflight(c.cache, c.inflight_key, c.task_id)


async def _abort_cancelled_analysis(c: _AC) -> None:
    task = await _get_task(c.cache, c.task_id, user_id=c.user_id)
    if task and task.get("status") in ("done", "error"):
        return
    logger.warning(
        "AI task %s: cancelled after %.1fs for ad_id=%d",
        c.task_id, _time.monotonic() - c.started_at, c.payload.ad_id,
    )
    await _update_task(
        c.cache, c.task_id, user_id=c.user_id,
        status="error", stage="error",
        error="Анализ прерван и не засчитан в лимит. Попробуйте ещё раз.",
    )
    await _refund_task_quota(c)
