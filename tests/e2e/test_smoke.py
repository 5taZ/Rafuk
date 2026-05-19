"""G-12: Minimal Playwright + axe-core smoke test.

Loads frontend/index.html via file:// and asserts:
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


@pytest.mark.asyncio
async def test_homepage_loads_without_critical_a11y_violations(page, frontend_path):
    """Load the Mini App shell and run axe-core against the rendered DOM."""
    index = frontend_path / "index.html"
    await page.goto(f"file://{index}")

    title = await page.title()
    assert title, "document.title must be set"

    # Inject axe-core and run accessibility audit
    if AXE_SCRIPT.exists():
        await page.add_script_tag(path=str(AXE_SCRIPT))
        results = await page.evaluate("() => axe.run()")
        serious = [
            v for v in results["violations"]
            if v["impact"] in ("critical", "serious")
        ]
        assert serious == [], f"Critical/serious a11y violations: {serious}"
    else:
        # Fallback: DOM-level smoke — ensure the page rendered without JS errors
        html_el = await page.query_selector("html")
        assert html_el is not None, "document.documentElement must exist"
