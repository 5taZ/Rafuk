from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from api.services.aggregator import (
    PriceStats,
    compute_price_vs_median,
    compute_price_vs_reference,
    get_param,
    normalize_price_byn,
    normalize_search_text,
    tokenize_search_text,
)
from api.services.market_signals import area_label, detect_anomaly_flags, region_label


# =============================================================================
# Deal scoring weights — documented so they can be tuned without reading code.
# =============================================================================
@dataclass(slots=True, frozen=True)
class _ScoringConfig:
    # Base score (all listings start here)
    base_score: float = 50.0

    # Price vs median thresholds for verdict
    verdict_good_price_threshold: float = -15.0
    verdict_below_market_threshold: float = -3.0
    verdict_fair_market_threshold: float = 3.0

    # Price delta score modifiers
    discount_score_multiplier: float = 2.0
    discount_score_cap: float = 35.0
    premium_penalty_multiplier: float = 1.5
    premium_penalty_cap: float = 30.0

    # Seller type modifiers
    private_seller_bonus: float = 5.0
    shop_seller_penalty: float = 2.0

    # Freshness bonuses (hours → points + label)
    freshness_6h_bonus: int = 12
    freshness_6h_label: str = "свежий лот"
    freshness_24h_bonus: int = 8
    freshness_24h_label: str = "сегодня"
    freshness_72h_bonus: int = 4
    freshness_72h_label: str = "свежий"

    # Profile match bonuses (config keyword matching)
    storage_exact_match_bonus: int = 8
    storage_mismatch_penalty: int = 8
    ram_exact_match_bonus: int = 4
    ram_mismatch_penalty: int = 6
    model_subset_bonus: int = 6
    # =============================================================================

    # Penalties
    anomaly_penalty_per_flag: int = 12
    anomaly_penalty_cap: int = 24


SCORING = _ScoringConfig()

_RAM_STORAGE_RE = re.compile(r"\b(4|6|8|12|16|18|24)\s*/\s*(64|128|256|512|1024)\b")
_RAM_STORAGE_GB_RE = re.compile(
    r"\b(4|6|8|12|16|18|24)\s*(?:gb|гб)\s*(64|128|256|512|1024)\s*(?:gb|гб)\b"
)
_RAM_STORAGE_SPACED_RE = re.compile(r"\b(4|6|8|12|16|18|24)\s+(64|128|256|512|1024)\b")
_RAM_RE = re.compile(r"\b(4|6|8|12|16|18|24)\s*(?:gb|гб)?\s*ram\b")
_GB_RE = re.compile(r"\b(16|32|64|128|256|512|1024|2048)\s*(?:gb|гб)\b")
_TB_RE = re.compile(r"\b([12])\s*(?:tb|тб)\b")
_STORAGE_TOKEN_RE = re.compile(r"\b(64|128|256|512|1024|2048)\b")
_FRESHNESS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
)
_CONFIG_TOKENS = {
    "pro",
    "max",
    "plus",
    "mini",
    "ultra",
    "air",
    "lite",
    "note",
    "fe",
    "flip",
    "fold",
    "slim",
    "fat",
    "new",
    "used",
}
_SKIP_TOKENS = {
    "gb",
    "гб",
    "ram",
    "озу",
    "ssd",
    "hdd",
}


@dataclass(slots=True)
class QueryInsights:
    normalized_query: str
    config_summary: str | None
    storage_gb: int | None
    ram_gb: int | None
    model_tokens: list[str]


@dataclass(slots=True)
class DealScore:
    score: float
    verdict: str
    reasons: list[str]
    config_summary: str | None


def _extract_storage_gb(normalized: str) -> int | None:
    ram_storage = _RAM_STORAGE_RE.search(normalized)
    if ram_storage:
        return int(ram_storage.group(2))
    ram_storage_gb = _RAM_STORAGE_GB_RE.search(normalized)
    if ram_storage_gb:
        return int(ram_storage_gb.group(2))
    ram_storage_spaced = _RAM_STORAGE_SPACED_RE.search(normalized)
    if ram_storage_spaced:
        return int(ram_storage_spaced.group(2))

    tb = _TB_RE.search(normalized)
    if tb:
        return int(tb.group(1)) * 1024

    gb = _GB_RE.search(normalized)
    if gb:
        return int(gb.group(1))
    storage_token = _STORAGE_TOKEN_RE.search(normalized)
    if storage_token:
        return int(storage_token.group(1))
    return None


