from __future__ import annotations

from pathlib import Path

from api.schemas import AI_LISTING_PHOTO_MAX_COUNT


def test_listing_assistant_photo_limit_is_four_everywhere() -> None:
    frontend_js = Path("frontend/js/api_listing_assistant.js").read_text(encoding="utf-8")
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert AI_LISTING_PHOTO_MAX_COUNT == 4
    assert "const MAX_PHOTOS = 4;" in frontend_js
    assert "up to 4 photos" in nginx_conf
