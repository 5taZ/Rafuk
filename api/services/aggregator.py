from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

KOPECKS = 100
MAX_PRICE_BYN = 1_000_000.0
MIN_PRICE_BYN = 0.5  # Ignore listings priced below 0.50 BYN (kopecks remainder / spam)
MIN_CATEGORY_REFERENCE_COUNT = 3

# Maximum reasonable price (BYN) for accessory subcategories.
# When Kufar search returns mixed results (e.g. phones + cases for "чехол iPhone"),
# ads priced above these caps are clearly the *parent* product, not the accessory.
# Used by filter_ads_for_accessory_category() to compute accurate price stats.
ACCESSORY_PRICE_CAPS: dict[str, float] = {
    "phone_accessory": 150.0,
    "auto_accessory": 500.0,
    "computer_accessory": 2000.0,
    "photo_accessory": 500.0,
    "gaming_accessory": 1000.0,
    "home_accessory": 200.0,
    "bicycle_accessory": 200.0,
    "watch_accessory": 120.0,
}
STRICT_VARIANT_TOKENS = {
    # Tech device variants (phones, laptops, tablets)
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
    "studio",
    "slim",
    "fat",
    "ti",
    "super",
    "se",
    "xs",
    "xr",
    # Car generations (Roman numerals in listing titles)
    "vi",
    "vii",
    "viii",
    "ix",
    "xii",
    # Car body types
    "седан",
    "хэтчбек",
    "универсал",
    "купе",
    "кабриолет",
    "лифтбек",
    "рестайлинг",
    "рестайл",
    # Common car trim / model qualifiers
    "gt",
    "gts",
    "amg",
    "sport",
    "comfortline",
    "highline",
    "trendline",
}
# Tokens shorter than 3 chars are too ambiguous for variant matching and cause
# false positives (e.g. "s" matching inside "s24", "x" matching "xs").
# They are excluded from the variant-extras check but still used for token matching.

SEARCH_ALIASES = {
    "айфон": "iphone",
    "макбук": "macbook",
    "мак бук": "macbook",
    "mac book": "macbook",
    "playstation": "ps",
    "play station": "ps",
    "плейстейшен": "ps",
    "плей стейшен": "ps",
    "пс": "ps",
    "пс5": "ps5",
    "пс4": "ps4",
    "ps 5": "ps5",
    "ps 4": "ps4",
    "слим": "slim",
    "phat": "fat",
    "фат": "fat",
    "обычная": "fat",
    "обычный": "fat",
    "про макс": "pro max",
    "promax": "pro max",
    "самсунг": "samsung",
    "галакси": "galaxy",
    "гэлакси": "galaxy",
    "сяоми": "xiaomi",
    "редми": "redmi",
    "поко": "poco",
    "поук": "poco",
    "найк": "nike",
    "адидас": "adidas",
    # Car brand/model transliterations
    "поло": "polo",
    "фольксваген": "volkswagen",
    "пассат": "passat",
    "гольф": "golf",
    "тигуан": "tiguan",
    "тойота": "toyota",
    "камри": "camry",
    "королла": "corolla",
    "раф4": "rav4",
    "бмв": "bmw",
    "мерседес": "mercedes",
    "ауди": "audi",
    "мазда": "mazda",
    "форд": "ford",
    "фокус": "focus",
    "хёндай": "hyundai",
    "хундай": "hyundai",
    "солярис": "solaris",
    "киа": "kia",
    "рио": "rio",
    "рено": "renault",
    "логан": "logan",
    "пежо": "peugeot",
    "опель": "opel",
    "шевроле": "chevrolet",
    "ниссан": "nissan",
    "хонда": "honda",
    "митсубиси": "mitsubishi",
    "лексус": "lexus",
    "лендровер": "landrover",
    "ягуар": "jaguar",
    "порше": "porsche",
    "нот": "note",
    "нубия": "nubia",
    "хуавей": "huawei",
    "хонор": "honor",
}


class PriceStats(BaseModel):
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int


@dataclass(slots=True, frozen=True)
class PriceReference:
    stats: PriceStats
    scope: str
    label: str


def normalize_search_text(value: str) -> str:
    text = value.casefold()
    for source, target in SEARCH_ALIASES.items():
        text = text.replace(source, target)
    text = re.sub(r"(\d+)\s*gb\b", r"\1", text)
    text = re.sub(r"(\d+)\s*гб\b", r"\1", text)
    text = re.sub(r"(\d+)\s*/\s*(\d+)", r"\1 \2", text)
    text = re.sub(r"\b([12])\s*(?:tb|тб)\b", lambda match: str(int(match.group(1)) * 1024), text)
    text = re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def tokenize_search_text(value: str) -> list[str]:
    normalized = normalize_search_text(value)
    return normalized.split() if normalized else []


