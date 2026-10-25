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

import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from api.services.aggregator import get_param
from api.services.kufar_filters import normalize_filter_text
from api.services.market_signals import area_label, region_label

logger = logging.getLogger(__name__)

__all__ = [
    "CONDITION_FILTER_ALIASES",
    "canonical_condition_filter",
    "canonical_filter_value",
    "match_condition_filter",
    "match_region_filter",
    "normalize_filter_text",
    "warn_unknown_filter_value",
]

# D-1: Kufar's ``condition`` parameter has shipped both as text
# labels and numeric codes over the years; accept either. The
# unparametrised "true" (no condition selected) short-circuits
# to True so the helper can be used unconditionally.
_CONDITION_ALLOWED: dict[str, frozenset[str]] = {
    "new": frozenset({"new", "новый", "2"}),
    "used": frozenset({"used", "б/у", "бу", "1"}),
}

# RV-BE-03: the *filter* side has two spellings as well. /listings and
# the bot send the codes "new"/"used", while the Mini App tracker form
# (frontend/index.html, tracker-/edit-condition-select) submits its
# Russian labels "Новый"/"Б/у" verbatim and those are stored as-is. Keys
# are normalize_filter_text() forms, so case and spacing don't matter.
CONDITION_FILTER_ALIASES: dict[str, str] = {
    "new": "new",
    "новый": "new",
    "used": "used",
    "б/у": "used",
}


def canonical_filter_value(
    value: str | None, aliases: Mapping[str, str], *, field: str,
) -> str | None:
    """Map a stored/submitted filter value onto its canonical code.

    ``None``/blank means "no filter". An unrecognised value raises
    ``ValueError`` instead of quietly disabling the filter (RV-BE-03):
    the tracker schemas turn it into a 422, the matchers into "match
    nothing".
    """
    normalized = normalize_filter_text(value)
    if not normalized:
        return None
    try:
        return aliases[normalized]
    except KeyError:
        raise ValueError(
            f"Unsupported {field} filter {value!r}; expected one of {sorted(aliases)}"
        ) from None


def canonical_condition_filter(value: str | None) -> str | None:
    """``"new"``/``"used"`` for a condition filter in any accepted spelling."""
    return canonical_filter_value(value, CONDITION_FILTER_ALIASES, field="condition")


@lru_cache(maxsize=128)
def warn_unknown_filter_value(field: str, value: str) -> None:
    # RV-BE-03: matchers run once per ad per tracker; lru_cache makes this
    # log once per distinct legacy value instead of flooding the log.
    logger.warning(
        "Tracker filter %s=%r is not recognised; the tracker matches nothing until "
        "the filter is fixed",
        field,
        value,
    )


def match_condition_filter(ad: dict[str, Any], condition: str | None) -> bool:
    """True when *ad* matches the requested condition (or filter is absent)."""
    try:
        wanted = canonical_condition_filter(condition)
    except ValueError:
        # RV-BE-03: an unknown value used to return True here, i.e. it
        # silently switched the filter off. Fail closed instead.
        warn_unknown_filter_value("condition", str(condition))
        return False
    if wanted is None:
        return True
    return normalize_filter_text(get_param(ad, "condition")) in _CONDITION_ALLOWED[wanted]


def match_region_filter(ad: dict[str, Any], region_name: str | None) -> bool:
    """True when *ad* matches the requested region (or area), case-folded."""
    target = normalize_filter_text(region_name)
    if not target:
        return True
    return target in {
        normalize_filter_text(region_label(ad)),
        normalize_filter_text(area_label(ad)),
    }
