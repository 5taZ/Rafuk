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
from api.limiter import limiter
from api.routers.ai_analysis import (
    _AI_ANALYSIS_ERRORS,
    _check_ai_available,
    _check_ai_consent,
    _check_ai_entitlement,
    _check_rate_limit,
    _coerce_string_list,
    _log_ai_audit,
)
from api.schemas import (
    AI_LISTING_PHOTO_MAX_CHARS,
    AI_LISTING_PHOTO_MAX_COUNT,
    AIListingAssistantRequest,
    AIListingAssistantResponse,
    AIListingCompetitor,
    AIListingPriceTier,
    AIListingPricing,
    AINegotiationCounter,
)
from api.services.aggregator import (
    compute_price_stats,
    extract_prices,
    filter_ads_for_accessory_category,
    normalize_price_byn,
)
from api.services.ai_listing_guardrails import (
    normalize_listing_pricing,
    thin_market_warning,
)
from api.services.ai_sanitize import strip_html_in_payload
from api.services.ai_service import (
    CATEGORY_HINTS,
    detect_category,
    normalize_condition_label,
    sanitize_user_text,
)
from api.services.client_ip import get_client_ip
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
        market_median_byn=(
            float(market_anchors["median"]) if market_anchors.get("median") is not None else None
        ),
        market_q1_byn=(
            float(market_anchors["q1"]) if market_anchors.get("q1") is not None else None
        ),
        market_q3_byn=(
            float(market_anchors["q3"]) if market_anchors.get("q3") is not None else None
        ),
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


def _coerce_competitors(
    raw: Any,
    *,
    dataset_competitors: list[dict[str, Any]] | None = None,
) -> list[AIListingCompetitor]:
    items: list[AIListingCompetitor] = []
    if not isinstance(raw, list):
        return items
    # Build lookup from dataset competitors for image_url / link enrichment
    ds_lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for ds in dataset_competitors or []:
        ds_title = str(ds.get("title") or "").strip().lower()[:60]
        ds_price = int(round(float(ds.get("price_byn") or 0)))
        if ds_title:
            ds_lookup[(ds_title, ds_price)] = ds

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        price = _coerce_price(entry.get("price_byn") or entry.get("price"))
        advantage = str(entry.get("advantage") or "").strip()
        if not title or price is None:
            continue
        image_url: str | None = None
        link: str = ""
        match = ds_lookup.get((title.lower()[:60], int(round(price))))
        if not match:
            # Fuzzy: try matching by price only among same-price items
            for key, ds in ds_lookup.items():
                if abs(int(round(ds.get("price_byn") or 0)) - int(round(price))) > 2:
                    continue
                src_tokens = set(title.lower().split())
                ds_tokens = set(key[0].split())
                shorter = min(len(src_tokens), len(ds_tokens))
                if shorter == 0:
                    continue
                overlap = len(src_tokens & ds_tokens) / shorter
                if overlap >= 0.6:
                    match = ds
                    break
        if match:
            image_url = match.get("image_url")
            raw_link = match.get("link", "")
            if raw_link and raw_link.startswith(("https://www.kufar.by/", "https://kufar.by/", "https://re.kufar.by/")):
                link = raw_link
        items.append(AIListingCompetitor(
            title=title[:120],
            price_byn=price,
            advantage=advantage[:300],
            image_url=image_url,
            link=link,
        ))
        if len(items) >= 4:
            break
    return items


# A single user-uploaded photo capped at ~1.5MB of base64 (~1MB binary).
_LISTING_PHOTO_MAX_BYTES = AI_LISTING_PHOTO_MAX_CHARS
_LISTING_PHOTO_RE = _re.compile(
    r"^data:image/(jpeg|png|webp|jpg);base64,[A-Za-z0-9+/=]+$",
)


def _coerce_listing_photos(raw: list[str] | None) -> list[str]:
    """Validate seller-uploaded photos: keep only well-formed data URLs.

    OPUS-9: length check runs BEFORE the regex. Pydantic already
    caps each entry at AI_LISTING_PHOTO_MAX_CHARS at the schema
    layer, but that's a maximum; a request that just barely fits
    the schema (~1.5 MB × 4) still hits this regex 4 times — and a
    base64 regex on a 1.5 MB string is meaningfully slower than
    the cheap len() comparison. Filter oversized entries first so
    we never feed the regex more than the configured budget.
    """
    if not raw:
        return []
    cleaned: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        if len(entry) > _LISTING_PHOTO_MAX_BYTES:
            continue
        if not _LISTING_PHOTO_RE.match(entry):
            continue
        cleaned.append(entry)
        if len(cleaned) >= AI_LISTING_PHOTO_MAX_COUNT:
            break
    return cleaned


