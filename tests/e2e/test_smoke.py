"""G-12: Minimal Playwright + axe-core smoke test.

Serves frontend/ over http://127.0.0.1 and asserts:
  1. The page renders (document.title is set).
  2. No critical/serious axe-core a11y violations on the initial DOM.

Gated by KUFAR_E2E=1 — skipped in the default pytest run.
"""
from __future__ import annotations

import os
import pathlib

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("KUFAR_E2E") != "1",
    reason="E2E disabled by default; set KUFAR_E2E=1 to run",
)

AXE_SCRIPT = pathlib.Path(__file__).parent / "vendor" / "axe.min.js"


def test_homepage_loads_without_critical_a11y_violations(page, frontend_url):
    """Load the Mini App shell and run axe-core against the rendered DOM.

    OPS-12: pytest-playwright's ``page`` fixture is the sync API; the old
    ``async def`` awaited its methods and could never pass. axe-core is
    required: a missing download used to turn the test into a bare
    title check that always passed."""
    page.goto(f"{frontend_url}/index.html")

    assert page.title(), "document.title must be set"

    assert AXE_SCRIPT.exists(), (
        f"{AXE_SCRIPT} is missing; download the release pinned in the "
        "e2e-smoke job of .github/workflows/ci.yml"
    )
    page.add_script_tag(path=str(AXE_SCRIPT))
    results = page.evaluate("() => axe.run()")
    serious = [
        v for v in results["violations"]
        if v["impact"] in ("critical", "serious")
    ]
    assert serious == [], f"Critical/serious a11y violations: {serious}"
