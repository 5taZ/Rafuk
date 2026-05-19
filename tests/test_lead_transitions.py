from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.routers.workflow import (
    _LEAD_STATUS_TRANSITIONS,
    _validate_lead_status_transition,
)


def _all_allowed_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for from_status, targets in _LEAD_STATUS_TRANSITIONS.items():
        for to_status in sorted(targets):
            pairs.append((from_status, to_status))
    return pairs


def _forbidden_pairs() -> list[tuple[str, str]]:
    all_statuses = set(_LEAD_STATUS_TRANSITIONS.keys())
    pairs: list[tuple[str, str]] = []
    samples: list[tuple[str, str]] = [
        ("sold", "new"),
        ("sold", "bought"),
        ("sold", "in_progress"),
        ("closed", "new"),
        ("closed", "sold"),
        ("closed", "bought"),
        ("bought", "new"),
        ("bought", "reviewing"),
        ("abandoned", "bought"),
        ("abandoned", "sold"),
        ("watching", "sold"),
        ("watching", "closed"),
    ]
    for from_s, to_s in samples:
        if from_s in all_statuses and to_s in all_statuses:
            allowed = _LEAD_STATUS_TRANSITIONS.get(from_s, set())
            if to_s not in allowed:
                pairs.append((from_s, to_s))
    return pairs


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    _all_allowed_pairs(),
    ids=[f"{f}->{t}" for f, t in _all_allowed_pairs()],
)
def test_allowed_transition_succeeds(from_status: str, to_status: str) -> None:
    _validate_lead_status_transition(from_status, to_status)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    _forbidden_pairs(),
    ids=[f"{f}->{t}" for f, t in _forbidden_pairs()],
)
def test_forbidden_transition_raises_422(
    from_status: str, to_status: str,
) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_lead_status_transition(from_status, to_status)
    assert exc.value.status_code == 422
