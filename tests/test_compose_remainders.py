"""docker-compose.yml: docs off and /healthz for the API (L3), optional Redis password (M7)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _services() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def _interpolate(value: str, env: dict[str, str]) -> str:
    """Minimal compose interpolation: ``${VAR}``, nested ``${VAR:-default}``, ``${VAR-default}``
    (default only when unset) and ``$$``."""
    result: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("$$", index):
            result.append("$")
            index += 2
            continue
        if not value.startswith("${", index):
            result.append(value[index])
            index += 1
            continue
        depth, end = 0, index
        while True:
            if value.startswith("${", end):
                depth += 1
                end += 2
                continue
            if value[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        body = value[index + 2 : end]
        name, sep, default = body.partition(":-")
        if sep:
            current = env.get(name, "")
            result.append(current if current else _interpolate(default, env))
        else:
            name, sep, default = body.partition("-")
            if name in env or not sep:
                result.append(env.get(name, ""))
            else:
                result.append(_interpolate(default, env))
        index = end + 1
    return "".join(result)


def _redis_url(env: dict[str, str]) -> str:
    return _interpolate(_services()["api"]["environment"]["REDIS_URL"], env)


def test_compose_api_disables_docs_and_checks_healthz() -> None:
    api = _services()["api"]
    assert _interpolate(api["environment"]["API_DOCS_ENABLED"], {}) == "false"
    assert _interpolate(api["environment"]["API_DOCS_ENABLED"], {"API_DOCS_ENABLED": "true"}) == (
        "false"
    )
    test = " ".join(api["healthcheck"]["test"])
    assert "/healthz" in test
    assert "status != 200" in test


def test_compose_redis_url_contains_password_only_when_set() -> None:
    from redis.connection import parse_url

    without = _redis_url({})
    assert without == "redis://:@redis:6379/0"
    assert "password" not in parse_url(without)

    with_password = _redis_url({"REDIS_PASSWORD": "s3cret"})
    assert with_password == "redis://:s3cret@redis:6379/0"
    assert parse_url(with_password)["password"] == "s3cret"

    override = {"REDIS_PASSWORD": "s3cret", "COMPOSE_REDIS_URL": "redis://cache:6379/1"}
    assert _redis_url(override) == "redis://cache:6379/1"


def test_compose_redis_passes_password_to_container() -> None:
    redis = _services()["redis"]
    assert redis["environment"]["REDIS_PASSWORD"] == "${REDIS_PASSWORD:-}"
    assert redis["ports"] == ["127.0.0.1:6380:6379"]


def _database_url(env: dict[str, str]) -> str:
    return _interpolate(_services()["api"]["environment"]["DATABASE_URL"], env)


def test_compose_database_url_with_url_safe_password() -> None:
    from sqlalchemy.engine import make_url

    password = "0f3a9c-_.~Zz"  # every character class .env.example allows
    url = make_url(_database_url({"POSTGRES_PASSWORD": password}))
    assert (url.username, url.password, url.host, url.port) == ("user", password, "db", 5432)

    # Compose cannot encode: an unsafe password silently changes the URL, which is why
    # .env.example and README require URL-safe characters (or COMPOSE_DATABASE_URL).
    broken = make_url(_database_url({"POSTGRES_PASSWORD": "a@b"}))
    assert broken.password != "a@b"

    encoded = "postgresql+asyncpg://user:a%40b@db:5432/kufar"
    assert make_url(_database_url({"COMPOSE_DATABASE_URL": encoded})).password == "a@b"


def _render_nginx_template(env: dict[str, str]) -> str:
    """What the nginx image's envsubst step produces for the frontend environment."""
    frontend = _services()["frontend"]["environment"]
    rendered = {name: _interpolate(str(value), env) for name, value in frontend.items()}
    pattern = re.compile(rendered.pop("NGINX_ENVSUBST_FILTER"))
    template = (ROOT / "nginx" / "templates" / "default.conf.template").read_text("utf-8")
    for name, value in rendered.items():
        if pattern.search(name):
            template = template.replace(f"${{{name}}}", value)
    assert "${" not in template.split("server {", 1)[0].split("upstream kufar_api {", 1)[1]
    return template


def test_nginx_upstream_resolves_compose_api_dynamically() -> None:
    conf = _render_nginx_template({})
    assert re.search(r"^\s*server api:8000 resolve;", conf, re.MULTILINE)


def test_nginx_upstream_in_host_mode_is_resolved_from_etc_hosts() -> None:
    # start-local.sh: host.docker.internal comes from extra_hosts (/etc/hosts), which nginx's
    # runtime resolver does not read, so "resolve" must not be used for it.
    env = {"NGINX_API_UPSTREAM": "host.docker.internal:8010", "NGINX_API_UPSTREAM_PARAMS": ""}
    conf = _render_nginx_template(env)
    assert re.search(r"^\s*server host\.docker\.internal:8010 ;", conf, re.MULTILINE)
    assert "resolve;" not in conf


SH = shutil.which("sh") or shutil.which("bash")


def _run_shell(script: str, env: dict[str, str], tmp_path: Path) -> str:
    """Run a compose shell snippet with stub docker-entrypoint.sh and redis-cli on PATH."""
    assert SH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stubs = {
        "docker-entrypoint.sh": 'printf "%s|" "$@"\n',
        "redis-cli": 'if [ "${STUB_PASSWORD:-}" = "${REDISCLI_AUTH:-}" ]; then echo PONG; '
        'else echo "NOAUTH Authentication required."; fi\n',
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    run_env = {**os.environ, **env, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        [SH, "-c", _interpolate(script, {})],
        capture_output=True,
        text=True,
        env=run_env,
        check=False,
    )
    return f"{result.returncode}:{result.stdout}"


@pytest.mark.skipif(
    SH is None or "system32" in SH.lower(),
    reason="a native POSIX shell is not installed",
)
def test_compose_redis_command_and_healthcheck_scripts(tmp_path: Path) -> None:
    redis = _services()["redis"]
    command = redis["command"]
    assert command[:2] == ["sh", "-c"]
    healthcheck = redis["healthcheck"]["test"]
    assert healthcheck[0] == "CMD-SHELL"

    assert _run_shell(command[2], {"REDIS_PASSWORD": ""}, tmp_path) == "0:redis-server|"
    assert _run_shell(command[2], {"REDIS_PASSWORD": "p w"}, tmp_path) == (
        "0:redis-server|--requirepass|p w|"
    )

    assert _run_shell(healthcheck[1], {"REDIS_PASSWORD": ""}, tmp_path) == "0:"
    assert (
        _run_shell(
            healthcheck[1], {"REDIS_PASSWORD": "s3cret", "STUB_PASSWORD": "s3cret"}, tmp_path
        )
        == "0:"
    )
    # A wrong password makes the healthcheck fail.
    assert _run_shell(
        healthcheck[1], {"REDIS_PASSWORD": "wrong", "STUB_PASSWORD": "s3cret"}, tmp_path
    ).startswith("1:")
