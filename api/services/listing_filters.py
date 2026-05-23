"""Shared listing-filter helpers.

D-1 / D-2 (audit follow-up): the listings router and the tracker
matcher used to maintain two divergent copies of the same
"does this ad match my filter?" logic. The router accepted both
text labels (``Новый``/``Б/у``) and numeric codes (``1``/``2``)
for the ``condition`` filter and casefolded the ``region`` value
on both sides; the tracker matcher only accepted numeric codes
and compared regions case-sensitively, silently dropping legitimate
matches when Kufar happened to return text labels or the user typed
a region with non-canonical casing.

This module is the single source of truth for the comparison logic.
Both call sites import the same two predicates.
"""
from __future__ import annotations

from typing import Any

from api.services.aggregator import get_param
from api.services.market_signals import area_label, region_label

__all__ = [
    "match_condition_filter",
    "match_region_filter",
    "normalize_filter_text",
]

# D-1: Kufar's ``condition`` parameter has shipped both as text
# labels and numeric codes over the years; accept either. The
# unparametrised "true" (no condition selected) short-circuits
# to True so the helper can be used unconditionally.
_CONDITION_ALLOWED: dict[str, frozenset[str]] = {
    "new": frozenset({"new", "новый", "2"}),
    "used": frozenset({"used", "б/у", "бу", "1"}),
}


def normalize_filter_text(value: str | None) -> str:
    """Casefold + collapse whitespace, NFC-stable enough for region/condition."""
    return " ".join(str(value or "").casefold().split())


def match_condition_filter(ad: dict[str, Any], condition: str | None) -> bool:
    """True when *ad* matches the requested condition (or filter is absent)."""
    if not condition:
        return True
    normalized = normalize_filter_text(get_param(ad, "condition"))
    allowed = _CONDITION_ALLOWED.get(condition)
    return normalized in allowed if allowed else True


def match_region_filter(ad: dict[str, Any], region_name: str | None) -> bool:
    """True when *ad* matches the requested region (or area), case-folded."""
    target = normalize_filter_text(region_name)
    if not target:
        return True
    return target in {
        normalize_filter_text(region_label(ad)),
        normalize_filter_text(area_label(ad)),
    }