def _extract_ram_gb(normalized: str) -> int | None:
    ram_storage = _RAM_STORAGE_RE.search(normalized)
    if ram_storage:
        return int(ram_storage.group(1))
    ram_storage_gb = _RAM_STORAGE_GB_RE.search(normalized)
    if ram_storage_gb:
        return int(ram_storage_gb.group(1))
    ram_storage_spaced = _RAM_STORAGE_SPACED_RE.search(normalized)
    if ram_storage_spaced:
        return int(ram_storage_spaced.group(1))

    match = _RAM_RE.search(normalized)
    if match:
        return int(match.group(1))
    return None


def _model_tokens(tokens: list[str], storage_gb: int | None, ram_gb: int | None) -> list[str]:
    skip_values = {
        str(storage_gb) if storage_gb is not None else "",
        str(ram_gb) if ram_gb is not None else "",
    }
    return [
        token
        for token in tokens
        if token not in _SKIP_TOKENS and token not in skip_values and not token.isdigit()
    ]


def _format_config_summary(
    storage_gb: int | None,
    ram_gb: int | None,
    tokens: list[str],
) -> str | None:
    parts: list[str] = []
    variants = [token.title() for token in tokens if token in _CONFIG_TOKENS]
    if variants:
        parts.append(" ".join(dict.fromkeys(variants)))
    if ram_gb and storage_gb:
        parts.append(f"{ram_gb}/{storage_gb}")
    elif storage_gb:
        parts.append(f"{storage_gb}GB")
    elif ram_gb:
        parts.append(f"{ram_gb}GB RAM")
    return " ".join(parts) or None


def analyze_query_text(value: str) -> QueryInsights:
    normalized = normalize_search_text(value)
    tokens = tokenize_search_text(value)
    storage_gb = _extract_storage_gb(normalized)
    ram_gb = _extract_ram_gb(normalized)
    model_tokens = _model_tokens(tokens, storage_gb, ram_gb)
    config_summary = _format_config_summary(storage_gb, ram_gb, tokens)
    return QueryInsights(
        normalized_query=normalized,
        config_summary=config_summary,
        storage_gb=storage_gb,
        ram_gb=ram_gb,
        model_tokens=model_tokens,
    )


def default_config_keyword(query: str) -> str:
    return analyze_query_text(query).normalized_query[:128]


def config_keyword_matches(title: str, keyword: str | None) -> bool:
    if not keyword:
        return True
    return normalize_search_text(keyword) in normalize_search_text(title)


def profile_match_bonus(query: str, title: str) -> tuple[int, str | None, str | None]:
    query_profile = analyze_query_text(query)
    title_profile = analyze_query_text(title)
    if not query_profile.normalized_query:
        return 0, title_profile.config_summary, None

    bonus = 0
    reason = None
    if query_profile.storage_gb and title_profile.storage_gb:
        if query_profile.storage_gb == title_profile.storage_gb:
            bonus += SCORING.storage_exact_match_bonus
            reason = f"{title_profile.storage_gb}GB"
        else:
            bonus -= SCORING.storage_mismatch_penalty
    if query_profile.ram_gb and title_profile.ram_gb:
        if query_profile.ram_gb == title_profile.ram_gb:
            bonus += SCORING.ram_exact_match_bonus
        else:
            bonus -= SCORING.ram_mismatch_penalty
    query_tokens = set(query_profile.model_tokens)
    title_tokens = set(title_profile.model_tokens)
    if query_tokens and query_tokens.issubset(title_tokens):
        bonus += SCORING.model_subset_bonus
    return bonus, title_profile.config_summary, reason


