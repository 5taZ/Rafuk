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

# RV-AI-04: prepayment / money-transfer wording for the AI verdict floor
# (ai_marketplace.build_risk_floor). It feeds a hard rule rather than a
# medium-level hint, so it is narrower than _SUSPICIOUS_WORDS where that
# one misfires on ordinary listings ("перевод Маршака" in books, "киви"
# the fruit) and wider on inflections it misses ("предоплатой",
# "предоплату"). Explicit negations ("без предоплаты", "предоплата не
# нужна") are an honest seller's opposite signal and are dropped first.
_PREPAYMENT_NEGATION_RE = re.compile(
    r"\b(?:без|никак(?:ой|ая|ую|их)|не\s+(?:прошу|беру|требую|надо|нужн[аоы]?))\s+"
    r"(?:\w+\s+)?(?:предоплат|аванс|задат)\w*"
    r"|\b(?:предоплат|аванс|задат)\w*\s+(?:не\s+(?:прошу|беру|требую|надо|нужн)\w*|нет\b)",
    re.IGNORECASE,
)
_PREPAYMENT_RE = re.compile(
    r"\b(?:предоплат\w*|аванс(?:ом|а|у)?\b|задат(?:ок|ка|ку|ком)\b|на\s+карт[уы]\b"
    r"|перевед(?:и|ите)\b|карт[аыу]\s*\d{4}|qiwi|webmoney|яндекс\s*деньги)",
    re.IGNORECASE,
)


def find_prepayment_signals(text: str | None) -> list[str]:
    """Return the prepayment / transfer phrases found in listing text."""
    if not text or not isinstance(text, str):
        return []
    cleaned = _PREPAYMENT_NEGATION_RE.sub(" ", text)
    found: list[str] = []
    for match in _PREPAYMENT_RE.finditer(cleaned):
        phrase = re.sub(r"\s+", " ", match.group(0).lower())
        if phrase not in found:
            found.append(phrase)
    return found


def detect_risks(
    ad: dict[str, Any],
    market_stats: dict[str, Any] | None = None,
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

    E-FIND-04: a previous ``seller_info`` parameter and an
    ``is_duplicate`` check were removed in the May-2026 audit. The
    duplicate path was dead code — every production caller passed
    ``seller_info=None``, so the check never fired. A real duplicate
    detector needs ``Listing`` history (same title+price+region in
    24h) and is tracked separately as a feature in logicissues.md.
    """
    risks: list[dict[str, str]] = []
    _check_too_cheap(ad, market_stats, risks)
    _check_suspicious_desc(ad, risks)
    _check_no_photos(ad, risks)
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
    # H4: pass full ad dict so normalize_price_byn can detect free listings
    # (price=0 → returns 0.0 instead of None when ad context is provided).
    price_byn = normalize_price_byn(ad.get("price_byn"), ad)
    if price_byn is None:
        return
    if price_byn == 0:
        risks.append({
            "type": "too_cheap",
            "level": "high",
            "message": "Бесплатное объявление (цена 0)",
        })
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


def _check_no_photos(
    ad: dict[str, Any],
    risks: list[dict[str, str]],
) -> None:
    """E-FIND-03: zero-photo listings are a medium risk signal."""
    if not (ad.get("images") or ad.get("image_count")):
        risks.append({
            "type": "no_photos",
            "level": "medium",
            "message": "Объявление без фото",
        })
