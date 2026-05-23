from __future__ import annotations

import re
from pathlib import Path

import yaml


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


def test_compose_ports_bind_loopback_only() -> None:
    """SEC-NEW-2: every published port in the base docker-compose.yml
    must bind to 127.0.0.1 explicitly. Unprefixed ``"NNN:MMM"`` or
    ``0.0.0.0:NNN:MMM`` forms expose the port on all interfaces,
    which lets anyone on the same network bypass nginx/cloudflared.
    """
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    violations: list[str] = []
    for svc_name, svc in compose.get("services", {}).items():
        for port_entry in svc.get("ports") or []:
            entry = str(port_entry)
            # Pure numeric (container-only) is fine (rare but valid).
            if re.fullmatch(r"\d+", entry):
                continue
            # Must start with 127.0.0.1:
            if not entry.startswith("127.0.0.1:"):
                violations.append(f"{svc_name}: {entry}")
    assert not violations, (
        "Ports not bound to 127.0.0.1 in docker-compose.yml: "
        + ", ".join(violations)
    )


def test_compose_services_have_security_hardening() -> None:
    """SEC-NEW-1: every service in the base compose must have
    security_opt: ['no-new-privileges:true'] and cap_drop: ['ALL'].
    """
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    missing_secopt: list[str] = []
    missing_capdrop: list[str] = []
    for svc_name, svc in compose.get("services", {}).items():
        secopt = svc.get("security_opt") or []
        if "no-new-privileges:true" not in secopt:
            missing_secopt.append(svc_name)
        capdrop = svc.get("cap_drop") or []
        if "ALL" not in capdrop:
            missing_capdrop.append(svc_name)
    assert not missing_secopt, (
        "Services missing security_opt no-new-privileges: " + ", ".join(missing_secopt)
    )
    assert not missing_capdrop, (
        "Services missing cap_drop ALL: " + ", ".join(missing_capdrop)
    )


def test_frontend_uses_nginx_unprivileged() -> None:
    """SEC-NEW-9: frontend Dockerfile must use the unprivileged nginx image."""
    dockerfile = Path("Dockerfile.frontend").read_text(encoding="utf-8")
    assert "nginxinc/nginx-unprivileged:1.27-alpine" in dockerfile


def test_frontend_port_mapping_8080() -> None:
    """SEC-NEW-9: frontend maps host 8081 -> container 8080."""
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    frontend = compose["services"]["frontend"]
    ports = frontend.get("ports", [])
    assert any("8081:8080" in str(p) for p in ports), (
        f"frontend ports should map 8081->8080, got: {ports}"
    )
