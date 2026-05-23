from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.schemas import ListingDetailResponse, ListingField, ListingItem
from api.services.aggregator import (
    PriceStats,
    cluster_price_stats,
    compute_price_vs_median,
    compute_price_vs_reference,
    detect_price_type,
    get_category_label,
    get_param,
    normalize_price_byn,
    resolve_price_reference,
)
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import LiquidityInsight, compute_flip_estimates
from api.services.market_signals import (
    anomaly_labels,
    area_label,
    detect_anomaly_flags,
    fair_price_band,
    fair_price_label,
    region_label,
)
from api.services.reseller_tools import analyze_query_text, compute_deal_score
from api.services.risk_detector import detect_risks

IMAGE_BASE_URL = "https://rms.kufar.by/v1/gallery/"
IGNORED_AD_PARAMETER_KEYS = {"users_synonyms"}

PII_PARAMETER_KEYS = frozenset(
    {
        "phone",
        "phone_hidden",
        "contact_person",
        "email",
        "company_name",
        "company_address",
        "vat_number",
        "user_id",
        "username",
        "address",
        "legal_name",
    }
)
SENSITIVE_AD_PARAMETER_KEYS = IGNORED_AD_PARAMETER_KEYS | PII_PARAMETER_KEYS
SIMILAR_REFERENCE_LABEL = "Похожие объявления"
MAX_DISPLAY_DELTA_PERCENT = 200.0
MAX_NOISY_REFERENCE_SPREAD = 1.25
MAX_NOISY_REFERENCE_DELTA_PERCENT = 75.0


@dataclass(slots=True)
class _PriceContext:
    price_delta: float | None
    active_reference: PriceStats
    reference_scope: str
    reference_label: str | None
    reliable: bool


def _reference_spread(stats: PriceStats) -> float:
    if stats.median <= 0 or stats.q3 < stats.q1:
        return float("inf")
    return (stats.q3 - stats.q1) / stats.median


def _cluster_reference_usable(stats: PriceStats) -> bool:
    return stats.count >= 3 and stats.median > 0 and _reference_spread(stats) <= 1.5


def _display_delta_reliable(
    ad: dict[str, Any],
    delta: float | None,
    stats: PriceStats,
    *,
    cluster_applied: bool,
) -> bool:
    if delta is None or stats.median <= 0:
        return False
    price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    if price_byn is None:
        return False
    if price_byn == 0.0 and delta <= -99.0:
        return True
    if stats.count < 3:
        return abs(delta) < 0.5
    if abs(delta) > MAX_DISPLAY_DELTA_PERCENT:
        return False
    if cluster_applied:
        return True
    spread = _reference_spread(stats)
    return not (
        spread > MAX_NOISY_REFERENCE_SPREAD
        and abs(delta) > MAX_NOISY_REFERENCE_DELTA_PERCENT
    )


def _neutral_reference_stats(ad: dict[str, Any], fallback: PriceStats) -> PriceStats:
    price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    anchor = price_byn if price_byn is not None else fallback.median
    return PriceStats(
        mean=anchor,
        median=anchor,
        q1=anchor,
        q3=anchor,
        min=anchor,
        max=anchor,
        count=max(fallback.count, 3),
    )


def _price_context(
    ad: dict[str, Any],
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None,
    cluster_stats: PriceStats | None = None,
) -> _PriceContext:
    reference = resolve_price_reference(ad, market_stats, category_price_stats)
    price_delta = compute_price_vs_reference(ad, market_stats, category_price_stats)
    active_reference = reference.stats
    reference_scope = reference.scope
    reference_label = reference.label
    cluster_applied = False

    if cluster_stats is not None and _cluster_reference_usable(cluster_stats):
        price_delta = compute_price_vs_median(ad, cluster_stats.median)
        active_reference = cluster_stats
        reference_scope = "similar"
        reference_label = SIMILAR_REFERENCE_LABEL
        cluster_applied = True

    reliable = _display_delta_reliable(
        ad,
        price_delta,
        active_reference,
        cluster_applied=cluster_applied,
    )
    return _PriceContext(
        price_delta=price_delta if reliable else None,
        active_reference=active_reference,
        reference_scope=reference_scope,
        reference_label=reference_label,
        reliable=reliable,
    )


def _stringify_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts) or None
    return str(value).strip() or None


def _parameter_value(item: dict[str, Any]) -> str | None:
    display_value = _stringify_value(item.get("vl"))
    if display_value:
        return display_value
    return _stringify_value(item.get("v"))


def image_url(image: dict[str, Any]) -> str | None:
    path = image.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    return f"{IMAGE_BASE_URL}{path}"


