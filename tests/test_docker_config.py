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


_SECURITY_HEADERS = (
    "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy",
    "Strict-Transport-Security", "Content-Security-Policy",
)


def _security_header_lines(lines: list[str], indent: int) -> list[str]:
    prefix = " " * indent + "add_header "
    return sorted(
        line.strip() for line in lines
        if line.startswith(prefix) and line[len(prefix):].split(" ", 1)[0] in _SECURITY_HEADERS
    )


def test_nginx_locations_with_add_header_repeat_the_security_headers() -> None:
    """SC-05 / IN-09: an ``add_header`` in a location drops every inherited
    server-level ``add_header``. /sw.js and the static-asset location set
    Cache-Control that way, so scripts, stylesheets, SVGs and the service
    worker went out without CSP, nosniff and HSTS. Every location with its
    own add_header must carry the server-level set verbatim."""
    lines = Path("nginx/default.conf").read_text(encoding="utf-8").splitlines()
    server_headers = _security_header_lines(lines, 4)
    assert [line.split(" ", 2)[1] for line in server_headers] == sorted(_SECURITY_HEADERS)
    checked = []
    for start, line in enumerate(lines):
        if not line.startswith("    location "):
            continue
        end = lines.index("    }", start)
        block = lines[start + 1:end]
        if not any(entry.startswith("        add_header ") for entry in block):
            continue
        assert _security_header_lines(block, 8) == server_headers, line
        checked.append(line.split()[-2])
    assert {"/sw.js", "/api/", r"\.(?:js|css|woff2?|svg|ico|png|jpg|jpeg|gif|webp)$"} <= set(
        checked
    ), checked


def test_nginx_api_upstream_keeps_connections_alive() -> None:
    """IN-10: without ``keepalive`` the HTTP/1.1 + ``Connection ""`` settings
    of the /api/ locations did nothing; every request opened a new TCP
    connection to uvicorn."""
    template = Path("nginx/default.conf").read_text(encoding="utf-8")
    upstream = template[template.index("upstream kufar_api {"):]
    upstream = upstream[: upstream.index("\n}")]
    assert re.search(r"^\s*keepalive \d+;", upstream, re.MULTILINE)
    assert template.count('proxy_set_header Connection "";') == template.count("proxy_pass ")


def test_nginx_does_not_serve_the_spa_shell_as_metrics() -> None:
    """IN-18: /metrics through nginx fell through to index.html with 200."""
    template = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert re.search(r"location = /metrics \{\s*return 404;\s*\}", template)


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
    """SEC-NEW-9: frontend Dockerfile must use the unprivileged nginx image.

    RV-OPS-02: the test used to pin the exact ``1.27-alpine`` tag, which
    froze an end-of-life branch. It now requires the unprivileged image on
    1.28 or newer: ``resolve`` in the upstream block needs nginx >= 1.27.3.
    """
    dockerfile = Path("Dockerfile.frontend").read_text(encoding="utf-8")
    match = re.search(
        r"^FROM nginxinc/nginx-unprivileged:(\d+)\.(\d+)(?:\.\d+)?-alpine$",
        dockerfile,
        re.MULTILINE,
    )
    assert match is not None
    assert (int(match.group(1)), int(match.group(2))) >= (1, 28)


def test_frontend_port_mapping_8080() -> None:
    """SEC-NEW-9: frontend maps host 8081 -> container 8080."""
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    frontend = compose["services"]["frontend"]
    ports = frontend.get("ports", [])
    assert any("8081:8080" in str(p) for p in ports), (
        f"frontend ports should map 8081->8080, got: {ports}"
    )


PYTHON_SERVICES = ("migrate", "api", "bot", "scheduler")


