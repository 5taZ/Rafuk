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
    assert "db:" in compose


def test_nginx_has_security_headers() -> None:
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "X-Content-Type-Options" in nginx_conf
    assert "X-Frame-Options" in nginx_conf
    assert "Content-Security-Policy" in nginx_conf
