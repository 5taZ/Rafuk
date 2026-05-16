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


def test_nginx_uses_csp_frame_ancestors_instead_of_x_frame_options() -> None:
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "X-Frame-Options" not in nginx_conf
    # SEC-LOW (issues §1.2): all three Telegram web clients must be
    # listed in frame-ancestors so the Mini App embeds correctly.
    assert (
        "frame-ancestors https://web.telegram.org "
        "https://webk.telegram.org https://webz.telegram.org"
        in nginx_conf
    )


def test_nginx_disables_server_tokens_and_sets_hsts() -> None:
    """SEC-MEDIUM/LOW (issues §1.2): nginx must hide its version
    (server_tokens off) and emit HSTS at the server level so static
    assets carry the header even when /api/ isn't traversed."""
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "server_tokens off;" in nginx_conf
    assert "Strict-Transport-Security" in nginx_conf
    assert "max-age=31536000" in nginx_conf
    assert "includeSubDomains" in nginx_conf


def test_nginx_rate_limit_key_trusts_cf_header_only_from_proxy_peer() -> None:
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "geo $trusted_rate_limit_proxy" in nginx_conf
    assert 'map "$trusted_rate_limit_proxy:$http_cf_connecting_ip" $rate_limit_key' in nginx_conf
    assert "~^1:(.+)$ $1;" in nginx_conf
    assert "default $binary_remote_addr;" in nginx_conf
