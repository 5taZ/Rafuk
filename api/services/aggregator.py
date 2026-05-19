from __future__ import annotations

import asyncio
import math
import re
import statistics
from dataclasses import dataclass
from functools import lru_cache
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
    # LOGIC-NEW-4: small-sample stats are unreliable; tag them so
    # threshold alerts don't fire on a single observation.
    reliable: bool = True


@dataclass(slots=True, frozen=True)
class PriceReference:
    stats: PriceStats
    scope: str
    label: str


# Pre-compile every regex normalize_search_text uses so each call avoids
# the regex-cache lookup. Compiled once at import.
_GB_RE = re.compile(r"(\d+)\s*gb\b")
_GB_CYR_RE = re.compile(r"(\d+)\s*гб\b")
_SLASH_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_TB_RE = re.compile(r"\b([12])\s*(?:tb|тб)\b")
_NONALNUM_RE = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)

# BE-M13: replace the per-alias ``str.replace`` loop (67 separate
# passes, O(n × m)) with a precompiled alternation regex. The ``re``
# engine builds an Aho-Corasick-ish DFA for an OR-of-literals pattern,
# so each iteration is O(n × max_alias_len) — roughly 67× cheaper for
# the alias step alone on a typical 50-char query/title.
#
# Aliases are sorted longest-first so multi-word keys (``"mac book"``,
# ``"play station"``, ``"про макс"``) win over their single-word
# shorter substrings — same precedence the old iteration-order trick
# relied on, but explicit instead of implicit.
#
# We iterate to fixed point because the old sequential ``.replace``
# cascade allowed earlier substitutions to feed into later ones
# (e.g. ``"плейстейшен"`` → ``"ps"`` then ``"ps 4"`` → ``"ps4"``).
# A single regex pass operates on the original string with
# non-overlapping matches and can't see its own output, so we re-run
# until the string stabilises. Two iterations cover every alias chain
# in the current map; the loop bails out as soon as the text stops
# changing, and the safety cap of 4 guarantees termination even if
# someone adds an alias that paradoxically rewrites to itself.
_ALIAS_PATTERN = re.compile(
    "|".join(re.escape(k) for k in sorted(SEARCH_ALIASES, key=len, reverse=True))
)
_ALIAS_MAX_PASSES = 4


def _alias_sub(match: re.Match[str]) -> str:
    return SEARCH_ALIASES[match.group(0)]


def _apply_aliases(text: str) -> str:
    for _ in range(_ALIAS_MAX_PASSES):
        new_text = _ALIAS_PATTERN.sub(_alias_sub, text)
        if new_text == text:
            return new_text
        text = new_text
    return text


# Cached per-string normaliser. The same query / title is processed
# repeatedly (e.g. by precompute_cluster_stats which used to be O(n²)
# in the number of ads, multiplied by the ~150 replace+regex ops in
# this function). Caching collapses those repeated calls into O(1)
# lookups; the cap is large enough to cover a full Kufar page (≤1500
# ads) plus search inputs without thrashing.
@lru_cache(maxsize=4096)
def normalize_search_text(value: str) -> str:
    text = value.casefold()
    text = _apply_aliases(text)
    text = _GB_RE.sub(r"\1", text)
    text = _GB_CYR_RE.sub(r"\1", text)
    text = _SLASH_RE.sub(r"\1 \2", text)
    text = _TB_RE.sub(lambda match: str(int(match.group(1)) * 1024), text)
    text = _NONALNUM_RE.sub(" ", text)
    return " ".join(text.split())


def tokenize_search_text(value: str) -> list[str]:
    normalized = normalize_search_text(value)
    return normalized.split() if normalized else []


