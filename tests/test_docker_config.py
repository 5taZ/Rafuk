from __future__ import annotations

from pathlib import Path


def test_dockerfile_mentions_service_switch() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "SERVICE=api" in dockerfile
    assert "uvicorn api.main:app" in dockerfile


def test_compose_has_core_services() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "api:" in compose
    assert "bot:" in compose
    assert "scheduler:" in compose
    assert "redis:" in compose
    assert "127.0.0.1:6380:6379" in compose
    assert "AI_API_KEY:" in compose
    assert "AI_BASE_URL:" in compose
    assert "AI_CHAT_MIN_INTERVAL_SECONDS:" in compose
    assert "AI_PHOTO_PRECHECK_ENABLED:" in compose


def test_nginx_has_cors_header() -> None:
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "Access-Control-Allow-Origin" in nginx_conf
    assert "Access-Control-Allow-Methods" in nginx_conf
    assert "Access-Control-Allow-Headers" in nginx_conf
    assert "return 204" in nginx_conf
    assert "Access-Control-Allow-Origin *" not in nginx_conf


def test_nginx_has_frontend_security_headers() -> None:
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in nginx_conf
    assert "img-src 'self' https://rms.kufar.by https://*.kufar.by data: blob:" in nginx_conf
    assert "object-src 'none'" in nginx_conf
    assert "Permissions-Policy" in nginx_conf
