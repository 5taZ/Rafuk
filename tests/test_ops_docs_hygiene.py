from __future__ import annotations

from pathlib import Path


def test_ci_push_trigger_includes_active_bad_app_branch() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "branches: [main, develop, bad-app]" in workflow


def test_readme_test_count_is_current() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "792 tests" in readme
    assert "791 tests" not in readme
    assert "725 tests" not in readme
    assert "717 tests" not in readme
    assert "716 tests" not in readme
    assert "715 tests" not in readme
    assert "714 tests" not in readme
    assert "661 tests" not in readme
    assert "494 tests" not in readme


def test_stop_local_preserves_database_volume() -> None:
    stop_local = Path("stop-local.sh").read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=\"${COMPOSE_PROJECT_NAME:-myprojetctkufar}\"" in stop_local
    assert "docker compose --profile local-db stop frontend redis postgres" in stop_local
    assert "down -v" not in stop_local
