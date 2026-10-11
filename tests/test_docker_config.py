from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

# Paths are built from the repository root, so the tests pass from any working directory.
ROOT = Path(__file__).resolve().parents[1]
NGINX_TEMPLATE = ROOT / "nginx" / "templates" / "default.conf.template"
PYTHON_SERVICES = ("api", "bot", "scheduler")
LOCAL_SCRIPTS = ("start-local.sh", "stop-local.sh", "status-local.sh")


def _compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _services() -> dict[str, Any]:
    return _compose()["services"]


def _nginx_conf() -> str:
    return NGINX_TEMPLATE.read_text(encoding="utf-8")


def _location_block(conf: str, header: str) -> str:
    start = conf.index(header)
    return conf[start : conf.index("}", start)]


def test_dockerfile_mentions_service_switch() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "SERVICE=api" in dockerfile
    assert "uvicorn api.main:app" in dockerfile


def test_dockerfile_installs_dependencies_before_copying_code() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    deps_copy = dockerfile.index("COPY pyproject.toml uv.lock")
    deps_sync = dockerfile.index("uv sync --locked --no-dev --no-install-project")
    code_copy = dockerfile.index("COPY . .")
    assert deps_copy < deps_sync < code_copy < dockerfile.index("uv sync --locked --no-dev\n")
    # --frozen would install a stale uv.lock as is instead of failing the build.
    assert "--frozen" not in dockerfile
    assert "nodejs" not in dockerfile
    assert "uv run" not in dockerfile.split("CMD ", 1)[1]
    assert "pip install uv" not in dockerfile
    assert re.search(r"ARG UV_VERSION=\d+\.\d+\.\d+", dockerfile)
    assert "--proxy-headers" in dockerfile
    assert "--forwarded-allow-ips" in dockerfile
    assert "USER appuser" in dockerfile


def test_dockerignore_keeps_secrets_and_data_out_of_image() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for pattern in (".env", "*.db", ".git", ".run", ".venv", "tests"):
        assert pattern in patterns
    # hatchling needs the readme declared in pyproject.toml.
    assert "!README.md" in patterns


def test_compose_has_core_services() -> None:
    services = _services()
    for name in ("migrate", "api", "bot", "scheduler", "frontend", "db", "redis"):
        assert name in services


def test_compose_runs_migrations_before_python_services() -> None:
    services = _services()
    migrate = services["migrate"]
    assert migrate["command"] == ["alembic", "-c", "migrations/alembic.ini", "upgrade", "head"]
    assert migrate["depends_on"]["db"]["condition"] == "service_healthy"
    assert migrate["image"] == services["api"]["image"]

    for name in PYTHON_SERVICES:
        depends_on = services[name]["depends_on"]
        assert depends_on["migrate"]["condition"] == "service_completed_successfully"
        assert depends_on["db"]["condition"] == "service_healthy"
        assert "api" not in depends_on


def test_compose_restart_policies() -> None:
    services = _services()
    for name in (*PYTHON_SERVICES, "frontend", "db", "redis"):
        assert services[name]["restart"] == "unless-stopped"
    assert services["migrate"]["restart"] == "no"


def test_compose_shares_one_environment() -> None:
    services = _services()
    keys = {
        "BOT_TOKEN",
        "DATABASE_URL",
        "REDIS_URL",
        "API_BASE_URL",
        "MINI_APP_URL",
        "KUFAR_REQUEST_DELAY",
        "KUFAR_PARALLEL_SEMAPHORE",
        "KUFAR_TIMEOUT",
        "ALERT_CHECK_INTERVAL",
        "CACHE_TTL_SECONDS",
        "INIT_DATA_MAX_AGE_SECONDS",
    }
    environments = []
    for name in ("migrate", *PYTHON_SERVICES):
        environment = dict(services[name]["environment"])
        assert environment.pop("SERVICE") == name
        assert keys <= environment.keys()
        environments.append(environment)
    assert all(environment == environments[0] for environment in environments)
    # Containers reach Postgres by service name, whatever the host-mode .env says.
    assert "@db:5432/" in environments[0]["DATABASE_URL"]


def test_compose_publishes_db_and_redis_on_loopback_only() -> None:
    services = _services()
    assert services["db"]["ports"] == ["127.0.0.1:5433:5432"]
    assert services["redis"]["ports"] == ["127.0.0.1:6380:6379"]
    assert "ports" not in services["api"]