def _freshness_bonus(list_time: str | None) -> tuple[int, str | None]:
    if not list_time:
        return 0, None
    parsed = None
    for fmt in _FRESHNESS_FORMATS:
        try:
            parsed = datetime.strptime(list_time, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(list_time)
        except ValueError:
            return 0, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_hours = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600
    if age_hours <= 6:
        return SCORING.freshness_6h_bonus, SCORING.freshness_6h_label
    if age_hours <= 24:
        return SCORING.freshness_24h_bonus, SCORING.freshness_24h_label
    if age_hours <= 72:
        return SCORING.freshness_72h_bonus, SCORING.freshness_72h_label
    return 0, None


def compute_deal_score(
    ad: dict[str, Any],
    *,
    query: str,
    market_stats: PriceStats,
) -> DealScore:
    reasons: list[str] = []
    delta = compute_price_vs_median(ad, market_stats.median)

    # Verdict is driven purely by price vs median
    if delta <= SCORING.verdict_good_price_threshold:
        verdict = "Хорошая цена"
    elif delta <= SCORING.verdict_below_market_threshold:
        verdict = "Ниже рынка"
    elif delta <= SCORING.verdict_fair_market_threshold:
        verdict = "Средняя цена"
    else:
        verdict = "Выше рынка"

    # Score is also primarily price-driven, with small bonuses/penalties
    score = SCORING.base_score
    if delta < 0:
        discount = abs(delta)
        score += min(discount * SCORING.discount_score_multiplier, SCORING.discount_score_cap)
        reasons.append(f"-{discount:.0f}% к медиане")
    elif delta > 0:
        score -= min(delta * SCORING.premium_penalty_multiplier, SCORING.premium_penalty_cap)
        reasons.append(f"+{delta:.0f}% к медиане")

    seller_type_param = get_param(ad, "seller_type")
    is_private = seller_type_param == "Частное лицо" or (
        not seller_type_param and not ad.get("company_ad")
    )
    is_shop = seller_type_param == "Магазин" or (
        not seller_type_param and ad.get("company_ad")
    )
    if is_private:
        score += SCORING.private_seller_bonus
        reasons.append("частник")
    elif is_shop:
        score -= SCORING.shop_seller_penalty

    freshness_score, freshness_reason = _freshness_bonus(ad.get("list_time"))
    score += freshness_score
    if freshness_reason:
        reasons.append(freshness_reason)

    config_bonus, config_summary, config_reason = profile_match_bonus(
        query, str(ad.get("subject", ""))
    )
    score += config_bonus
    if config_reason:
        reasons.append(config_reason)

    anomaly_flags = detect_anomaly_flags(ad, market_stats)
    if anomaly_flags:
        score -= min(
            len(anomaly_flags) * SCORING.anomaly_penalty_per_flag,
            SCORING.anomaly_penalty_cap,
        )
        reasons.append("есть аномалии")

    score = round(max(0.0, min(100.0, score)), 1)

    unique_reasons = list(dict.fromkeys(reasons))
    return DealScore(
        score=score,
        verdict=verdict,
        reasons=unique_reasons[:4],
        config_summary=config_summary,
    )


def matches_tracker_filters(
    ad: dict[str, Any],
    *,
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None = None,
    min_discount_percent: float | None = None,
    max_price_byn: float | None = None,
    seller_type: str | None = None,
    condition: str | None = None,
    region_name: str | None = None,
    config_keyword: str | None = None,
) -> bool:
    price_byn = normalize_price_byn(ad.get("price_byn"))
    if max_price_byn is not None and price_byn is not None and price_byn > max_price_byn:
        return False
    if seller_type:
        ad_seller = get_param(ad, "seller_type")
        is_shop = bool(ad.get("company_ad")) or (
            ad_seller and ad_seller.lower() in ("shop", "магазин")
        )
        if seller_type == "shop" and not is_shop:
            return False
        if seller_type == "private" and is_shop:
            return False
    if condition:
        ad_condition = get_param(ad, "condition")
        # Map tracker values to Kufar numeric codes for comparison
        condition_map = {"new": "2", "used": "1"}
        expected = condition_map.get(condition, condition)
        if ad_condition != expected:
            return False
    if region_name:
        ad_region = region_label(ad)
        ad_area = area_label(ad)
        if ad_region != region_name and ad_area != region_name:
            return False
    if config_keyword and not config_keyword_matches(str(ad.get("subject", "")), config_keyword):
        return False
    if min_discount_percent is not None:
        delta = compute_price_vs_reference(ad, market_stats, category_price_stats)
        if abs(min(delta, 0.0)) < min_discount_percent:
            return False
    return True
