"""AI Listing Assistant router — helps sellers draft listings with market data."""

from __future__ import annotations

import hashlib
import logging
import re as _re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from api.config import get_settings
from api.dependencies import get_cache, get_kufar_client, get_telegram_user
from api.routers.ai_analysis import (
    _AI_ANALYSIS_ERRORS,
    _check_ai_available,
    _check_ai_consent,
    _check_rate_limit,
    _log_ai_audit,
)
from api.schemas import (
    AIListingAssistantRequest,
    AIListingAssistantResponse,
    AIListingPriceTier,
    AIListingPricing,
    AINegotiationCounter,
)
from api.services.ai_listing_guardrails import (
    normalize_listing_pricing,
    thin_market_warning,
)
from api.services.ai_service import (
    CATEGORY_HINTS,
    detect_category,
    normalize_condition_label,
)
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.query_pipeline import load_query_dataset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Coercion helpers ──────────────────────────────────────────────────────


def _coerce_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return round(num, 2)


def _coerce_price_tier(raw: Any) -> AIListingPriceTier | None:
    if not isinstance(raw, dict):
        return None
    price = _coerce_price(raw.get("price_byn") or raw.get("price"))
    if price is None:
        return None
    label = str(raw.get("label") or "").strip()[:64]
    weeks = str(raw.get("weeks_to_sell") or raw.get("weeks") or "").strip()[:32]
    reasoning = str(raw.get("reasoning") or "").strip()[:600]
    return AIListingPriceTier(
        label=label,
        price_byn=price,
        weeks_to_sell=weeks,
        reasoning=reasoning,
    )


def _coerce_pricing(
    raw: Any, *, market_anchors: dict[str, float | int | None]
) -> AIListingPricing:
    pricing = AIListingPricing(
        market_median_byn=market_anchors.get("median"),  # type: ignore[arg-type]
        market_q1_byn=market_anchors.get("q1"),  # type: ignore[arg-type]
        market_q3_byn=market_anchors.get("q3"),  # type: ignore[arg-type]
        competing_count=int(market_anchors.get("count") or 0),
    )
    if not isinstance(raw, dict):
        return pricing
    pricing.fast = _coerce_price_tier(raw.get("fast"))
    pricing.market = _coerce_price_tier(raw.get("market"))
    pricing.patient = _coerce_price_tier(raw.get("patient"))
    pricing.floor_byn = _coerce_price(raw.get("floor_byn") or raw.get("floor"))
    return pricing


def _coerce_negotiation(raw: Any) -> list[AINegotiationCounter]:
    items: list[AINegotiationCounter] = []
    if not isinstance(raw, list):
        return items
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        scenario = str(entry.get("scenario") or "").strip()
        response = str(entry.get("response") or "").strip()
        if not scenario or not response:
            continue
        items.append(AINegotiationCounter(scenario=scenario[:200], response=response[:600]))
        if len(items) >= 6:
            break
    return items


def _coerce_string_list(raw: Any, *, limit: int, max_len: int) -> list[str]:
    items: list[str] = []
    if not isinstance(raw, list):
        return items
    seen: set[str] = set()
    for entry in raw:
        text = _re.sub(r"\s+", " ", str(entry or "").strip())
        if not text:
            continue
        text = text[:max_len]
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= limit:
            break
    return items


# A single user-uploaded photo capped at ~1.5MB of base64 (~1MB binary).
_LISTING_PHOTO_MAX_BYTES = 1_500_000
_LISTING_PHOTO_RE = _re.compile(
    r"^data:image/(jpeg|png|webp|jpg);base64,[A-Za-z0-9+/=]+$",
)


def _coerce_listing_photos(raw: list[str] | None) -> list[str]:
    """Validate seller-uploaded photos: keep only well-formed data URLs."""
    if not raw:
        return []
    cleaned: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        if not _LISTING_PHOTO_RE.match(entry):
            continue
        if len(entry) > _LISTING_PHOTO_MAX_BYTES:
            continue
        cleaned.append(entry)
        if len(cleaned) >= 4:
            break
    return cleaned


def _listing_assistant_cache_key(
    payload: AIListingAssistantRequest,
    photos: list[str],
) -> str:
    """Stable cache key derived from the canonical user input."""
    canonical_title = " ".join((payload.title or "").lower().split())
    canonical_notes = " ".join((payload.extra_notes or "").lower().split())
    photo_hashes = [hashlib.sha256(p.encode("utf-8")).hexdigest()[:16] for p in photos]
    parts = [
        ("v", "1"),
        ("title", canonical_title),
        ("category", str(payload.category or "")),
        ("condition", (payload.condition or "").strip().lower()),
        ("price", f"{int(round(payload.draft_price_byn))}" if payload.draft_price_byn else ""),
        ("negot", "1" if payload.is_negotiable else "0"),
        ("notes", canonical_notes),
        ("photos", ",".join(photo_hashes)),
    ]
    serialised = "|".join(f"{k}={v}" for k, v in parts)
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
    return f"ai_listing:{digest}"


