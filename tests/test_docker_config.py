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
    assert "AI_API_KEY:" in compose
    assert "AI_BASE_URL:" in compose
    assert "AI_CHAT_MIN_INTERVAL_SECONDS:" in compose
    assert "AI_PHOTO_PRECHECK_ENABLED:" in compose


def test_redis_host_port_only_in_override() -> None:
    """PR-18: the base ``docker-compose.yml`` must NOT publish the
    Redis port — production deploys that pass ``-f docker-compose.yml``
    explicitly should leave the cache internal to ``kufar-net``. Local
    development gets the port via ``docker-compose.override.yml`` which
    Compose auto-merges on ``docker compose up`` (no ``-f`` flags).
    """
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    override = Path("docker-compose.override.yml").read_text(encoding="utf-8")

    # Base file: no ``ports:`` mapping for redis — only the inline
    # comment that explains why.
    assert "127.0.0.1:6380:6379" not in compose, (
        "Redis host port mapping must live in docker-compose.override.yml, "
        "not the base docker-compose.yml — see PR-18."
    )
    # Override carries the host mapping for local dev.
    assert "127.0.0.1:6380:6379" in override
    # And the override's redis stanza loopback-binds (no public
    # interface even on a developer's box).
    assert "127.0.0.1" in override


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


def test_nginx_cors_origin_is_strict_allowlist() -> None:
    """PR-19: the nginx CORS allowlist must spell each Telegram
    Mini-App embedder out, not match every ``*.telegram.org``
    subdomain via regex. A subdomain takeover on an unrelated
    ``*.telegram.org`` host would otherwise inherit
    ``Access-Control-Allow-Origin`` for the API.
    """
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")

    # The wildcard regex from before must be gone.
    assert (
        '~* "^https://[a-z0-9.-]+\\.telegram\\.org$"' not in nginx_conf
    ), (
        "Old wildcard regex still present — PR-19 expects an explicit "
        "Mini-App embedder allowlist."
    )
    # Each known embedder is matched exactly.
    for origin in (
        'https://web.telegram.org',
        'https://webk.telegram.org',
        'https://webz.telegram.org',
    ):
        assert (
            f'$http_origin = "{origin}"' in nginx_conf
        ), f"missing exact-match guard for {origin}"