def _listing_assistant_cache_key(
    payload: AIListingAssistantRequest,
    photos: list[str],
    *,
    user_id: int,
) -> str:
    """Stable per-user cache key derived from the canonical user input."""
    canonical_title = " ".join((payload.title or "").lower().split())
    canonical_notes = " ".join((payload.extra_notes or "").lower().split())
    photo_hashes = [hashlib.sha256(p.encode("utf-8")).hexdigest()[:16] for p in photos]
    parts = [
        ("v", "3"),
        ("title", canonical_title),
        ("category", str(payload.category or "")),
        ("condition", (payload.condition or "").strip().lower()),
        (
            "price",
            f"{int(round(payload.draft_price_byn))}"
            if payload.draft_price_byn is not None else "",
        ),
        ("negot", "1" if payload.is_negotiable else "0"),
        ("seller_goal", payload.seller_goal or ""),
        ("notes", canonical_notes),
        ("photos", ",".join(photo_hashes)),
    ]
    serialised = "|".join(f"{k}={v}" for k, v in parts)
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
    return f"ai_listing:u{int(user_id)}:{digest}"


def _build_listing_competitors(dataset_ads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick a diverse, priced subset of ads to use as competitor context."""

    _pricing_params = {
        "storage", "ram", "memory", "generation", "generation_name",
        "screen_size", "diagonal", "processor", "cpu", "gpu",
        "drive_type", "ssd_capacity", "hdd_capacity",
        "mileage", "engine_volume", "year", "year_of_manufacture",
        "rooms", "area", "floor", "total_floors",
        "frame_size", "wheel_size",
    }

    priced: list[tuple[dict[str, Any], float]] = []
    for ad in dataset_ads:
        price = normalize_price_byn(ad.get("price_byn"))
        if price is not None:
            priced.append((ad, price))
    out: list[dict[str, Any]] = []
    for ad, price in priced[:24]:
        title_text = str(ad.get("subject") or ad.get("title") or "").strip()
        params = ad.get("ad_parameters") or []
        seller_label = ""
        condition = ""
        key_params: list[str] = []
        for p in params:
            label = str(p.get("p") or "").lower()
            value = str(p.get("v") or p.get("vl") or "").strip()
            if not value:
                continue
            if label == "seller_type" and value:
                seller_label = value
            elif label in {"condition", "cond"} and value:
                condition = value
            elif label in _pricing_params:
                key_params.append(f"{label}={value}")
        out.append(
            {
                "title": title_text[:120],
                "price_byn": price,
                "seller_type": seller_label,
                "condition": condition,
                "parameters": key_params[:5],
                "image_url": _first_image_url(ad),
                "link": ad.get("ad_link", f"https://www.kufar.by/item/{ad.get('ad_id', '')}"),
            }
        )
        if len(out) >= 8:
            break
    return out


_IMAGE_BASE_URL = "https://rms.kufar.by/v1/gallery/"


def _first_image_url(ad: dict[str, Any]) -> str | None:
    """Extract the first image thumbnail URL from a Kufar ad."""
    images = ad.get("images") or []
    for img in images:
        path = img.get("path", "")
        if path:
            return f"{_IMAGE_BASE_URL}{path}"
    return None


def _build_listing_fallback_response(
    *,
    payload: AIListingAssistantRequest,
    title: str,
    market_anchors: dict[str, float | int | None],
    competitors: list[dict[str, Any]],
) -> AIListingAssistantResponse:
    safe_title = sanitize_user_text(title, max_length=160) or title[:160]
    safe_condition = (
        sanitize_user_text(payload.condition, max_length=64) if payload.condition else ""
    )
    safe_notes = (
        sanitize_user_text(payload.extra_notes, max_length=320) if payload.extra_notes else ""
    )
    median = _coerce_price(market_anchors.get("median"))
    q1 = _coerce_price(market_anchors.get("q1"))
    q3 = _coerce_price(market_anchors.get("q3"))
    count = int(market_anchors.get("count") or 0)
    draft = _coerce_price(payload.draft_price_byn)
    anchor = median or draft

    pricing = AIListingPricing(
        market_median_byn=median,
        market_q1_byn=q1,
        market_q3_byn=q3,
        competing_count=count,
    )
    if anchor:
        fast = q1 or round(anchor * 0.92, 2)
        market = median or anchor
        patient = q3 or round(anchor * 1.08, 2)
        pricing.fast = AIListingPriceTier(
            label="Быстро",
            price_byn=fast,
            weeks_to_sell="быстрее рынка",
            reasoning="Ниже основного ориентира, чтобы быстрее получить отклики.",
        )
        pricing.market = AIListingPriceTier(
            label="Рынок",
            price_byn=market,
            weeks_to_sell="средний темп",
            reasoning="Базовый ориентир по текущей выборке Kufar.",
        )
        pricing.patient = AIListingPriceTier(
            label="Терпеливо",
            price_byn=patient,
            weeks_to_sell="дольше",
            reasoning="Верхний ориентир, если состояние и комплект сильные.",
        )
        pricing.floor_byn = round(fast * 0.90, 2)

    description_bits = [f"Продаю {safe_title}."]
    if safe_condition:
        description_bits.append(f"Состояние: {safe_condition}.")
    if safe_notes:
        description_bits.append(safe_notes.rstrip(".") + ".")
    description_bits.append(
        "Перед продажей можно уточнить комплект, состояние и удобное время встречи."
    )
    if payload.is_negotiable:
        description_bits.append("Разумный торг обсуждается при осмотре.")

    selling_points = ["Актуальное состояние и комплект лучше показать на фото"]
    if count:
        selling_points.append(f"Цена сверена с {count} похожими объявлениями Kufar")
    if safe_condition:
        selling_points.append(f"Состояние: {safe_condition}")
    if payload.is_negotiable:
        selling_points.append("Можно заранее обозначить границы торга")

    fallback_competitors = []
    for item in competitors[:4]:
        price = _coerce_price(item.get("price_byn"))
        if price is None:
            continue
        fallback_competitors.append(AIListingCompetitor(
            title=str(item.get("title") or "")[:120],
            price_byn=price,
            advantage="Ориентир для сравнения цены",
            image_url=item.get("image_url"),
            link=str(item.get("link") or ""),
        ))

    if count and median:
        market_summary = (
            "AI-сервис сейчас недоступен, поэтому показан рыночный черновик "
            f"по данным Kufar: {count} похожих объявлений, медианный ориентир "
            f"около {round(median)} BYN."
        )
    elif draft:
        market_summary = (
            "AI-сервис сейчас недоступен, поэтому показан базовый черновик от вашей цены. "
            "Уточните название или категорию, чтобы получить больше рыночных аналогов."
        )
    else:
        market_summary = (
            "AI-сервис сейчас недоступен, поэтому показан базовый черновик. "
            "Добавьте цену, состояние или больше характеристик для точнее ориентира."
        )

    playbook = []
    if pricing.floor_byn:
        playbook.append(AINegotiationCounter(
            scenario="Покупатель предлагает сильно ниже ориентира",
            response=(
                f"Готов обсудить торг, но ниже {round(pricing.floor_byn)} BYN "
                "не планирую — цена сверена с рынком."
            ),
        ))
    elif payload.is_negotiable:
        playbook.append(AINegotiationCounter(
            scenario="Покупатель просит скидку",
            response="Можем обсудить разумный торг после осмотра и понимания условий сделки.",
        ))

    return AIListingAssistantResponse(
        title_suggestion=safe_title[:200],
        description=" ".join(description_bits)[:2000],
        description_short=f"{safe_title}. Состояние и комплект уточню в сообщениях."[:400],
        selling_points=selling_points[:6],
        pricing=pricing,
        negotiation_playbook=playbook,
        photo_tips=[
            "Сделайте общий кадр при хорошем дневном свете",
            "Покажите крупно состояние, комплект и возможные следы использования",
            "Добавьте фото серийника или маркировки, если это безопасно",
        ],
        competitors=fallback_competitors,
        market_summary=market_summary[:600],
    )


# ── Endpoint ──────────────────────────────────────────────────────────────


@router.post("/listing-assistant", response_model=AIListingAssistantResponse)
# PR-02: edge cap on top of the per-user AI quota guard. Listing
# assistant is the heaviest AI endpoint (multi-photo upload + a
# parallel Gemini call + Kufar strict-then-broad fan-out), so the
# absolute call cap is intentionally tight — the cache fast-path
# is the right answer for repeat clicks on the same draft.
@limiter.limit("10/minute")
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
    await _check_ai_entitlement(request, _user.user_id, endpoint="listing")

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()
    audit_model = getattr(ai, "listing_assistant_model", settings.ai_model)

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Введите название товара")

    photos = _coerce_listing_photos(payload.photos)
    # OPUS-17: snapshot client IP once per request — both audit
    # paths (cache hit / miss) write the same IP.
    client_ip = get_client_ip(request)

    # OPUS-6: cache lookup BEFORE rate-limit + AI-audit so a hit
    # costs no quota and is audited as cached=True — same shape as
    # /analyze. Earlier order (rate-limit → audit → cache) charged
    # a quota point on every cache hit and logged the audit row
    # without the cached marker, hiding cache effectiveness from
    # ops and slowly draining the user's hourly budget for free.
    cache = get_cache(request)
    cache_key = _listing_assistant_cache_key(payload, photos, user_id=_user.user_id)
    cached = await cache.get_json(cache_key)
    if isinstance(cached, dict):
        try:
            response = AIListingAssistantResponse.model_validate(cached)
        except ValidationError:
            logger.info("Listing assistant cache hit was stale, recomputing")
            await cache.delete(cache_key)
        else:
            await _log_ai_audit(
                request.app.state.session_factory,
                telegram_user_id=_user.user_id,
                endpoint="listing_assistant",
                query=payload.title,
                model=audit_model,
                cached=True,
                ip_address=client_ip,
            )
            return response

    await _check_rate_limit(request, _user.user_id, endpoint="listing")

    # AI audit trail (Belarus Law No. 99-З) — cache miss path.
    await _log_ai_audit(
        request.app.state.session_factory,
        telegram_user_id=_user.user_id,
        endpoint="listing_assistant",
        query=payload.title,
        model=audit_model,
        ip_address=client_ip,
    )

    try:
        dataset = await load_query_dataset(
            query=title,
            currency="BYN",
            strict_search=True,
            settings=settings,
            client=kufar_client,
            category=payload.category,
        )
    except KufarAPIError as exc:
        logger.warning("Listing assistant: Kufar strict search failed: %s", exc)
        dataset = None

    if dataset is None or not dataset.ads:
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
            logger.warning("Listing assistant: Kufar broad search also failed: %s", exc)
            dataset = None

    market_stats = dataset.price_stats if dataset is not None else None

    # Detect category early so we can filter ads for accessories.
    # When searching "чехол iPhone", Kufar returns phones + cases;
    # filtering by price cap keeps only accessory-priced listings.
    parameters = []
    if payload.condition:
        parameters.append({"label": "condition", "value": payload.condition})
    category_key = detect_category(title, parameters)
    filtered_ads = dataset.ads if dataset is not None else []
    if dataset is not None and category_key in {
        "phone_accessory", "auto_accessory", "computer_accessory",
        "photo_accessory", "gaming_accessory", "home_accessory",
        "bicycle_accessory", "watch_accessory",
    }:
        filtered_ads = filter_ads_for_accessory_category(dataset.ads, category_key)
        if filtered_ads is not dataset.ads:
            filtered_prices = extract_prices(filtered_ads)
            if filtered_prices:
                market_stats = compute_price_stats(filtered_prices)

    competitors = _build_listing_competitors(filtered_ads)

    market_anchors: dict[str, float | int | None] = {
        "median": market_stats.median if market_stats and market_stats.count else None,
        "q1": market_stats.q1 if market_stats and market_stats.count else None,
        "q3": market_stats.q3 if market_stats and market_stats.count else None,
        "min": market_stats.min if market_stats and market_stats.count else None,
        "max": market_stats.max if market_stats and market_stats.count else None,
        "count": market_stats.count if market_stats else 0,
    }

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
                seller_goal=payload.seller_goal,
            ),
            timeout=timeout_s,
        )
    except (TimeoutError, *_AI_ANALYSIS_ERRORS) as exc:
        logger.warning("Listing assistant AI failed, returning market fallback: %s", exc)
        return _build_listing_fallback_response(
            payload=payload,
            title=title,
            market_anchors=market_anchors,
            competitors=competitors,
        )

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
        competitors=_coerce_competitors(
            ai_result.get("competitors"), dataset_competitors=competitors,
        ),
        market_summary=market_summary[:600],
    )

    # SEC-NEW-4: defence-in-depth strip of HTML tags in AI output before cache/return.
    serialized = strip_html_in_payload(response.model_dump(mode="json"))
    cache_ttl = int(getattr(settings, "ai_listing_assistant_cache_ttl", 3600) or 3600)
    await cache.set_json(cache_key, serialized, ttl=cache_ttl)
    return AIListingAssistantResponse.model_validate(serialized)
