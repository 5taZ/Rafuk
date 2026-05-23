from __future__ import annotations

from pathlib import Path

from fastapi.responses import JSONResponse


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


def test_nginx_api_location_reasserts_hsts() -> None:
    """SEC-NEW-3: the /api/ location block uses add_header which disables
    inheritance — HSTS must be explicitly repeated there."""
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    # Find lines between the /api/ location and its closing brace
    lines = nginx_conf.splitlines()
    in_api_block = False
    found_hsts = False
    for line in lines:
        if "location" in line and "/api/" in line:
            in_api_block = True
        if in_api_block and "Strict-Transport-Security" in line and "add_header" in line:
            found_hsts = True
            break
    assert found_hsts, "HSTS add_header missing from /api/ location block"


def test_apply_security_headers_sets_all_expected_headers() -> None:
    """BE-DEEP-1: unit test for the shared helper that exception handlers use."""
    from api.main import _apply_security_headers

    response = JSONResponse(status_code=500, content={"detail": "boom"})
    _apply_security_headers(response)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert "geolocation=()" in response.headers["Permissions-Policy"]
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-site"


def test_apply_security_headers_does_not_overwrite_existing() -> None:
    """setdefault semantics: pre-existing headers are preserved."""
    from api.main import _apply_security_headers

    response = JSONResponse(status_code=200, content={})
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    _apply_security_headers(response)
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_global_exception_handler_carries_security_headers() -> None:
    """BE-DEEP-1: 500 responses from the exception handler must carry
    the same security headers as normal middleware responses."""
    from fastapi.testclient import TestClient

    from api.main import create_app

    app = create_app()

    @app.get("/_test-500")
    async def _crash():
        raise RuntimeError("deliberate crash")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/_test-500")
        assert resp.status_code == 500
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]