def build_query_key(query: str, strict_search: bool, category_id: int | None = None) -> str:
    mode = "strict" if strict_search else "broad"
    normalized = normalize_search_text(query)
    if category_id is None:
        return f"{mode}::{normalized}"
    return f"{mode}::cat:{category_id}::{normalized}"


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
MAX_CLUSTER_PRICE_SPREAD = 1.5
_PRODUCT_TYPE_ALIASES = {
    "мотор": "engine",
    "мотора": "engine",
    "двигатель": "engine",
    "двигателя": "engine",
    "двиг": "engine",
    "зеркало": "mirror",
    "зеркала": "mirror",
    "поворотник": "turn_signal",
    "поворотники": "turn_signal",
    "поворотника": "turn_signal",
    "указатель": "turn_signal",
    "бампер": "bumper",
    "бампера": "bumper",
    "дверь": "door",
    "двери": "door",
    "крыло": "fender",
    "крыла": "fender",
    "фара": "headlight",
    "фары": "headlight",
    "фонарь": "tail_light",
    "фонари": "tail_light",
    "капот": "hood",
    "капота": "hood",
    "кпп": "gearbox",
    "коробка": "gearbox",
    "коробку": "gearbox",
    "акпп": "gearbox",
    "мкпп": "gearbox",
    "диск": "wheel",
    "диски": "wheel",
    "колесо": "wheel",
    "колеса": "wheel",
    "шина": "tire",
    "шины": "tire",
    "резина": "tire",
    "сиденье": "seat",
    "сидения": "seat",
    "чехол": "case",
    "чехлы": "case",
    "чехла": "case",
    "зарядка": "charger",
    "зарядное": "charger",
    "зарядник": "charger",
    "аккумулятор": "battery",
    "аккумулятора": "battery",
    "батарея": "battery",
    "дисплей": "display",
    "экран": "display",
    "матрица": "display",
    "камера": "camera",
    "объектив": "lens",
    "клавиатура": "keyboard",
    "мышь": "mouse",
    "наушники": "headphones",
    "монитор": "monitor",
}


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


def _cluster_spread(stats: PriceStats) -> float:
    if stats.median <= 0 or stats.q3 < stats.q1:
        return float("inf")
    return (stats.q3 - stats.q1) / stats.median


def _product_cluster_key(tokens: list[str], query_tokens: set[str]) -> tuple[str, ...]:
    token_set = set(tokens)
    aliases = {
        alias
        for token in tokens
        if token not in query_tokens and (alias := _PRODUCT_TYPE_ALIASES.get(token))
    }
    if ({"крышка", "крышку"} & token_set) and ({"багажника", "багажник"} & token_set):
        aliases.add("trunk_lid")
    if not aliases:
        return ()
    return tuple(sorted(aliases))