def build_query_key(query: str, strict_search: bool) -> str:
    mode = "strict" if strict_search else "broad"
    return f"{mode}::{normalize_search_text(query)}"


def is_strict_match(title: str, query: str) -> bool:
    """Check if a listing title strictly matches the search query.

    A strict match requires:
    1. Every query token must appear in the title (set-based containment).
    2. The title must not contain variant tokens (pro/max/ultra/etc.) that
       the query does not also contain. This prevents "iphone 15 pro" from
       matching a search for just "iphone 15".
    3. Numeric storage/ram values in the query must appear in the title.
    """
    query_tokens = tokenize_search_text(query)
    title_tokens = tokenize_search_text(title)
    if not query_tokens or not title_tokens:
        return False

    title_set = set(title_tokens)
    if not all(token in title_set for token in query_tokens):
        return False

    query_variants = {token for token in query_tokens if token in STRICT_VARIANT_TOKENS}
    title_variants = {token for token in title_tokens if token in STRICT_VARIANT_TOKENS}
    extra_variants = title_variants - query_variants
    return not extra_variants


def apply_search_mode(
    ads: list[dict[str, Any]],
    query: str,
    strict_search: bool,
) -> list[dict[str, Any]]:
    if not strict_search:
        return ads
    return [ad for ad in ads if is_strict_match(str(ad.get("subject", "")), query)]


# Minimum number of similar listings required for a cluster-specific
# price comparison. Below this threshold the delta% is hidden (0.0)
# to avoid misleading comparisons against a tiny sample.
MIN_CLUSTER_SIZE = 3


