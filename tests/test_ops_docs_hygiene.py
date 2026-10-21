from __future__ import annotations

import re
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory


def _workflow() -> dict:
    return yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_ci_push_trigger_includes_active_bad_app_branch() -> None:
    """RV-OPS-09: this test used to pin ``[main, develop, bad-app]``, but the
    integration branch is ``dev`` (``develop`` does not exist), and pull
    requests into ``bad-app`` were not checked at all."""
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads `on` as True
    push_branches = triggers["push"]["branches"]
    assert {"main", "dev", "bad-app"} <= set(push_branches)
    assert "develop" not in push_branches
    assert {"main", "bad-app"} <= set(triggers["pull_request"]["branches"])


def test_ci_uses_the_lockfile_as_is() -> None:
    """RV-OPS-06: without --locked, `uv sync` / `uv export` silently
    re-resolved a stale uv.lock, so CI tested and audited something else
    than what the image installs."""
    jobs = _workflow()["jobs"]
    commands = [
        line.strip()
        for job in jobs.values()
        for step in job["steps"]
        for line in step.get("run", "").splitlines()
        if re.search(r"\buv (?:sync|export)\b", line)
    ]
    assert len(commands) >= 4
    assert all("--locked" in command for command in commands), commands
    assert "uv lock --check" in [step.get("run", "") for step in jobs["lint"]["steps"]]