def first_image_url(ad: dict[str, Any]) -> str | None:
    for image in ad.get("images", []):
        url = image_url(image)
        if url:
            return url
    return None


def collect_fields(
    items: list[dict[str, Any]],
    ignored: set[str] | None = None,
) -> list[ListingField]:
    ignored = ignored or set()
    fields: list[ListingField] = []
    for item in items:
        key = item.get("p")
        if key in ignored:
            continue
        label = _stringify_value(item.get("pl"))
        value = _parameter_value(item)
        if not label or not value:
            continue
        fields.append(ListingField(label=label, value=value))
    return fields


def extract_seller_rating(ad: dict[str, Any]) -> float | None:
    for param in ad.get("account_parameters", []):
        p = param.get("p", "")
        if p in ("retention_rate", "positive_feedback_percent", "seller_rating"):
            val = param.get("v") or param.get("vl")
            try:
                return round(float(val), 1)
            except (TypeError, ValueError):
                continue
    # Also check top-level field
    for key in ("retention_rate", "seller_rating"):
        val = ad.get(key)
        if val is not None:
            try:
                return round(float(val), 1)
            except (TypeError, ValueError):
                continue
    return None


def build_listing_detail(
    ad: dict[str, Any],
    query: str,
    currency: str,
    rates: dict[str, float],
    currency_service: CurrencyService,
    median_byn: float,
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None = None,
    liquidity: LiquidityInsight | None = None,
    cluster_stats: PriceStats | None = None,
) -> ListingDetailResponse:
    raw_price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    price_type = (
        detect_price_type(ad) if raw_price_byn is None or raw_price_byn == 0.0 else "fixed"
    )
    price_byn = raw_price_byn
    description = _stringify_value(ad.get("body")) or _stringify_value(ad.get("body_short"))
    price_ctx = _price_context(ad, market_stats, category_price_stats, cluster_stats)
    # For negotiable listings the price is unknown — we must NOT report
    # a delta vs market (the metric function returns 0.0 in that case,
    # which the UI would render as "≈ по рынку", masking the unknown
    # price as "fair"). Surface it as None so the badge is suppressed.
    price_delta: float | None = None if price_type == "negotiable" else price_ctx.price_delta
    fair_band = fair_price_band(price_delta)
    flags = detect_anomaly_flags(ad, price_ctx.active_reference) if price_ctx.reliable else []
    query_insights = analyze_query_text(query)
    deal_reference = (
        price_ctx.active_reference
        if price_ctx.reliable
        else _neutral_reference_stats(ad, price_ctx.active_reference)
    )
    deal_score = compute_deal_score(
        ad,
        query=query,
        market_stats=deal_reference,
    )

    price = (
        currency_service.convert_from_byn(price_byn, currency, rates)
        if price_byn is not None
        else None
    )

    return ListingDetailResponse(
        query=query,
        normalized_query=query_insights.normalized_query,
        config_summary=query_insights.config_summary,
        storage_gb=query_insights.storage_gb,
        ram_gb=query_insights.ram_gb,
        ad_id=int(ad.get("ad_id", 0)),
        title=str(ad.get("subject", "")),
        price=price,
        price_type=price_type,
        currency=currency,
        link=str(ad.get("ad_link", "")),
        list_time=ad.get("list_time"),
        region_id=ad.get("region_id"),
        region_name=region_label(ad),
        area_name=area_label(ad),
        category=get_category_label(ad),
        condition=get_param(ad, "condition"),
        seller_type=(
            get_param(ad, "seller_type")
            or ("shop" if ad.get("company_ad") else "private")
        ),
        price_vs_median=price_delta,
        price_reference_scope=price_ctx.reference_scope,
        price_reference_label=price_ctx.reference_label,
        fair_price_band=fair_band,
        fair_price_label=fair_price_label(fair_band),
        anomaly_flags=flags,
        anomaly_labels=anomaly_labels(flags),
        deal_score=deal_score.score,
        deal_verdict=deal_score.verdict if price_delta is not None else None,
        deal_reasons=deal_score.reasons,
        price_byn=price_byn,
        liquidity=liquidity,
        flip_estimates=compute_flip_estimates(ad, market_stats),
        company_ad=bool(ad.get("company_ad")),
        phone_hidden=bool(ad.get("phone_hidden", True)),
        description=description,
        images=[url for image in ad.get("images", []) if (url := image_url(image))],
        parameters=collect_fields(
            ad.get("ad_parameters", []), ignored=SENSITIVE_AD_PARAMETER_KEYS,
        ),
        seller_fields=collect_fields(ad.get("account_parameters", []), ignored=PII_PARAMETER_KEYS),
        seller_rating=extract_seller_rating(ad),
        risk_factors=detect_risks(ad, market_stats={"median": price_ctx.active_reference.median}),
    )


