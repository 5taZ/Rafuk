from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from api.schemas import ListingDetailResponse, ListingField, ListingItem
from api.services.aggregator import (
    PriceStats,
    ad_price_byn,
    ad_seller_type,
    coerce_ad_id,
    compute_price_vs_median,
    get_param,
)
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_flip_estimates
from api.services.market_signals import (
    ad_region_id,
    anomaly_labels,
    detect_anomaly_flags,
    fair_price_band,
    fair_price_label,
    region_label,
)
from api.services.reseller_tools import DealScore, analyze_query_text, compute_deal_score

logger = logging.getLogger(__name__)

IMAGE_BASE_URL = "https://rms.kufar.by/v1/gallery/"
IGNORED_AD_PARAMETER_KEYS = {"users_synonyms"}
SELLER_TYPE_LABELS = {"private": "Частное лицо", "shop": "Магазин"}
# Errors a single malformed Kufar ad can raise while being mapped.
AD_MAPPING_ERRORS = (TypeError, ValueError, AttributeError, KeyError, ValidationError)


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


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def image_url(image: Any) -> str | None:
    if not isinstance(image, dict):
        return None
    path = image.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    return f"{IMAGE_BASE_URL}{path}"


def image_urls(ad: dict[str, Any]) -> list[str]:
    images = ad.get("images")
    if not isinstance(images, list):
        return []
    return [url for image in images if (url := image_url(image))]


def first_image_url(ad: dict[str, Any]) -> str | None:
    urls = image_urls(ad)
    return urls[0] if urls else None


def collect_fields(
    items: Any,
    ignored: set[str] | None = None,
) -> list[ListingField]:
    ignored = ignored or set()
    fields: list[ListingField] = []
    for item in _dict_items(items):
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
    for item in _dict_items(ad.get("ad_parameters")):
        if item.get("p") == "category":
            return _parameter_value(item)
    return None


def seller_type_label(ad: dict[str, Any]) -> str | None:
    """Seller type as the Mini App shows it: the raw parameter or a label from company_ad."""
    raw = get_param(ad, "seller_type")
    if raw:
        return raw
    return SELLER_TYPE_LABELS.get(ad_seller_type(ad) or "")


def _list_time(ad: dict[str, Any]) -> str | None:
    value = ad.get("list_time")
    return value if isinstance(value, str) and value else None


def _price_signals(
    ad: dict[str, Any],
    median_byn: float,
) -> tuple[float | None, float | None, str | None]:
    """Price in BYN, delta to the median and fair-price band; all None without a price."""
    price_byn = ad_price_byn(ad)
    if price_byn is None:
        return None, None, None
    price_delta = compute_price_vs_median(ad, median_byn)
    return price_byn, price_delta, fair_price_band(price_delta)


def build_listing_detail(
    ad: dict[str, Any],
    query: str,
    currency: str,
    rates: dict[str, float],
    currency_service: CurrencyService,
    median_byn: float,
    market_stats: PriceStats,
    liquidity=None,
    duplicate_count: int = 0,
    deal_score: DealScore | None = None,
) -> ListingDetailResponse:
    price_byn, price_delta, fair_band = _price_signals(ad, median_byn)
    description = _stringify_value(ad.get("body")) or _stringify_value(ad.get("body_short"))
    flags = detect_anomaly_flags(ad, market_stats)
    query_insights = analyze_query_text(query)
    if deal_score is None:
        deal_score = compute_deal_score(
            ad,
            query=query,
            market_stats=market_stats,
            duplicate_count=duplicate_count,
        )

    return ListingDetailResponse(
        query=query,
        normalized_query=query_insights.normalized_query,
        config_summary=query_insights.config_summary,
        storage_gb=query_insights.storage_gb,
        ram_gb=query_insights.ram_gb,
        ad_id=coerce_ad_id(ad.get("ad_id")) or 0,
        title=str(ad.get("subject") or ""),
        # ListingDetailResponse.price is a required float: 0 stands for "no price" there,
        # price_byn stays None so the UI can tell the two apart.
        price=currency_service.convert_from_byn(price_byn or 0.0, currency, rates),
        currency=currency,
        link=str(ad.get("ad_link") or ""),
        list_time=_list_time(ad),
        region_id=ad_region_id(ad),
        region_name=region_label(ad),
        category=category_label(ad),
        condition=get_param(ad, "condition"),
        seller_type=seller_type_label(ad),
        price_vs_median=price_delta,
        fair_price_band=fair_band,
        fair_price_label=fair_price_label(fair_band),
        anomaly_flags=flags,
        anomaly_labels=anomaly_labels(flags),
        is_duplicate=duplicate_count > 0,
        duplicate_count=duplicate_count,
        deal_score=deal_score.score,
        deal_verdict=deal_score.verdict,
        deal_reasons=deal_score.reasons,
        price_byn=price_byn,
        liquidity=liquidity,
        flip_estimates=compute_flip_estimates(ad, market_stats),
        company_ad=ad.get("company_ad") is True,
        phone_hidden=ad.get("phone_hidden") is not False,
        description=description,
        images=image_urls(ad),
        parameters=collect_fields(ad.get("ad_parameters"), ignored=IGNORED_AD_PARAMETER_KEYS),
        seller_fields=collect_fields(ad.get("account_parameters")),
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
    liquidity=None,
    duplicate_count: int = 0,
    deal_score: DealScore | None = None,
) -> ListingItem:
    price_byn, price_delta, fair_band = _price_signals(ad, median_byn)
    flags = detect_anomaly_flags(ad, market_stats)
    if deal_score is None:
        deal_score = compute_deal_score(
            ad,
            query=query,
            market_stats=market_stats,
            duplicate_count=duplicate_count,
        )

    return ListingItem(
        ad_id=coerce_ad_id(ad.get("ad_id")) or 0,
        subject=str(ad.get("subject") or ""),
        # ListingItem.price is a required float: 0 stands for "no price" there,
        # price_byn stays None so the UI can tell the two apart.
        price=currency_service.convert_from_byn(price_byn or 0.0, currency, rates),
        currency=currency,
        ad_link=str(ad.get("ad_link") or ""),
        list_time=_list_time(ad),
        region_id=ad_region_id(ad),
        region_name=region_label(ad),
        condition=get_param(ad, "condition"),
        seller_type=seller_type_label(ad),
        price_vs_median=price_delta,
        config_summary=deal_score.config_summary,
        fair_price_band=fair_band,
        fair_price_label=fair_price_label(fair_band),
        anomaly_flags=flags,
        anomaly_labels=anomaly_labels(flags),
        is_duplicate=duplicate_count > 0,
        duplicate_count=duplicate_count,
        deal_score=deal_score.score,
        deal_verdict=deal_score.verdict,
        deal_reasons=deal_score.reasons,
        price_byn=price_byn,
        liquidity=liquidity,
        flip_estimates=compute_flip_estimates(ad, market_stats),
        thumbnail=first_image_url(ad),
    )


def safe_build_listing_item(ad: dict[str, Any], **kwargs: Any) -> ListingItem | None:
    """build_listing_item that skips one malformed ad instead of failing the whole page.

    Ads without a usable ad_id are dropped too: the Mini App cannot open or save them.
    """
    if not isinstance(ad, dict) or coerce_ad_id(ad.get("ad_id")) is None:
        return None
    try:
        return build_listing_item(ad, **kwargs)
    except AD_MAPPING_ERRORS:
        logger.warning(
            "Skipping malformed Kufar ad %r",
            ad.get("ad_id"),
            exc_info=True,
        )
        return None