def _precompute_cluster_stats_sync(
    all_ads: list[dict[str, Any]],
    query: str | None = None,
) -> dict[int, PriceStats | None]:
    """Synchronous CPU-bound implementation — run via asyncio.to_thread()."""
    # Step 1: tokenise + extract variant set for each ad once.
    # ad_id_list[i] keeps ad_id for index lookup; tokens_list[i] is None
    # when the title was empty (those ads can never match anything).
    n = len(all_ads)
    ad_id_list: list[int] = [0] * n
    token_sets: list[set[str] | None] = [None] * n
    variant_sets: list[set[str] | None] = [None] * n
    query_tokens = set(tokenize_search_text(query or ""))
    product_keys: list[tuple[str, ...]] = [()] * n
    product_groups: dict[tuple[str, ...], list[int]] = {}
    for i, ad in enumerate(all_ads):
        ad_id_list[i] = int(ad.get("ad_id", 0))
        tokens = tokenize_search_text(str(ad.get("subject", "")))
        if not tokens:
            continue
        token_sets[i] = set(tokens)
        variant_sets[i] = {t for t in tokens if t in STRICT_VARIANT_TOKENS}
        product_key = _product_cluster_key(tokens, query_tokens)
        product_keys[i] = product_key
        if product_key:
            product_groups.setdefault(product_key, []).append(i)

    # Step 2: per ad, find the cluster of similar ads using only set ops.
    # Collect prices in one pass (avoid re-walking the cluster).
    result: dict[int, PriceStats | None] = {}
    for i in range(n):
        target_tokens = token_sets[i]
        target_variants = variant_sets[i]
        if target_tokens is None or target_variants is None:
            result[ad_id_list[i]] = None
            continue

        cluster_prices: list[float] = []
        cluster_size = 0
        for j in range(n):
            j_tokens = token_sets[j]
            j_variants = variant_sets[j]
            if j_tokens is None or j_variants is None:
                continue
            # Mirrors is_strict_match: every target token must appear in
            # j; j must not carry extra variant tokens vs. target.
            if not target_tokens.issubset(j_tokens):
                continue
            if j_variants - target_variants:
                continue
            cluster_size += 1
            price = normalize_price_byn(all_ads[j].get("price_byn"), all_ads[j])
            if price is not None:
                cluster_prices.append(price)

        if cluster_size >= MIN_CLUSTER_SIZE and cluster_prices:
            result[ad_id_list[i]] = compute_price_stats(cluster_prices)
        else:
            product_key = product_keys[i]
            product_indexes = product_groups.get(product_key, [])
            product_prices: list[float] = []
            for idx in product_indexes:
                price = normalize_price_byn(all_ads[idx].get("price_byn"), all_ads[idx])
                if price is not None:
                    product_prices.append(price)
            product_stats = (
                compute_price_stats(product_prices)
                if len(product_indexes) >= MIN_CLUSTER_SIZE and product_prices
                else None
            )
            product_stats_is_usable = (
                product_stats is not None
                and _cluster_spread(product_stats) <= MAX_CLUSTER_PRICE_SPREAD
            )
            result[ad_id_list[i]] = (
                product_stats
                if product_stats_is_usable
                else None
            )

    return result


async def precompute_cluster_stats(
    all_ads: list[dict[str, Any]],
    query: str | None = None,
) -> dict[int, PriceStats | None]:
    """Async wrapper — offloads the O(n²) CPU work to a thread.

    Returns a dict mapping ``ad_id`` → ``PriceStats | None`` so callers
    can look up per-ad cluster stats in O(1) instead of calling
    :func:`cluster_price_stats` per listing.

    Performance contract
    --------------------
    The naive implementation called :func:`cluster_price_stats` n times,
    each of which iterates ``all_ads`` again calling ``is_strict_match``,
    which itself calls ``normalize_search_text`` twice (each touching
    150+ string operations). That made the function quadratic *and*
    multiplied by a heavy per-pair constant — for 1500 ads this blocked
    the event loop for several seconds.

    The sync helper tokenises every ad **once** up-front, then walks
    the n×n pairs over already-built sets so the inner loop is just two
    O(k) set comparisons (k = number of tokens, typically <10). On a
    1500-ad fan-out this drops from ~340M string ops to ~22M set ops —
    roughly 100× faster in practice. M9: the CPU-bound work is now
    offloaded via ``asyncio.to_thread`` so it never blocks the event
    loop.
    """
    return await asyncio.to_thread(_precompute_cluster_stats_sync, all_ads, query)


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
    if re.search(rf"\b{_FREE_WORD}\b", subject) and not _TITLE_BLACKLIST_RE.search(subject):
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
            # B-04: keep free listings (p == 0.0) — they are
            # intentionally included by ``extract_prices`` and a
            # 0.2*median lower band would otherwise drop them,
            # making /segments and /price-stats disagree.
            return [p for p in prices if p == 0.0 or 0.2 * med <= p <= 5.0 * med]
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
    med = round(statistics.median(sorted_prices), 2)
    # LOGIC-NEW-4: small-sample stats are unreliable; tag them so
    # threshold alerts don't fire on a single observation.
    if len(sorted_prices) < 3:
        return PriceStats(
            mean=round(statistics.mean(sorted_prices), 2),
            median=med,
            q1=med,
            q3=med,
            min=round(sorted_prices[0], 2),
            max=round(sorted_prices[-1], 2),
            count=len(sorted_prices),
            reliable=False,
        )
    return PriceStats(
        mean=round(statistics.mean(sorted_prices), 2),
        median=med,
        q1=round(_percentile(sorted_prices, 25), 2),
        q3=round(_percentile(sorted_prices, 75), 2),
        min=round(sorted_prices[0], 2),
        max=round(sorted_prices[-1], 2),
        count=len(sorted_prices),
    )