def find_similar_listings(
    target_title: str,
    all_ads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find listings that share the same variant tokens as the target.

    Uses :func:`is_strict_match` in reverse — treats the target title as
    the "query" and filters ``all_ads`` to only those that match its
    variant profile (generation, body type, trim, etc.).

    Returns the filtered list, or the full ``all_ads`` if the cluster is
    smaller than :data:`MIN_CLUSTER_SIZE`.
    """
    similar = [
        ad for ad in all_ads
        if is_strict_match(str(ad.get("subject", "")), target_title)
    ]
    if len(similar) >= MIN_CLUSTER_SIZE:
        return similar
    return []


def cluster_price_stats(
    target_title: str,
    all_ads: list[dict[str, Any]],
) -> PriceStats | None:
    """Compute price stats for listings similar to *target_title*.

    Returns ``None`` when the cluster is too small (< MIN_CLUSTER_SIZE),
    signalling the caller to hide the delta% badge.
    """
    similar = find_similar_listings(target_title, all_ads)
    if not similar:
        return None
    prices = extract_prices(similar)
    if not prices:
        return None
    return compute_price_stats(prices)


def precompute_cluster_stats(
    all_ads: list[dict[str, Any]],
) -> dict[int, PriceStats | None]:
    """Pre-compute cluster stats for every ad in *all_ads* in O(n²) once.

    Returns a dict mapping ``ad_id`` → ``PriceStats | None`` so callers
    can look up per-ad cluster stats in O(1) instead of calling
    :func:`cluster_price_stats` per listing (which itself is O(n)).
    """
    result: dict[int, PriceStats | None] = {}
    for ad in all_ads:
        ad_id = int(ad.get("ad_id", 0))
        title = str(ad.get("subject", ""))
        result[ad_id] = cluster_price_stats(title, all_ads)
    return result


# ── Price-type detection (free vs negotiable) ─────────────────────────
#
# Kufar returns price=0 for both "договорная" (price unknown / "ask the
# seller") and "бесплатно" (genuine giveaway). The two are semantically
# different: negotiable items must be EXCLUDED from price metrics
# (median, mean, stats, %vs market), free items must be INCLUDED as 0
# (a 100% discount). Wrongly labelling a negotiable listing as free
# poisons the market median; wrongly labelling free as negotiable
# hides genuine giveaways from users. The default is therefore the
# safer "negotiable" — only explicit, contextual giveaway phrases
# escalate to "free".
#
# Sources of false positives we deliberately reject:
# * "Бесплатная доставка / установка / сборка / осмотр" — describes a
#   service, not the item itself.
# * "Покажу/осмотр товара бесплатно" — describes the demo, not the price.
# * "Free shipping" — same in English.
# * Standalone "не нужен / не нужны" — common in body text ("этот мне
#   уже не нужен"), doesn't imply giveaway.
# * Naked "заберите" — just means "come pick it up after payment".

# Russian + Belarusian "free" tokens
_FREE_WORD = r"(?:бесплатно|бясплатна|даром|дарма|безвозмездно)"

# Verbs of giving / taking (Russian + Belarusian inflections)
_GIVE_VERB = (
    r"(?:"
    r"отда[мйёт]\w{0,3}|отдаю|отдадим|отдают|"        # Russian
    r"адда[мйёт]\w{0,3}|аддаю|аддадзім|"               # Belarusian "addam"
    r"забер[иёы]\w*|заберите?|забирай(?:це|те)?|"      # Russian "zaberi"
    r"забіра[ею]|забіра[йю]ц[ея]|"                    # Belarusian
    r"возьми(?:те|це)?"
    r")"
)

# Explicit "give-it-away" phrasing — verb + free-word in either order,
# allowing up to 3 intervening words (e.g. "отдам в хорошие руки бесплатно").
_GIVEAWAY_RE = (
    re.compile(rf"\b{_GIVE_VERB}\b(?:\s+\w+){{0,3}}\s+{_FREE_WORD}\b"),
    re.compile(rf"\b{_FREE_WORD}\b(?:\s+\w+){{0,3}}\s+{_GIVE_VERB}\b"),
)

# English giveaway phrases
_EN_GIVEAWAY_RE = re.compile(
    r"\b(?:take it for free|grab it free|free to a good home|giving away|free to take)\b"
)

# Negation kills any positive match: "не бесплатно", "не за бесплатно",
# "не даром", "не за даром".
_NEGATION_RE = re.compile(rf"\bне\s+(?:за\s+)?{_FREE_WORD}\b")

# When _FREE_WORD appears in subject (title), it's usually a real signal
# unless the next word is a "service" compound like "доставка" or "установка".
_TITLE_BLACKLIST_RE = re.compile(
    rf"\b{_FREE_WORD}\s+("
    r"доставк|перевозк|перевоз|осмотр|установк|сборк|разборк|"
    r"проб|испытан|монтаж|ремонт|получит|оценк|консультац|"
    r"сервис|подключен|настройк|обучен|пример|просмотр"
    r")"
)


def detect_price_type(ad: dict[str, Any]) -> str:
    """Decide if a zero-price ad is 'free' (genuine giveaway) or 'negotiable'.

    Default: 'negotiable' (the safer choice — unknown price).
    Returns 'free' only when the ad text contains a contextual giveaway
    phrase (verb of giving + free-word) OR a standalone free-word in the
    title that isn't followed by a service-compound stop-word.
    """
    subject = (ad.get("subject") or "").lower()
    body = (ad.get("body") or "").lower()
    body_short = (ad.get("body_short") or "").lower()
    full_text = f"{subject}\n{body}\n{body_short}"

    # Negation overrides everything
    if _NEGATION_RE.search(full_text):
        return "negotiable"

    # 1. Contextual giveaway phrases anywhere
    for pat in _GIVEAWAY_RE:
        if pat.search(full_text):
            return "free"

    # 2. English explicit giveaway
    if _EN_GIVEAWAY_RE.search(full_text):
        return "free"

    # 3. Standalone free-word in title — but not in service-compound context
    if re.search(rf"\b{_FREE_WORD}\b", subject):
        if not _TITLE_BLACKLIST_RE.search(subject):
            return "free"

    return "negotiable"


def normalize_price_byn(raw_price: Any, ad: dict[str, Any] | None = None) -> float | None:
    if raw_price in (None, "", 0, 0.0):
        # Kufar API returns both "negotiable" and "free" as 0.
        # Distinguish them by ad text when the raw ad dict is provided.
        if ad is not None:
            price_type = detect_price_type(ad)
            if price_type == "free":
                return 0.0
        return None
    try:
        numeric = float(raw_price)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    if numeric <= 0:
        return None
    # Kufar API returns prices in kopecks — convert to BYN
    price_byn = round(numeric / 100, 2)
    if price_byn > MAX_PRICE_BYN:
        return None
    if price_byn < MIN_PRICE_BYN:
        return None
    return price_byn


def extract_prices(ads: list[dict[str, Any]]) -> list[float]:
    """Extract prices for metric calculations.

    * Negotiable listings (price_byn == 0 with no free keywords) are excluded.
    * Free listings (price_byn == 0 with free keywords) are included as 0.0.
    * Normal priced listings are included as-is.
    """
    prices: list[float] = []
    for ad in ads:
        price_byn = normalize_price_byn(ad.get("price_byn"), ad)
        if price_byn is None:
            continue
        prices.append(price_byn)
    return prices


def _percentile(data: list[float], percentile: float) -> float:
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * percentile / 100
    floor_idx = int(k)
    ceil_idx = min(floor_idx + 1, len(data) - 1)
    return data[floor_idx] + (k - floor_idx) * (data[ceil_idx] - data[floor_idx])


def _remove_outliers(prices: list[float]) -> list[float]:
    """Remove statistical outliers using the IQR method.

    Prices outside [Q1 - 2.5*IQR, Q3 + 2.5*IQR] are excluded. This prevents
    bogus listings (e.g. 1 BYN phones or 99999 BYN accessories) from polluting
    median and mean calculations. Only applied when there are enough data points
    for IQR to be meaningful (>= 8).

    When IQR=0 (near-uniform prices with one or two extreme outliers), falls
    back to median-absolute-deviation: any price further than 5x from the
    median is excluded.
    """
    if len(prices) < 8:
        return prices
    sorted_prices = sorted(prices)
    q1 = _percentile(sorted_prices, 25)
    q3 = _percentile(sorted_prices, 75)
    iqr = q3 - q1
    if iqr <= 0:
        med = statistics.median(sorted_prices)
        if med > 0:
            return [p for p in prices if 0.2 * med <= p <= 5.0 * med]
        return prices
    lower = q1 - 2.5 * iqr
    upper = q3 + 2.5 * iqr
    return [p for p in prices if lower <= p <= upper]


def compute_price_stats(prices: list[float]) -> PriceStats:
    """Compute descriptive price statistics after outlier removal.

    ``count`` reflects the number of prices AFTER outlier removal, which is
    the set used for mean/median/Q1/Q3 — this is the meaningful count for
    interpreting the statistics.  It may be smaller than the raw listing count
    when spam or miscategorized listings are filtered out.
    """
    if not prices:
        return PriceStats(mean=0.0, median=0.0, q1=0.0, q3=0.0, min=0.0, max=0.0, count=0)
    cleaned = _remove_outliers(prices)
    sorted_prices = sorted(cleaned)
    return PriceStats(
        mean=round(statistics.mean(sorted_prices), 2),
        median=round(statistics.median(sorted_prices), 2),
        q1=round(_percentile(sorted_prices, 25), 2),
        q3=round(_percentile(sorted_prices, 75), 2),
        min=round(sorted_prices[0], 2),
        max=round(sorted_prices[-1], 2),
        count=len(sorted_prices),
    )


def compute_price_vs_median(ad: dict[str, Any], median: float) -> float:
    if not median:
        return 0.0
    price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    if price_byn is None:
        return 0.0
    return round((price_byn - median) / median * 100.0, 2)


def get_category_id(ad: dict[str, Any]) -> int | None:
    raw_category = ad.get("category")
    try:
        return int(raw_category)
    except (TypeError, ValueError):
        return None


def get_category_label(ad: dict[str, Any]) -> str | None:
    for param in ad.get("ad_parameters", []):
        if param.get("p") == "category":
            value = param.get("vl") or param.get("v")
            if isinstance(value, str) and value.strip():
                return value.strip()
    category_id = get_category_id(ad)
    if category_id is None:
        return None
    return f"Категория {category_id}"


def compute_category_price_stats(ads: list[dict[str, Any]]) -> dict[int, PriceStats]:
    grouped_prices: dict[int, list[float]] = {}
    for ad in ads:
        category_id = get_category_id(ad)
        if category_id is None:
            continue
        price_byn = normalize_price_byn(ad.get("price_byn"), ad)
        if price_byn is None:
            continue
        grouped_prices.setdefault(category_id, []).append(price_byn)
    return {
        category_id: compute_price_stats(prices)
        for category_id, prices in grouped_prices.items()
        if prices
    }


def resolve_price_reference(
    ad: dict[str, Any],
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None = None,
    *,
    min_category_count: int = MIN_CATEGORY_REFERENCE_COUNT,
) -> PriceReference:
    if category_price_stats:
        category_id = get_category_id(ad)
        if category_id is not None:
            category_stats = category_price_stats.get(category_id)
            if (
                category_stats is not None
                and category_stats.count >= min_category_count
                and category_stats.median > 0
            ):
                return PriceReference(
                    stats=category_stats,
                    scope="category",
                    label=get_category_label(ad) or f"Категория {category_id}",
                )
    return PriceReference(stats=market_stats, scope="query", label="Весь запрос")


def filter_ads_for_accessory_category(
    ads: list[dict[str, Any]],
    detected_category: str,
) -> list[dict[str, Any]]:
    """Filter ads to only those within the accessory price range.

    When Kufar search returns mixed results (e.g. phones + cases for
    "чехол iPhone 14 Pro"), ads priced above the accessory cap are
    clearly the parent product, not the accessory.  Removing them
    before computing price stats prevents the median from being
    dominated by phone-level prices (~2000 BYN instead of ~20 BYN).

    Returns the original list unchanged when *detected_category* is
    not an accessory type or when the filtered result would be empty.
    """
    cap = ACCESSORY_PRICE_CAPS.get(detected_category)
    if cap is None:
        return ads

    filtered: list[dict[str, Any]] = []
    for ad in ads:
        price = normalize_price_byn(ad.get("price_byn"))
        if price is not None and price <= cap:
            filtered.append(ad)

    # If filtering removes everything (edge case), fall back to the
    # original list — better to have noisy stats than zero stats.
    return filtered if filtered else ads


def compute_price_vs_reference(
    ad: dict[str, Any],
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None = None,
    *,
    min_category_count: int = MIN_CATEGORY_REFERENCE_COUNT,
) -> float:
    reference = resolve_price_reference(
        ad,
        market_stats,
        category_price_stats,
        min_category_count=min_category_count,
    )
    return compute_price_vs_median(ad, reference.stats.median)


def filter_deal_ads(
    ads: list[dict[str, Any]],
    median: float,
    discount_from_percent: float,
    discount_to_percent: float | None = None,
    *,
    market_stats: PriceStats | None = None,
    category_price_stats: dict[int, PriceStats] | None = None,
) -> list[dict[str, Any]]:
    if not median:
        return []

    lower_bound = abs(discount_from_percent)
    upper_bound = abs(discount_to_percent) if discount_to_percent is not None else None
    if upper_bound is not None and upper_bound < lower_bound:
        lower_bound, upper_bound = upper_bound, lower_bound

    filtered = []
    effective_market_stats = market_stats or compute_price_stats(extract_prices(ads))
    for ad in ads:
        delta = compute_price_vs_reference(
            ad,
            effective_market_stats,
            category_price_stats,
        )
        if delta >= 0:
            continue
        discount = abs(delta)
        if discount < lower_bound:
            continue
        if upper_bound is not None and discount > upper_bound:
            continue
        filtered.append(ad)
    return filtered


def sort_listings(
    ads: list[dict[str, Any]],
    sort: str,
    median: float,
    *,
    market_stats: PriceStats | None = None,
    category_price_stats: dict[int, PriceStats] | None = None,
) -> list[dict[str, Any]]:
    effective_market_stats = market_stats or compute_price_stats(extract_prices(ads))
    if sort == "cheap":
        decorated = [
            (
                compute_price_vs_reference(ad, effective_market_stats, category_price_stats),
                normalize_price_byn(ad.get("price_byn")) or 0.0,
                i,
                ad,
            )
            for i, ad in enumerate(ads)
        ]
        decorated.sort()
        return [ad for _, _, _, ad in decorated]
    if sort == "price_asc":
        return sorted(ads, key=lambda ad: normalize_price_byn(ad.get("price_byn")) or 0.0)
    if sort == "price_desc":
        return sorted(
            ads,
            key=lambda ad: normalize_price_byn(ad.get("price_byn")) or 0.0,
            reverse=True,
        )
    if sort == "near_median":
        return sorted(
            ads,
            key=lambda ad: abs((normalize_price_byn(ad.get("price_byn")) or 0.0) - median),
        )
    return sorted(ads, key=lambda ad: ad.get("list_time", ""), reverse=True)


def get_param(ad: dict[str, Any], name: str) -> str | None:
    for param in ad.get("ad_parameters", []):
        if param.get("p") == name and isinstance(param.get("v"), str):
            return param["v"]
    return None


def extract_category_distribution(ads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cat_map: dict[int, dict[str, Any]] = {}
    for ad in ads:
        cat_id = get_category_id(ad)
        if cat_id is None:
            continue
        if cat_id not in cat_map:
            cat_map[cat_id] = {
                "id": cat_id,
                "label": get_category_label(ad) or f"Категория {cat_id}",
                "count": 0,
            }
        cat_map[cat_id]["count"] += 1
    return sorted(cat_map.values(), key=lambda x: x["count"], reverse=True)


# Tokens that are too generic to suggest as a refinement.
# Brand names already implied by the query, condition adjectives, units, and
# stop-words add no signal — we want concrete model qualifiers like "pro",
# "256", "max", "2024" instead.
_REFINEMENT_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "in",
        "on",
        "at",
        "by",
        "of",
        "to",
        "with",
        "without",
        "or",
        "as",
        "is",
        "be",
        "из",
        "до",
        "от",
        "по",
        "на",
        "в",
        "и",
        "с",
        "со",
        "у",
        "за",
        "о",
        "об",
        "ни",
        "не",
        "но",
        "то",
        "же",
        "бу",
        "юу",
        "новый",
        "новая",
        "новое",
        "новые",
        "новых",
        "идеал",
        "торг",
        "срочно",
        "продам",
        "продаю",
        "продается",
        "продаётся",
        "обмен",
        "куплю",
        "куплен",
        "куплена",
        "состояние",
        "оригинал",
        "оригинальный",
        "комплект",
        "коробка",
        "хороший",
        "отличное",
        "отличный",
        "белый",
        "чёрный",
        "черный",
        "синий",
        "красный",
        "зелёный",
        "зеленый",
        "силиконовый",
        "силиконовая",
        "минск",
        "гомель",
        "брест",
        "витебск",
        "гродно",
        "могилев",
        "могилёв",
        "руб",
        "byn",
        "usd",
        "eur",
        "$",
        "руб.",
        "грн",
    }
)


def extract_search_refinements(
    ads: list[dict[str, Any]],
    query: str,
    *,
    limit: int = 5,
    min_support: int = 3,
) -> list[str]:
    """Suggest tap-to-append refinements based on the result set.

    Looks at every listing's `subject`, normalises tokens the same way the
    search uses, drops tokens already present in the user's query plus
    obvious stop-words, then returns the top `limit` tokens by frequency
    that appear in at least `min_support` listings. The frontend renders
    them as chips: "пробовали X, Y, Z?".

    Returns an empty list when the result set is too small for any token
    to clear `min_support` — there's no point suggesting refinements
    backed by a single anecdotal listing.
    """
    if not ads:
        return []
    query_tokens = set(tokenize_search_text(query))
    counts: dict[str, int] = {}
    seen_per_ad: set[tuple[int, str]] = set()
    for idx, ad in enumerate(ads):
        title = str(ad.get("subject") or "")
        if not title:
            continue
        for token in tokenize_search_text(title):
            if token in query_tokens:
                continue
            if token in _REFINEMENT_STOPWORDS:
                continue
            # Skip 1-char and pure-digit tokens shorter than 3 chars
            # — they're noise (e.g. "1", "2", lone letters).
            if len(token) < 3:
                continue
            # Skip tokens consisting only of letters from the query
            # (substring noise).
            key = (idx, token)
            if key in seen_per_ad:
                continue
            seen_per_ad.add(key)
            counts[token] = counts.get(token, 0) + 1
    if not counts:
        return []
    # Threshold: at least min_support listings *and* at least 5 % of the
    # result set so we don't surface long-tail noise on big datasets.
    floor = max(min_support, int(len(ads) * 0.05))
    candidates = [(token, n) for token, n in counts.items() if n >= floor]
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return [token for token, _ in candidates[:limit]]


def compute_segments(ads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # Kufar's `condition` param flipped from text labels ("Новый",
    # "Б/у") to numeric codes ("1"=used, "2"=new) some time after
    # 2026 — the old map silently produced 0-count segments for
    # every query. Numeric codes restore segmentation. Seller type
    # comes from the top-level ``company_ad`` flag because the API
    # no longer surfaces a "seller_type" ad parameter.
    condition_map = {"1": "used", "2": "new", "Новый": "new", "Б/у": "used"}
    grouped: dict[str, list[float]] = {}

    for ad in ads:
        price_byn = normalize_price_byn(ad.get("price_byn"))
        if price_byn is None:
            continue
        condition = condition_map.get(get_param(ad, "condition") or "")
        if not condition:
            continue
        seller_type = "shop" if ad.get("company_ad") else "private"
        grouped.setdefault(f"{condition}_{seller_type}", []).append(price_byn)

    return {name: compute_price_stats(prices).model_dump() for name, prices in grouped.items()}
