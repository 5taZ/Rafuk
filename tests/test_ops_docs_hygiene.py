from __future__ import annotations

from pathlib import Path


def test_ci_push_trigger_includes_active_bad_app_branch() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "branches: [main, develop, bad-app]" in workflow


def test_readme_test_count_is_current() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "714 tests" in readme
    assert "661 tests" not in readme
    assert "494 tests" not in readme
