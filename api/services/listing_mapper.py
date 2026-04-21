from __future__ import annotations

from typing import Any

from api.schemas import ListingDetailResponse, ListingField, ListingItem
from api.services.aggregator import (
    PriceStats,
    compute_price_vs_median,
    get_param,
    normalize_price_byn,
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

IMAGE_BASE_URL = "https://rms.kufar.by/v1/gallery/"
IGNORED_AD_PARAMETER_KEYS = {"users_synonyms"}

PII_PARAMETER_KEYS = frozenset({
    "phone", "phone_hidden", "contact_person", "email",
    "company_name", "company_address", "vat_number", "user_id",
    "username", "address", "legal_name",
})


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


def category_label(ad: dict[str, Any]) -> str | None:
    for item in ad.get("ad_parameters", []):
        if item.get("p") == "category":
            return _parameter_value(item)
    return None


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
    liquidity: LiquidityInsight | None = None,
) -> ListingDetailResponse:
    price_byn = normalize_price_byn(ad.get("price_byn")) or 0.0
    description = _stringify_value(ad.get("body")) or _stringify_value(ad.get("body_short"))
    price_delta = compute_price_vs_median(ad, median_byn)
    fair_band = fair_price_band(price_delta)
    flags = detect_anomaly_flags(ad, market_stats)
    query_insights = analyze_query_text(query)
    deal_score = compute_deal_score(
        ad,
        query=query,
        market_stats=market_stats,
    )

    return ListingDetailResponse(
        query=query,
        normalized_query=query_insights.normalized_query,
        config_summary=query_insights.config_summary,
        storage_gb=query_insights.storage_gb,
        ram_gb=query_insights.ram_gb,
        ad_id=int(ad.get("ad_id", 0)),
        title=str(ad.get("subject", "")),
        price=currency_service.convert_from_byn(price_byn, currency, rates),
        currency=currency,
        link=str(ad.get("ad_link", "")),
        list_time=ad.get("list_time"),
        region_id=ad.get("region_id"),
        region_name=region_label(ad),
        area_name=area_label(ad),
        category=category_label(ad),
        condition=get_param(ad, "condition"),
        seller_type=get_param(ad, "seller_type"),
        price_vs_median=price_delta,
        fair_price_band=fair_band,
        fair_price_label=fair_price_label(fair_band),
        anomaly_flags=flags,
        anomaly_labels=anomaly_labels(flags),
        deal_score=deal_score.score,
        deal_verdict=deal_score.verdict,
        deal_reasons=deal_score.reasons,
        price_byn=price_byn,
        liquidity=liquidity,
        flip_estimates=compute_flip_estimates(ad, market_stats),
        company_ad=bool(ad.get("company_ad")),
        phone_hidden=bool(ad.get("phone_hidden", True)),
        description=description,
        images=[url for image in ad.get("images", []) if (url := image_url(image))],
        parameters=collect_fields(ad.get("ad_parameters", []), ignored=IGNORED_AD_PARAMETER_KEYS),
        seller_fields=collect_fields(ad.get("account_parameters", []), ignored=PII_PARAMETER_KEYS),
        seller_rating=extract_seller_rating(ad),
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
    liquidity: LiquidityInsight | None = None,
) -> ListingItem:
    price_byn = normalize_price_byn(ad.get("price_byn")) or 0.0
    price_delta = compute_price_vs_median(ad, median_byn)
    fair_band = fair_price_band(price_delta)
    flags = detect_anomaly_flags(ad, market_stats)
    deal_score = compute_deal_score(
        ad,
        query=query,
        market_stats=market_stats,
    )

    return ListingItem(
        ad_id=int(ad.get("ad_id", 0)),
        subject=str(ad.get("subject", "")),
        price=currency_service.convert_from_byn(price_byn, currency, rates),
        currency=currency,
        ad_link=str(ad.get("ad_link", "")),
        list_time=ad.get("list_time"),
        region_id=ad.get("region_id"),
        region_name=region_label(ad),
        area_name=area_label(ad),
        condition=get_param(ad, "condition"),
        seller_type=get_param(ad, "seller_type"),
        company_ad=bool(ad.get("company_ad")),
        price_vs_median=price_delta,
        config_summary=deal_score.config_summary,
        fair_price_band=fair_band,
        fair_price_label=fair_price_label(fair_band),
        anomaly_flags=flags,
        anomaly_labels=anomaly_labels(flags),
        deal_score=deal_score.score,
        deal_verdict=deal_score.verdict,
        deal_reasons=deal_score.reasons,
        price_byn=price_byn,
        liquidity=liquidity,
        flip_estimates=compute_flip_estimates(ad, market_stats),
        thumbnail=first_image_url(ad),
        seller_rating=extract_seller_rating(ad),
    )
