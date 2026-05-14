from __future__ import annotations

import subprocess
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_ci_push_trigger_includes_active_bad_app_branch() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "branches: [main, develop, bad-app]" in workflow


def test_readme_points_to_live_verification_and_existing_wave_plan() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "uv run pytest --tb=short -q" in readme
    assert "uv run ruff check ." in readme
    assert "konechno.md" in readme
    assert "CHANGELOG.md" not in readme


def test_env_example_matches_local_redis_policy() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert "REDIS_URL=redis://localhost:6380/0" in env_example
    assert "REDIS_URL=redis://:<redis-password>@localhost:6379/0" not in env_example
    assert "DOCKER_REDIS_URL=redis://:<redis-password>@redis:6379/0" in env_example


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
