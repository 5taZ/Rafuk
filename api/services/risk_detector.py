"""Pure-function risk detection for marketplace listings.

Analyzes a single listing (ad dict) against optional market context and
seller signals, returning a list of structured risk dicts::

    {"type": "too_cheap",  "level": "high",   "message": "Цена более 40% ниже медианы"}
    {"type": "suspicious_desc", "level": "medium", "message": "Подозрительные слова в описании"}
    {"type": "duplicate",  "level": "high",   "message": "Дубликат от того же продавца"}

Risk levels: ``"high"`` / ``"medium"``.
Overall score: ``"high"`` if any factor is high, ``"medium"`` if any
risks exist, ``"low"`` if none.
"""
from __future__ import annotations

import re
from typing import Any

from api.services.aggregator import normalize_price_byn

# Words commonly used in scam/fraud listings on BY marketplaces.
_SUSPICIOUS_WORDS = re.compile(
    r"\b(предоплата|на карту|перевод(?:ить|ит|оди|ела)?\s|аванс|задаток|предоплат[аы]|"
    r"перевед[иь]|карт[аыу]\s*\d{4}|qiwi|киви|webmoney|яндекс\s*деньги)",
    re.IGNORECASE,
)


def detect_risks(
    ad: dict[str, Any],
    market_stats: dict[str, Any] | None = None,
    seller_info: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return a list of risk dicts for *ad* given optional context.

    Parameters
    ----------
    ad:
        Raw Kufar ad dict.  Expected keys: ``price_byn`` (kopecks),
        ``company_ad``, ``feedback_info``, ``body`` / ``body_short``,
        ``ad_parameters``, ``account_parameters``.
    market_stats:
        Dict with at least a ``median`` key (in BYN, **not** kopecks).
        If *None* the too-cheap check is skipped.
    seller_info:
        Reserved for future use (e.g. seller listing count from DB).
        Currently unused — new-seller detection works off the ad dict
        itself.
    """
    risks: list[dict[str, str]] = []
    _check_too_cheap(ad, market_stats, risks)
    _check_suspicious_desc(ad, risks)
    _check_duplicate(ad, seller_info, risks)
    return risks


def compute_risk_score(risks: list[dict[str, str]]) -> str:
    """Derive an overall risk label from a list of risk dicts.

    Returns ``"high"`` if any factor is high, ``"medium"`` if any
    risks exist, ``"low"`` if the list is empty.
    """
    if not risks:
        return "low"
    if any(r.get("level") == "high" for r in risks):
        return "high"
    return "medium"


# ── Individual checks ──────────────────────────────────────────────────


def _check_too_cheap(
    ad: dict[str, Any],
    market_stats: dict[str, Any] | None,
    risks: list[dict[str, str]],
) -> None:
    if not market_stats:
        return
    median = market_stats.get("median")
    if not median or median <= 0:
        return
    price_byn = normalize_price_byn(ad.get("price_byn"))
    if not price_byn or price_byn <= 0:
        return
    if price_byn < median * 0.68:
        risks.append({
            "type": "too_cheap",
            "level": "high",
            "message": "Цена более 32% ниже медианы",
        })


def _check_suspicious_desc(
    ad: dict[str, Any],
    risks: list[dict[str, str]],
) -> None:
    description = ad.get("body") or ad.get("body_short") or ""
    if not description or not isinstance(description, str):
        return
    if _SUSPICIOUS_WORDS.search(description):
        risks.append({
            "type": "suspicious_desc",
            "level": "medium",
            "message": "Подозрительные слова в описании",
        })


def _check_duplicate(
    ad: dict[str, Any],
    seller_info: dict[str, Any] | None,
    risks: list[dict[str, str]],
) -> None:
    # Duplicate detection requires DB context (seller_info) which is
    # populated by the listing-detail endpoint.  When seller_info is
    # None (e.g. bulk listing mapper) this check is silently skipped.
    if not seller_info:
        return
    if seller_info.get("is_duplicate"):
        risks.append({
            "type": "duplicate",
            "level": "high",
            "message": "Дубликат от того же продавца",
        })