def _services() -> dict:
    return yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_dockerfile_starts_services_from_the_virtualenv() -> None:
    """RV-OPS-01: ``uv run`` in CMD re-checked (and could re-lock/re-sync)
    the environment at every start, which fails on the read-only root
    filesystem of the compose services. The services run the venv
    binaries directly, under ``exec`` so they get the stop signal."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    cmd = dockerfile.split("\nCMD ", 1)[1]
    assert "uv run" not in cmd
    for entrypoint in (
        "api) exec /app/.venv/bin/uvicorn api.main:app",
        "bot) exec /app/.venv/bin/python -m bot.main",
        "scheduler) exec /app/.venv/bin/python -m scheduler.collector",
        "migrate) exec /app/.venv/bin/alembic -c migrations/alembic.ini upgrade head",
    ):
        assert entrypoint in cmd


def test_dockerfile_layers_install_locked_dependencies_before_code() -> None:
    """RV-OPS-06 / RV-OPS-13: a pinned uv, ``--locked`` syncs (a stale
    uv.lock fails the build), the dependency layer before the code layer,
    no uv cache in the layers and no ``chown -R`` copy of /app: the code and
    the venv stay root-owned."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG UV_VERSION=\d+\.\d+\.\d+$", dockerfile, re.MULTILINE)
    assert "COPY --from=uv /uv /usr/local/bin/uv" in dockerfile
    assert "pip install" not in dockerfile
    assert "UV_NO_CACHE=1" in dockerfile
    assert "UV_COMPILE_BYTECODE=1" in dockerfile
    assert not re.search(r"^RUN .*\bchown\b", dockerfile, re.MULTILINE)
    assert not re.search(r"^COPY --chown", dockerfile, re.MULTILINE)
    assert "--frozen" not in dockerfile
    deps_copy = dockerfile.index("COPY pyproject.toml uv.lock ./")
    deps_sync = dockerfile.index("RUN uv sync --locked --no-dev --no-install-project\n")
    code_copy = dockerfile.index("COPY . .")
    code_sync = dockerfile.index("RUN uv sync --locked --no-dev\n")
    # IN-16: the editable install left the application code uncompiled.
    compile_code = dockerfile.index(
        "RUN /app/.venv/bin/python -m compileall -q api bot scheduler migrations\n"
    )
    user = dockerfile.index("USER appuser")
    assert deps_copy < deps_sync < code_copy < code_sync < compile_code < user


