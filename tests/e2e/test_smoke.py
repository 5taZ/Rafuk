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
from playwright.sync_api import expect

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


def test_mobile_search_clear_removes_query_and_category_picker(new_context, frontend_url):
    page = new_context(
        viewport={"width": 320, "height": 640}, is_mobile=True, has_touch=True,
        service_workers="block",
    ).new_page()
    page.route("**/api/v1/**", lambda route: route.fulfill(json={"items": [], "listings": []}))
    page.route("**/api/v1/price-stats?*", lambda route: route.fulfill(json={
        "count": 2,
        "total_results": 2,
        "categories": [
            {"id": 1, "label": "Мобильные телефоны", "count": 1},
            {"id": 2, "label": "Аксессуары", "count": 1},
        ],
    }))
    page.goto(frontend_url)

    search_input = page.get_by_role("searchbox")
    clear_button = page.get_by_role("button", name="Очистить запрос")
    category_picker = page.locator("#category-picker")
    expect(clear_button).to_be_hidden()
    search_input.fill("iPhone 18 Pro")
    page.get_by_role("button", name="Найти", exact=True).tap()
    expect(category_picker).to_be_visible()
    expect(clear_button).to_be_visible()

    clear_button.tap()
    expect(search_input).to_have_value("")
    expect(search_input).to_be_focused()
    expect(clear_button).to_be_hidden()
    expect(category_picker).to_be_hidden()