def test_compose_api_healthcheck_does_not_use_docs() -> None:
    test = " ".join(_services()["api"]["healthcheck"]["test"])
    assert "/docs" not in test
    assert "curl" not in test


def test_compose_frontend_uses_nginx_template() -> None:
    frontend = _services()["frontend"]
    assert frontend["environment"]["API_UPSTREAM"] == "${NGINX_API_UPSTREAM:-api:8000}"
    assert "./nginx/templates:/etc/nginx/templates:ro" in frontend["volumes"]
    assert frontend["depends_on"]["api"]["condition"] == "service_healthy"


def test_compose_frontend_follows_recreated_api() -> None:
    frontend = _services()["frontend"]
    # Compose restarts nginx whenever it recreates or restarts api ...
    assert frontend["depends_on"]["api"]["restart"] is True
    # ... and nginx re-resolves the api name itself ("resolve" needs nginx >= 1.27.3).
    params = frontend["environment"]["API_UPSTREAM_PARAMS"]
    assert params == "${NGINX_API_UPSTREAM_PARAMS-resolve}"
    match = re.fullmatch(r"nginx:(\d+)\.(\d+)(?:\.\d+)?-alpine", frontend["image"])
    assert match is not None
    assert (int(match.group(1)), int(match.group(2))) >= (1, 28)


def test_compose_frontend_is_published_on_loopback_only() -> None:
    # nginx trusts X-Forwarded-For from Docker's private addresses; a public port would let
    # clients relayed by docker-proxy spoof their address.
    assert _services()["frontend"]["ports"] == ["${FRONTEND_BIND_ADDRESS:-127.0.0.1}:8081:80"]


def test_nginx_upstream_comes_from_template_variable() -> None:
    conf = _nginx_conf()
    assert "server ${API_UPSTREAM} ${API_UPSTREAM_PARAMS};" in conf
    assert "host.docker.internal" not in _location_block(conf, "location ^~ /api/")
    assert "proxy_pass http://kufar_api;" in conf
    assert NGINX_TEMPLATE.is_file()
    # The old static config must not come back next to the template.
    assert not (ROOT / "nginx" / "default.conf").exists()


def test_nginx_upstream_resolves_api_name_at_runtime() -> None:
    upstream = _location_block(_nginx_conf(), "upstream kufar_api {")
    # "resolve" needs the peers in shared memory and a resolver: Docker's embedded DNS.
    assert re.search(r"^\s*zone kufar_api \d+k;", upstream, re.MULTILINE)
    assert re.search(r"^\s*resolver 127\.0\.0\.11 valid=\d+s", upstream, re.MULTILINE)


def test_nginx_trusts_forwarded_for_only_from_private_addresses() -> None:
    trusted = re.findall(r"^\s*set_real_ip_from\s+(\S+);", _nginx_conf(), re.MULTILINE)
    assert trusted
    for address in trusted:
        network = ipaddress.ip_network(address, strict=True)
        assert network.is_private, address
        assert network.prefixlen >= 8, address


def test_nginx_has_no_wildcard_cors() -> None:
    conf = _nginx_conf()
    assert "Access-Control-Allow-Origin" not in conf


def test_nginx_security_and_cache_headers() -> None:
    conf = _nginx_conf()
    assert 'X-Content-Type-Options "nosniff"' in conf
    assert "Referrer-Policy" in conf
    # The Mini App runs inside an iframe in Telegram Web.
    assert "add_header X-Frame-Options" not in conf

    spa = _location_block(conf, "location / {")
    assert 'Cache-Control "no-cache"' in spa
    assert "try_files $uri $uri/ /index.html;" in spa

    static = _location_block(conf, r"location ~* \.(?:js|mjs|css|map)$")
    assert "try_files $uri =404;" in static
    assert "must-revalidate" in static


def test_local_scripts_do_not_use_global_pkill_patterns() -> None:
    for script in LOCAL_SCRIPTS:
        text = (ROOT / script).read_text(encoding="utf-8")
        assert "pkill -f" not in text
        assert "pgrep -f" not in text

    start = (ROOT / "start-local.sh").read_text(encoding="utf-8")
    assert "--proxy-headers" in start
    assert "NGINX_API_UPSTREAM=" in start
    assert "docker compose up -d --wait db redis" in start
    assert "--host 0.0.0.0" not in start
    # host.docker.internal lives in /etc/hosts, which nginx's runtime resolver ignores.
    assert 'NGINX_API_UPSTREAM_PARAMS=""' in start


