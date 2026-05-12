from __future__ import annotations

from pathlib import Path


def test_nginx_cors_allows_csrf_header() -> None:
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    header_lines = [
        line for line in nginx_conf.splitlines() if "Access-Control-Allow-Headers" in line
    ]
    assert header_lines
    assert all("X-Requested-With" in line for line in header_lines)


def test_frontend_csp_matches_vendored_telegram_sdk_policy() -> None:
    index_text = Path("frontend/index.html").read_text(encoding="utf-8")
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    nginx_csp_lines = [
        line for line in nginx_conf.splitlines() if "Content-Security-Policy" in line
    ]
    assert "script-src 'self'" in index_text
    assert "script-src 'self'" in nginx_conf
    assert "cdn.jsdelivr.net" not in index_text
    assert "cdn.jsdelivr.net" not in nginx_conf
    assert "script-src 'self' https://telegram.org" not in index_text
    assert all("https://telegram.org" not in line for line in nginx_csp_lines)