def test_uv_is_pinned_to_the_same_release_in_dockerfile_and_ci() -> None:
    """RV-OPS-06: the Docker build and CI must run the uv that wrote uv.lock."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    docker_uv = re.search(r"^ARG UV_VERSION=(\S+)$", dockerfile, re.MULTILINE)
    assert docker_uv is not None
    assert workflow["env"]["UV_VERSION"] == docker_uv.group(1)
    setup_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "astral-sh/setup-uv" in step.get("uses", "")
    ]
    assert setup_steps
    assert all(step["with"]["version"] == "${{ env.UV_VERSION }}" for step in setup_steps)


def _dockerignore_rules(path: str) -> list[tuple[bool, re.Pattern[str]]]:
    """Parse a .dockerignore into (negated, regex) rules with Docker's
    matching: patterns are anchored at the context root, ``*`` and ``?`` do
    not cross ``/``, ``**`` does, and the last matching rule wins."""
    rules = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line.lstrip("!").strip("/")
        regex, i = "", 0
        while i < len(pattern):
            if pattern.startswith("**/", i):
                regex, i = regex + "(?:.*/)?", i + 3
            elif pattern.startswith("**", i):
                regex, i = regex + ".*", i + 2
            elif pattern[i] == "*":
                regex, i = regex + "[^/]*", i + 1
            elif pattern[i] == "?":
                regex, i = regex + "[^/]", i + 1
            elif pattern[i] == "[":
                end = pattern.index("]", i)
                regex, i = regex + pattern[i : end + 1], end + 1
            else:
                regex, i = regex + re.escape(pattern[i]), i + 1
        rules.append((negated, re.compile(regex)))
    return rules


def _ignored(rules: list[tuple[bool, re.Pattern[str]]], path: str) -> bool:
    # A rule that matches a parent directory applies to everything under it.
    parts = path.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    ignored = False
    for negated, regex in rules:
        if any(regex.fullmatch(prefix) for prefix in prefixes):
            ignored = not negated
    return ignored


def test_python_image_context_keeps_secrets_tests_and_frontend_out() -> None:
    """RV-OPS-13: the Python image gets its packages and nothing else —
    no .env, databases, tests, docs, frontend or agent state.

    IN-02: the context is an allowlist (``*`` first), because root-anchored
    denylist patterns let nested files such as ``reviews/*.md`` or
    ``api/.env`` into the image."""
    rules = _dockerignore_rules(".dockerignore")
    assert rules[0] == (False, re.compile("[^/]*"))
    for needed in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        ".python-version",
        "api/main.py",
        "api/services/data/marketplace_lexicon.json",
        "bot/main.py",
        "scheduler/collector.py",
        "migrations/alembic.ini",
        "migrations/env.py",
        "migrations/script.py.mako",
    ):
        assert not _ignored(rules, needed), needed
    for excluded in (
        "reviews/08-infra-ci-tests.md",
        "REVIEW.md",
        "api/.env",
        "api/.env.local",
        "bot/local.db",
        "scheduler/notes.md",
        "api/services/key.pem",
        "migrations/__pycache__/env.cpython-312.pyc",
        "some-new-dir/file.txt",
        "proxy-server.mjs",
        ".env",
        ".env.production",
        "bot.db",
        ".git",
        ".venv/bin/python",
        ".claude/settings.local.json",
        "api/__pycache__/main.cpython-312.pyc",
        "tests/conftest.py",
        "docs/BACKUP_RUNBOOK.md",
        "frontend/index.html",
        "assets/logo.svg",
        "nginx/default.conf",
        "scripts/backup.sh",
        "issues.md",
        "start-local.sh",
    ):
        assert _ignored(rules, excluded), excluded


def test_frontend_image_context_is_an_allowlist() -> None:
    """RV-OPS-13: BuildKit reads Dockerfile.frontend.dockerignore instead of
    .dockerignore for the nginx image, whose context is only the static
    bundle, the logos and the nginx template."""
    rules = _dockerignore_rules("Dockerfile.frontend.dockerignore")
    dockerfile = Path("Dockerfile.frontend").read_text(encoding="utf-8")
    sources = re.findall(r"^COPY (\S+) ", dockerfile, re.MULTILINE)
    assert sources == ["nginx/default.conf", "frontend", "assets"]
    for needed in ("nginx/default.conf", "frontend/index.html", "frontend/js/app.js",
                   "assets/logo.svg"):
        assert not _ignored(rules, needed), needed
    for excluded in (".env", "api/main.py", "tests/conftest.py", "uv.lock", ".git", "bot.db",
                     "reviews/08-infra-ci-tests.md", "frontend/.env", "nginx/NOTES.md"):
        assert _ignored(rules, excluded), excluded


def test_compose_python_services_reach_backing_services_by_name() -> None:
    """RV-OPS-02: Postgres and Redis are published on 127.0.0.1 only, which
    host.docker.internal cannot reach on a Linux Docker Engine. The
    containers use postgres:5432 / redis:6379 over kufar-net; the
    DOCKER_* variables only override that for other servers."""
    services = _services()
    for name in PYTHON_SERVICES:
        environment = services[name]["environment"]
        database_url = environment["DATABASE_URL"]
        redis_url = environment["REDIS_URL"]
        assert database_url.startswith("${DOCKER_DATABASE_URL:-postgresql+asyncpg://"), name
        assert "@postgres:5432/" in database_url, name
        assert redis_url.startswith("${DOCKER_REDIS_URL:-redis://:${REDIS_PASSWORD}@"), name
        assert "@redis:6379/" in redis_url, name
        # The host-mode URLs from .env must not leak into the containers.
        assert "${DATABASE_URL}" not in database_url, name
        assert "${REDIS_URL}" not in redis_url, name
        assert "extra_hosts" not in services[name], name
    command = services["redis"]["command"]
    assert command[command.index("--requirepass") + 1].startswith("${REDIS_PASSWORD:?")


def test_compose_migrate_runs_alembic_from_the_image_after_postgres() -> None:
    """RV-OPS-01 / RV-OPS-02: migrate uses the image CMD (SERVICE=migrate,
    the venv's alembic) instead of ``uv run`` with a tmpfs uv cache, and
    waits for the bundled Postgres when the local-db profile runs it."""
    migrate = _services()["migrate"]
    assert "command" not in migrate
    assert migrate["environment"]["SERVICE"] == "migrate"
    assert "UV_CACHE_DIR" not in migrate["environment"]
    assert "/app/.cache" not in migrate["tmpfs"]
    postgres = migrate["depends_on"]["postgres"]
    assert postgres["condition"] == "service_healthy"
    # Optional dependency: without --profile local-db there is no postgres
    # service (DOCKER_DATABASE_URL then points at an external database).
    assert postgres["required"] is False


def test_compose_frontend_proxies_to_the_api_service() -> None:
    """RV-OPS-02: nginx gets its API upstream from the environment: the api
    service by default (re-resolved), the host API only when start-local.sh
    asks for it.

    IN-01: the static shell no longer waits for a healthy api, nor restarts
    with it; the tunnel waits for nginx, which it forwards to."""
    frontend = _services()["frontend"]
    environment = frontend["environment"]
    assert environment["API_UPSTREAM"] == "${NGINX_API_UPSTREAM:-api:8000}"
    # "-", not ":-": start-local.sh passes an explicitly empty value.
    assert environment["API_UPSTREAM_PARAMS"] == "${NGINX_API_UPSTREAM_PARAMS-resolve}"
    # The entrypoint renders the template into conf.d on a read-only rootfs,
    # as nginx-unprivileged's uid 101: a root-owned tmpfs is not writable.
    assert "/etc/nginx/conf.d:uid=101,gid=101" in frontend["tmpfs"]
    dockerfile = Path("Dockerfile.frontend").read_text(encoding="utf-8")
    assert "FROM nginxinc/nginx-unprivileged:" in dockerfile
    assert "api" not in frontend.get("depends_on", {})
    assert _services()["cloudflared"]["depends_on"] == {
        "frontend": {"condition": "service_started"},
    }
    assert "extra_hosts" not in frontend["build"]

    start_local = Path("start-local.sh").read_text(encoding="utf-8")
    assert (
        'NGINX_API_UPSTREAM="host.docker.internal:8010" NGINX_API_UPSTREAM_PARAMS="" \\\n'
        "        docker compose up -d --build frontend --no-deps --force-recreate"
    ) in start_local


def _render_nginx_template(values: dict[str, str]) -> str:
    """envsubst with an explicit variable list, as the nginx entrypoint runs
    it: only the listed names are replaced, every other $name stays."""
    template = Path("nginx/default.conf").read_text(encoding="utf-8")

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return values.get(name, match.group(0))

    return re.sub(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))", substitute, template)


def test_nginx_api_upstream_is_rendered_from_the_environment() -> None:
    """RV-OPS-02: every /api/ location proxies to the kufar_api upstream,
    whose server comes from API_UPSTREAM / API_UPSTREAM_PARAMS; nothing
    else in the file is substituted, so nginx's own variables survive."""
    template = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "server ${API_UPSTREAM} ${API_UPSTREAM_PARAMS};" in template
    assert re.search(r"^\s*zone kufar_api \d+k;", template, re.MULTILINE)
    assert re.search(r"^\s*resolver 127\.0\.0\.11 valid=\d+s", template, re.MULTILINE)
    proxy_targets = re.findall(r"^\s*proxy_pass (\S+);", template, re.MULTILINE)
    assert len(proxy_targets) == 4
    assert all(target.startswith("http://kufar_api/api/") for target in proxy_targets)

    dockerfile = Path("Dockerfile.frontend").read_text(encoding="utf-8")
    assert "COPY nginx/default.conf /etc/nginx/templates/default.conf.template" in dockerfile
    envsubst_filter = re.search(r"NGINX_ENVSUBST_FILTER=(\S+)", dockerfile)
    assert envsubst_filter is not None
    template_variables = set(re.findall(r"\$\{([A-Za-z_]\w*)\}", template))
    assert template_variables == {"API_UPSTREAM", "API_UPSTREAM_PARAMS"}
    assert all(re.search(envsubst_filter.group(1), name) for name in template_variables)
    assert "API_UPSTREAM=api:8000" in dockerfile
    assert "API_UPSTREAM_PARAMS=resolve" in dockerfile

    for values, server in (
        ({"API_UPSTREAM": "api:8000", "API_UPSTREAM_PARAMS": "resolve"},
         "server api:8000 resolve;"),
        ({"API_UPSTREAM": "host.docker.internal:8010", "API_UPSTREAM_PARAMS": ""},
         "server host.docker.internal:8010 ;"),
    ):
        rendered = _render_nginx_template(values)
        assert server in rendered
        assert "${" not in rendered
        for nginx_variable in ("$http_origin", "$remote_addr", "$rate_limit_key", "$uri"):
            assert nginx_variable in rendered


# OPS-07: Settings fields the compose services deliberately do not pass.
SETTINGS_NOT_PASSED_TO_CONTAINERS = {
    # The containers never run with auth bypass (ENV=production refuses it
    # anyway); without the confirmation flag AUTH_BYPASS=true fails at start.
    "AUTH_BYPASS_CONFIRM_RISK": "auth bypass is local-dev only",
}
# OPS-07: compose defaults that differ from api/config.py on purpose.
COMPOSE_DEFAULT_OVERRIDES = {
    ("*", "ENV"): "production",
    ("api", "KUFAR_PARALLEL_SEMAPHORE"): "3",
}


def _settings_env_names() -> dict[str, object]:
    from api.config import Settings

    return {
        (field.alias or name).upper(): field.default
        for name, field in Settings.model_fields.items()
    }


def _compose_default_matches(value: str, default: object) -> bool:
    if default is None:
        return value == ""
    if isinstance(default, bool):
        return value.lower() == str(default).lower()
    if isinstance(default, int | float):
        return float(value) == float(default)
    return value == str(default)


def test_compose_passes_every_settings_field_to_the_api() -> None:
    """OPS-07: the containers do not read .env (it never enters the image),
    so a Settings field missing from the compose environment silently kept
    its code default: the strict security flags, DB pool sizing, AI limits and
    the image-proxy knobs could not be set for the stack at all. A new
    Settings field has to be passed or allowlisted with a reason here."""
    settings_fields = _settings_env_names()
    services = _services()
    api_env = services["api"]["environment"]
    missing = set(settings_fields) - set(api_env) - set(SETTINGS_NOT_PASSED_TO_CONTAINERS)
    assert not missing, f"Settings fields not passed to the api container: {sorted(missing)}"
    assert set(SETTINGS_NOT_PASSED_TO_CONTAINERS) <= set(settings_fields)
    assert not set(SETTINGS_NOT_PASSED_TO_CONTAINERS) & set(api_env)
    # Not Settings fields, but the image CMD (WORKERS, LOG_LEVEL) and
    # api/logging_config.py (LOG_LEVEL, LOG_FORMAT) read them.
    assert {"WORKERS", "LOG_LEVEL", "LOG_FORMAT"} <= set(api_env)
    # The bot and the scheduler get the settings they read.
    assert {"INTERNAL_SERVICE_TOKEN", "INTERNAL_SERVICE_TOKEN_REQUIRED",
            "ALLOW_LEGACY_BOT_INITDATA"} <= set(services["bot"]["environment"])
    assert {"KUFAR_REQUEST_DELAY", "KUFAR_PARALLEL_SEMAPHORE", "AUTO_REMOVE_MISSING_DAYS",
            "ALERT_CHECK_INTERVAL"} <= set(services["scheduler"]["environment"])
    for name in PYTHON_SERVICES:
        assert services[name]["environment"]["BOT_TOKEN"].startswith("${BOT_TOKEN:?"), name


def test_compose_defaults_match_the_settings_defaults() -> None:
    """OPS-07: passing a field must not change behaviour while the variable
    is unset, so every ``${NAME:-default}`` equals the api/config.py default
    (blank stands for None) unless COMPOSE_DEFAULT_OVERRIDES says otherwise."""
    from api.config import Settings

    settings_fields = _settings_env_names()
    required = {
        (field.alias or name).upper()
        for name, field in Settings.model_fields.items()
        if field.is_required()
    }
    mismatches = []
    for service in PYTHON_SERVICES:
        for name, value in _services()[service]["environment"].items():
            if name not in settings_fields or name in required:
                continue
            match = re.fullmatch(r"\$\{(\w+):-(.*)\}", str(value))
            if match is None:
                continue
            assert match.group(1) == name, (service, name, value)
            expected = COMPOSE_DEFAULT_OVERRIDES.get(
                (service, name), COMPOSE_DEFAULT_OVERRIDES.get(("*", name)),
            )
            if expected is not None:
                ok = match.group(2) == expected
            else:
                ok = _compose_default_matches(match.group(2), settings_fields[name])
            if not ok:
                mismatches.append(f"{service}.{name}={value} (Settings: {settings_fields[name]})")
    assert not mismatches, mismatches