def _ci_steps(job: str) -> list[dict[str, Any]]:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return yaml.safe_load(ci)["jobs"][job]["steps"]


@pytest.mark.parametrize("job", ["lint", "test"])
def test_ci_installs_dependencies_from_up_to_date_lockfile(job: str) -> None:
    syncs = [step["run"] for step in _ci_steps(job) if "uv sync" in step.get("run", "")]
    assert syncs
    # --locked fails when uv.lock is out of date with pyproject.toml instead of re-locking.
    assert all("uv sync --locked" in run for run in syncs)


def test_gitattributes_forces_lf_for_scripts_and_configs() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for pattern in ("*.sh", "Dockerfile", "*.conf", "*.template"):
        assert re.search(rf"^{re.escape(pattern)}\s+.*eol=lf", attributes, re.MULTILINE)


def test_gitignore_keeps_markdown_and_ignores_databases() -> None:
    rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.md" not in rules
    assert "*.db" in rules
    assert "bot.db" in rules


def test_env_example_documents_settings() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "INIT_DATA_MAX_AGE_SECONDS=" in env_example
    assert "SCHEDULER_SNAPSHOT_HOUR" not in env_example
    assert "DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5433/kufar" in env_example
    assert "REDIS_URL=redis://localhost:6380/0" in env_example


def test_database_password_url_safety_is_documented() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    credentials = env_example.split("POSTGRES_DB=", 1)[0].rsplit("\n\n", 1)[1]
    assert "URL-safe" in credentials
    assert "COMPOSE_DATABASE_URL" in credentials
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    password_note = readme.split("`POSTGRES_PASSWORD` into its `DATABASE_URL`", 1)[1][:400]
    assert "URL-safe" in password_note
    assert "COMPOSE_DATABASE_URL" in password_note


BASH = shutil.which("bash")


# The WSL launcher (System32\bash.exe) cannot read Windows paths reliably; skip it.
@pytest.mark.skipif(
    BASH is None or "system32" in BASH.lower(), reason="a native bash is not installed"
)
@pytest.mark.parametrize("script", LOCAL_SCRIPTS)
def test_local_scripts_parse(script: str) -> None:
    assert BASH is not None
    result = subprocess.run(
        [BASH, "-n", (ROOT / script).as_posix()], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _stop_compose_app_services(compose_ps: str, tmp_path: Path) -> list[str]:
    """Source start-local.sh with a stub docker on PATH; returns the docker calls."""
    assert BASH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls.log"
    ps_output = tmp_path / "ps.out"
    ps_output.write_text(compose_ps, encoding="utf-8", newline="\n")
    stub = bin_dir / "docker"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$*" >>"$STUB_CALLS"\n'
        'case "$*" in "compose ps"*) cat "$STUB_PS" ;; esac\n',
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_CALLS": calls.as_posix(),
        "STUB_PS": ps_output.as_posix(),
    }
    # Sourcing defines the functions without running main (and without any arguments).
    result = subprocess.run(
        [
            BASH,
            "-c",
            'script="$1"; set --; source "$script"; stop_compose_app_services',
            "_",
            (ROOT / "start-local.sh").as_posix(),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".run").exists()
    return calls.read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(
    BASH is None or "system32" in BASH.lower(), reason="a native bash is not installed"
)
def test_start_local_stops_compose_app_containers(tmp_path: Path) -> None:
    calls = _stop_compose_app_services("db\nredis\nbot\nscheduler\nfrontend\n", tmp_path)
    assert calls[0].startswith("compose ps --services --status running")
    # A second bot and scheduler next to the host processes conflict and send duplicates.
    assert calls[1:] == ["compose stop bot scheduler"]


@pytest.mark.skipif(
    BASH is None or "system32" in BASH.lower(), reason="a native bash is not installed"
)
def test_start_local_leaves_infra_containers_running(tmp_path: Path) -> None:
    calls = _stop_compose_app_services("db\nredis\nfrontend\n", tmp_path)
    assert len(calls) == 1
    assert calls[0].startswith("compose ps ")