def compute_listing_sort_key(
    ad: dict[str, Any],
    *,
    query: str,
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None = None,
    cluster_cache: dict[int, PriceStats | None] | None = None,
) -> tuple[float, float, str]:
    raw_price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    price_type = (
        detect_price_type(ad) if raw_price_byn is None or raw_price_byn == 0.0 else "fixed"
    )
    cluster_stats: PriceStats | None = None
    try:
        ad_id = int(ad.get("ad_id", 0))
    except (TypeError, ValueError):
        ad_id = 0
    if cluster_cache is not None:
        cluster_stats = cluster_cache.get(ad_id)
    price_ctx = _price_context(ad, market_stats, category_price_stats, cluster_stats)
    price_delta_for_sort: float | None = (
        None if price_type == "negotiable" else price_ctx.price_delta
    )
    deal_reference = (
        price_ctx.active_reference
        if price_ctx.reliable
        else _neutral_reference_stats(ad, price_ctx.active_reference)
    )
    deal_score = compute_deal_score(
        ad,
        query=query,
        market_stats=deal_reference,
    )
    return (
        -float(deal_score.score or 0.0),
        float(price_delta_for_sort or 0.0),
        str(ad.get("subject", "")),
    )


def build_listing_item(
    ad: dict[str, Any],
    *,
    query: str,
    currency: str,
    rates: dict[str, float],
    currency_service: CurrencyService,
    median_byn: float,
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None = None,
    liquidity: LiquidityInsight | None = None,
    all_ads: list[dict[str, Any]] | None = None,
    cluster_cache: dict[int, PriceStats | None] | None = None,
) -> ListingItem:
    raw_price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    price_type = (
        detect_price_type(ad) if raw_price_byn is None or raw_price_byn == 0.0 else "fixed"
    )
    price_byn = raw_price_byn
    cluster_stats: PriceStats | None = None
    ad_id = int(ad.get("ad_id", 0))
    if cluster_cache is not None:
        cluster_stats = cluster_cache.get(ad_id)
    elif all_ads:
        cluster_stats = cluster_price_stats(
            str(ad.get("subject", "")), all_ads,
        )
    price_ctx = _price_context(ad, market_stats, category_price_stats, cluster_stats)
    fair_band = fair_price_band(price_ctx.price_delta)
    # Negotiable listings have an unknown price — don't surface a delta
    # (otherwise the 0.0 default reads as "≈ по рынку" in the UI).
    # Free listings legitimately get -100% (full discount) and stay numeric.
    price_delta_for_response: float | None = (
        None if price_type == "negotiable" else price_ctx.price_delta
    )
    flags = detect_anomaly_flags(ad, price_ctx.active_reference) if price_ctx.reliable else []
    deal_reference = (
        price_ctx.active_reference
        if price_ctx.reliable
        else _neutral_reference_stats(ad, price_ctx.active_reference)
    )
    deal_score = compute_deal_score(
        ad,
        query=query,
        market_stats=deal_reference,
    )

    price = (
        currency_service.convert_from_byn(price_byn, currency, rates)
        if price_byn is not None
        else None
    )

    return ListingItem(
        ad_id=int(ad.get("ad_id", 0)),
        subject=str(ad.get("subject", "")),
        price=price,
        price_type=price_type,
        currency=currency,
        ad_link=str(ad.get("ad_link", "")),
        list_time=ad.get("list_time"),
        region_id=ad.get("region_id"),
        region_name=region_label(ad),
        area_name=area_label(ad),
        condition=get_param(ad, "condition"),
        seller_type=(
            get_param(ad, "seller_type")
            or ("shop" if ad.get("company_ad") else "private")
        ),
        company_ad=bool(ad.get("company_ad")),
        price_vs_median=price_delta_for_response,
        price_reference_scope=price_ctx.reference_scope,
        price_reference_label=price_ctx.reference_label,
        config_summary=deal_score.config_summary,
        fair_price_band=fair_band,
        fair_price_label=fair_price_label(fair_band),
        anomaly_flags=flags,
        anomaly_labels=anomaly_labels(flags),
        deal_score=deal_score.score,
        deal_verdict=deal_score.verdict if price_delta_for_response is not None else None,
        deal_reasons=deal_score.reasons,
        price_byn=price_byn,
        liquidity=liquidity,
        flip_estimates=compute_flip_estimates(ad, price_ctx.active_reference),
        thumbnail=first_image_url(ad),
        seller_rating=extract_seller_rating(ad),
        risk_factors=detect_risks(ad, market_stats={"median": price_ctx.active_reference.median}),
    )
