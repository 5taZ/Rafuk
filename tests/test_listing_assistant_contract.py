from __future__ import annotations

import re
from pathlib import Path

from api.routers.ai_listing_assistant import (
    _LISTING_PHOTO_MAX_BYTES,
    _coerce_listing_photos,
)
from api.schemas import AI_LISTING_PHOTO_MAX_COUNT


def test_listing_assistant_photo_limit_is_four_everywhere() -> None:
    frontend_js = Path("frontend/js/api_listing_assistant.js").read_text(encoding="utf-8")
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert AI_LISTING_PHOTO_MAX_COUNT == 4
    assert "const MAX_PHOTOS = 4;" in frontend_js
    assert "up to 4 photos" in nginx_conf


def test_coerce_listing_photos_rejects_oversize_before_regex() -> None:
    """OPUS-9: oversized payloads must be discarded by the cheap len()
    check, never the expensive base64 regex. Past the cap we feed a
    payload that *would* match the regex but is too long — coverage
    confirms it's filtered without the regex ever running on a
    multi-megabyte string.
    """
    valid_prefix = "data:image/jpeg;base64,"
    # Build something that's well past _LISTING_PHOTO_MAX_BYTES yet
    # otherwise regex-valid.
    oversized = valid_prefix + "A" * (_LISTING_PHOTO_MAX_BYTES + 32)
    cleaned = _coerce_listing_photos([oversized])
    assert cleaned == []


def test_coerce_listing_photos_keeps_valid_entries() -> None:
    body = "A" * 64
    valid = f"data:image/png;base64,{body}"
    invalid_scheme = f"data:image/svg+xml;base64,{body}"
    cleaned = _coerce_listing_photos([valid, invalid_scheme, "not a data url"])
    assert cleaned == [valid]


def test_coerce_listing_photos_caps_count() -> None:
    body = "A" * 32
    entries = [f"data:image/png;base64,{body}{i}" for i in range(AI_LISTING_PHOTO_MAX_COUNT + 5)]
    cleaned = _coerce_listing_photos(entries)
    assert len(cleaned) == AI_LISTING_PHOTO_MAX_COUNT
    # Sanity — every returned entry actually matches the regex shape we
    # advertise in the validator above.
    pattern = re.compile(r"^data:image/(jpeg|png|webp|jpg);base64,[A-Za-z0-9+/=]+$")
    assert all(pattern.match(entry) for entry in cleaned)