def _build_listing_competitors(dataset_ads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick a diverse, priced subset of ads to use as competitor context."""
    from api.services.aggregator import normalize_price_byn

    priced = [
        ad for ad in dataset_ads if normalize_price_byn(ad.get("price_byn")) is not None
    ]
    out: list[dict[str, Any]] = []
    for ad in priced[:24]:
        price = normalize_price_byn(ad.get("price_byn"))
        if price is None:
            continue
        title_text = str(ad.get("subject") or ad.get("title") or "").strip()
        params = ad.get("ad_parameters") or []
        seller_label = ""
        condition = ""
        for p in params:
            label = str(p.get("p") or "").lower()
            value = str(p.get("v") or p.get("vl") or "").strip()
            if label == "seller_type" and value:
                seller_label = value
            elif label in {"condition", "cond"} and value:
                condition = value
        out.append(
            {
                "title": title_text[:120],
                "price_byn": price,
                "seller_type": seller_label,
                "condition": condition,
            }
        )
        if len(out) >= 8:
            break
    return out


# ── Endpoint ──────────────────────────────────────────────────────────────


@router.post("/listing-assistant", response_model=AIListingAssistantResponse)
async def listing_assistant(
    payload: AIListingAssistantRequest,
    request: Request,
    _user=Depends(get_telegram_user),
    kufar_client: KufarClient = Depends(get_kufar_client),
):
    """Help a seller draft a listing — title, description, price tiers, anti-lowball playbook.

    Uses the same Kufar market data that powers buyer research, so the
    suggested price tiers are grounded in real Q1/median/Q3 numbers
    rather than model guesses.
    """
    import asyncio

    ai = _check_ai_available()
    await _check_ai_consent(request, _user.user_id)
    await _check_rate_limit(request, _user.user_id)

    # AI audit trail (Belarus Law No. 91-Z)
    await _log_ai_audit(
        request.app.state.session_factory,
        telegram_user_id=_user.user_id,
        endpoint="listing_assistant",
        query=payload.title,
        model=get_settings().ai_model,
    )

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Введите название товара")

    photos = _coerce_listing_photos(payload.photos)

    cache = get_cache(request)
    cache_key = _listing_assistant_cache_key(payload, photos)
    cached = await cache.get_json(cache_key)
    if isinstance(cached, dict):
        try:
            return AIListingAssistantResponse.model_validate(cached)
        except ValidationError:
            logger.info("Listing assistant cache hit was stale, recomputing")

    try:
        dataset = await load_query_dataset(
            query=title,
            currency="BYN",
            strict_search=False,
            settings=settings,
            client=kufar_client,
            category=payload.category,
        )
    except KufarAPIError as exc:
        logger.warning("Listing assistant: Kufar fetch failed: %s", exc)
        dataset = None

    market_stats = dataset.price_stats if dataset is not None else None
    competitors = _build_listing_competitors(dataset.ads) if dataset is not None else []

    market_anchors: dict[str, float | int | None] = {
        "median": market_stats.median if market_stats and market_stats.count else None,
        "q1": market_stats.q1 if market_stats and market_stats.count else None,
        "q3": market_stats.q3 if market_stats and market_stats.count else None,
        "min": market_stats.min if market_stats and market_stats.count else None,
        "max": market_stats.max if market_stats and market_stats.count else None,
        "count": market_stats.count if market_stats else 0,
    }

    category_key = detect_category(title)
    category_meta = CATEGORY_HINTS.get(category_key) or {}

    timeout_s = int(getattr(settings, "ai_listing_assistant_timeout", 120) or 120)
    try:
        ai_result = await asyncio.wait_for(
            ai.generate_listing(
                title=title,
                condition=normalize_condition_label(payload.condition) or payload.condition,
                is_negotiable=payload.is_negotiable,
                draft_price_byn=payload.draft_price_byn,
                extra_notes=payload.extra_notes,
                market_median=market_anchors["median"],
                market_q1=market_anchors["q1"],
                market_q3=market_anchors["q3"],
                market_min=market_anchors["min"],
                market_max=market_anchors["max"],
                market_count=int(market_anchors["count"] or 0),
                similar_listings=competitors or None,
                category_hint=category_meta.get("category_hints"),
                category_bargain_hint=category_meta.get("bargain_hint"),
                photo_data_urls=photos or None,
            ),
            timeout=timeout_s,
        )
    except (TimeoutError, *_AI_ANALYSIS_ERRORS) as exc:
        logger.error("Listing assistant failed: %s", exc)
        err_msg = "AI сервис недоступен"
        text = str(exc)
        if "429" in text or "RESOURCE_EXHAUSTED" in text:
            err_msg = "Лимит AI-анализов исчерпан. Попробуйте через минуту."
        raise HTTPException(status_code=502, detail=err_msg) from None

    raw_pricing = ai_result.get("pricing") if isinstance(ai_result.get("pricing"), dict) else {}
    normalised_pricing = normalize_listing_pricing(
        raw_pricing,
        market_median=market_anchors["median"],
        market_q1=market_anchors["q1"],
        market_q3=market_anchors["q3"],
        market_min=market_anchors["min"],
        market_max=market_anchors["max"],
        market_count=int(market_anchors["count"] or 0),
    )
    pricing = _coerce_pricing(normalised_pricing, market_anchors=market_anchors)

    market_summary = str(ai_result.get("market_summary") or "").strip()
    warning = thin_market_warning(int(market_anchors["count"] or 0))
    if warning:
        market_summary = f"{warning}\n\n{market_summary}".strip() if market_summary else warning

    response = AIListingAssistantResponse(
        title_suggestion=str(ai_result.get("title_suggestion") or "").strip()[:200],
        description=str(ai_result.get("description") or "").strip()[:2000],
        description_short=str(ai_result.get("description_short") or "").strip()[:400],
        selling_points=_coerce_string_list(ai_result.get("selling_points"), limit=6, max_len=160),
        pricing=pricing,
        negotiation_playbook=_coerce_negotiation(ai_result.get("negotiation_playbook")),
        photo_tips=_coerce_string_list(ai_result.get("photo_tips"), limit=5, max_len=140),
        market_summary=market_summary[:600],
    )

    cache_ttl = int(getattr(settings, "ai_listing_assistant_cache_ttl", 3600) or 3600)
    await cache.set_json(cache_key, response.model_dump(mode="json"), ttl=cache_ttl)
    return response