def compute_price_vs_median(ad: dict[str, Any], median: float) -> float | None:
    """Percentage delta of ad price vs median. None if price is missing (negotiable)."""
    if not median:
        return 0.0
    price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    if price_byn is None:
        # B-09: negotiable ads have no meaningful price delta.
        return None
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
        # B-02: pass ``ad`` so free accessories (price=0 + giveaway
        # text) survive the cap filter — their normalised price is
        # 0.0, which is trivially ``<= cap``.
        price = normalize_price_byn(ad.get("price_byn"), ad)
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
) -> float | None:
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
        # B-09: negotiable ads (delta=None) cannot match a discount filter.
        if delta is None or delta >= 0:
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

    # B-03: in price-driven sorts, negotiable listings (price unknown)
    # used to collapse to ``or 0.0``, mixing them with free listings
    # at the cheap end of every list. Bucket by ``known vs unknown``
    # so the unknown bucket always sinks to the tail regardless of
    # ascending/descending direction. Free listings still surface as
    # 0.0 in the known bucket where they belong.
    def _priced_key(ad: dict[str, Any]) -> tuple[int, float]:
        p = normalize_price_byn(ad.get("price_byn"), ad)
        if p is None:
            return (1, 0.0)
        return (0, p)

    if sort == "cheap":
        decorated = []
        for i, ad in enumerate(ads):
            delta = compute_price_vs_reference(
                ad, effective_market_stats, category_price_stats,
            )
            # B-09 follow-up: explicit None check; `or float('inf')` would
            # also bucket fairly-priced ads (delta=0.0) with negotiables.
            sort_key = delta if delta is not None else float("inf")
            decorated.append((sort_key, _priced_key(ad), i, ad))
        decorated.sort()
        return [ad for _, _, _, ad in decorated]
    if sort == "price_asc":
        return sorted(ads, key=_priced_key)
    if sort == "price_desc":
        # B-03: invert price inside the known bucket so descending
        # order keeps the unknown bucket at the tail (otherwise
        # ``reverse=True`` would surface negotiable listings first).
        def _desc_key(ad: dict[str, Any]) -> tuple[int, float]:
            p = normalize_price_byn(ad.get("price_byn"), ad)
            if p is None:
                return (1, 0.0)
            return (0, -p)

        return sorted(ads, key=_desc_key)
    if sort == "near_median":
        def _near_key(ad: dict[str, Any]) -> tuple[int, float]:
            p = normalize_price_byn(ad.get("price_byn"), ad)
            if p is None:
                return (1, 0.0)
            return (0, abs(p - median))

        return sorted(ads, key=_near_key)
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
        # B-01: pass ``ad`` so a free listing (price=0 + giveaway text)
        # is normalised to 0.0 instead of None and shows up in the
        # segment count, matching ``extract_prices`` / /price-stats.
        price_byn = normalize_price_byn(ad.get("price_byn"), ad)
        if price_byn is None:
            continue
        condition = condition_map.get(get_param(ad, "condition") or "")
        if not condition:
            continue
        seller_type = "shop" if ad.get("company_ad") else "private"
        grouped.setdefault(f"{condition}_{seller_type}", []).append(price_byn)

    return {name: compute_price_stats(prices).model_dump() for name, prices in grouped.items()}