def test_ci_actions_are_pinned_to_commit_shas() -> None:
    """SEC-HIGH (issues §1.8) pins every action to a 40-char SHA.
    RV-OPS-09: setup-uv and codecov carried the SHAs of annotated tag
    objects, which are not commits and do not resolve reliably."""
    uses = [
        step["uses"]
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
    tag_object_shas = (
        "8d55fbecc275b1c35dbe060458839f8d30439ccf",  # astral-sh/setup-uv tag v3
        "0f8570b1a125f4937846a11fcfa3bcd548bd8c97",  # codecov/codecov-action tag v4.6.0
    )
    assert not any(ref.endswith(tag_object_shas) for ref in uses)


def test_ci_compose_smoke_runs_the_production_stack() -> None:
    """RV-OPS-09: OPS-01 and OPS-02 survived for months because no job built
    Dockerfile.frontend or started the compose stack. compose-smoke does
    both on every push and PR, and the image publish waits for it."""
    jobs = _workflow()["jobs"]
    smoke = jobs["compose-smoke"]
    assert "if" not in smoke
    assert smoke["env"]["COMPOSE_FILE"] == "docker-compose.yml"
    assert smoke["env"]["COMPOSE_PROFILES"] == "local-db"
    script = "\n".join(step.get("run", "") for step in smoke["steps"])
    assert "docker compose config --quiet" in script
    assert "docker compose build" in script
    assert "docker compose up -d" in script
    assert "http://127.0.0.1:8081/api/v1/health/ready" in script
    assert "docker compose down -v" in script
    assert "compose-smoke" in jobs["docker-build"]["needs"]


def test_readme_production_command_runs_the_bundled_database() -> None:
    """RV-OPS-02: the containers reach the database as postgres:5432, so the
    documented full-stack command must start it (local-db profile)."""
    readme = Path("README.md").read_text(encoding="utf-8")
    assert (
        "docker compose -f docker-compose.yml --profile local-db --profile tunnel up -d --build"
        in readme
    )


def test_migrations_env_escapes_percent_in_database_url(monkeypatch) -> None:
    """RV-DB-07: Alembic's Config is a ConfigParser, so a percent-encoded
    password in DATABASE_URL raised "invalid interpolation syntax" in
    migrations/env.py and the migrate service never let api start."""
    import alembic

    import api.config

    url = "postgresql+asyncpg://kufar:p%40ss%25w0rd@postgres:5432/kufar"
    config = Config()
    fake_context = mock.MagicMock(config=config)
    fake_context.is_offline_mode.return_value = True
    monkeypatch.setattr(alembic, "context", fake_context)
    monkeypatch.setattr(api.config, "get_settings", lambda: SimpleNamespace(database_url=url))

    runpy.run_path("migrations/env.py")

    assert config.get_main_option("sqlalchemy.url") == url
    assert fake_context.configure.call_args.kwargs["url"] == url


def test_readme_points_to_live_verification_and_existing_wave_plan() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "uv run pytest --tb=short -q" in readme
    assert "uv run ruff check ." in readme
    assert "konechno.md" in readme
    assert "CHANGELOG.md" not in readme


def test_env_example_matches_local_redis_policy() -> None:
    """RV-OPS-02 / RV-OPS-04: this test used to pin a host-mode REDIS_URL
    without a password, although compose always starts Redis with
    --requirepass (the host processes got NOAUTH), and the example pointed
    DOCKER_DATABASE_URL at host.docker.internal:5433, which a Linux Docker
    Engine cannot reach."""
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert "\nREDIS_URL=redis://:<redis-password>@localhost:6380/0\n" in env_example
    assert "REDIS_URL=redis://localhost:6380/0" not in env_example
    assert "REDIS_URL=redis://:<redis-password>@localhost:6379/0" not in env_example
    assert "DOCKER_REDIS_URL=redis://:<redis-password>@redis:6379/0" in env_example
    assert (
        "DOCKER_DATABASE_URL=postgresql+asyncpg://kufar:<postgres-password>@postgres:5432/kufar"
        in env_example
    )
    # No variable, active or commented out, routes through the host gateway.
    assert not re.search(
        r"^#?\s*[A-Z_]+=\S*host\.docker\.internal", env_example, re.MULTILINE,
    )


def test_backup_runbook_expected_head_matches_alembic_head() -> None:
    cfg = Config("migrations/alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    runbook = Path("docs/BACKUP_RUNBOOK.md").read_text(encoding="utf-8")
    assert f"Expected Alembic head: `{heads[0]}`." in runbook


def test_partition_snapshots_script_keeps_cleanup_index() -> None:
    partition_script = Path("scripts/partition_snapshots.sql").read_text(encoding="utf-8")
    assert "idx_query_snapshots_snapshot_at" in partition_script
    assert "ON query_snapshots(snapshot_at)" in partition_script


def test_partition_snapshots_script_uses_numeric_money_columns() -> None:
    partition_script = Path("scripts/partition_snapshots.sql").read_text(encoding="utf-8")
    assert partition_script.count("NUMERIC(12, 2) NOT NULL DEFAULT 0.0") == 4
    assert "FLOAT NOT NULL DEFAULT 0.0" not in partition_script


def test_nginx_ai_budget_comment_matches_fastapi_guards() -> None:
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "_check_rate_limit" in nginx_conf
    assert "per-route @limiter.limit decorators" not in nginx_conf


def test_static_version_bump_updates_service_worker_cache(tmp_path: Path) -> None:
    work = tmp_path
    (work / "scripts").mkdir()
    (work / "frontend" / "js").mkdir(parents=True)
    (work / "frontend").mkdir(exist_ok=True)
    (work / "scripts" / "bump_static_version.sh").write_text(
        Path("scripts/bump_static_version.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (work / "frontend" / "index.html").write_text(
        '<script defer src="js/app_bundle.js?v=old-tag"></script>',
        encoding="utf-8",
    )
    (work / "frontend" / "js" / "app_actions.js").write_text(
        'import("js/api_ai.js?v=old-tag");',
        encoding="utf-8",
    )
    (work / "frontend" / "sw.js").write_text(
        'const CACHE_VERSION = "rafuk-cache-v9";',
        encoding="utf-8",
    )

    subprocess.run(
        ["bash", "scripts/bump_static_version.sh", "new-tag"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "?v=new-tag" in (work / "frontend" / "index.html").read_text(encoding="utf-8")
    actions = (work / "frontend" / "js" / "app_actions.js").read_text(encoding="utf-8")
    assert "?v=new-tag" in actions
    assert 'const CACHE_VERSION = "rafuk-cache-new-tag";' in (
        work / "frontend" / "sw.js"
    ).read_text(encoding="utf-8")


def test_stop_local_preserves_database_volume() -> None:
    stop_local = Path("stop-local.sh").read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=\"${COMPOSE_PROJECT_NAME:-myprojetctkufar}\"" in stop_local
    assert "docker compose --profile local-db stop frontend redis postgres" in stop_local
    assert "down -v" not in stop_local
