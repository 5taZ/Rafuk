from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

FRONTEND = Path("frontend")
HTML_FILE = FRONTEND / "index.html"
CSS_FILE = FRONTEND / "css" / "style.css"
CSS_PARTS_DIR = FRONTEND / "css" / "parts"
JS_DIR = FRONTEND / "js"


@pytest.fixture(scope="module")
def soup() -> BeautifulSoup:
    assert HTML_FILE.exists()
    return BeautifulSoup(HTML_FILE.read_text(encoding="utf-8"), "html.parser")


@pytest.fixture(scope="module")
def css_text() -> str:
    """Concatenated CSS — style.css is now a thin @import loader.

    Tests assert against rules that may live in any partial under
    parts/, so we inline them here. Order follows the @import order
    in style.css to keep the cascade representation accurate.
    """
    assert CSS_FILE.exists()
    chunks = [CSS_FILE.read_text(encoding="utf-8")]
    if CSS_PARTS_DIR.is_dir():
        # Match the @import order in style.css.
        for name in ("tokens", "layout", "modals", "pipeline", "states", "ai", "brand"):
            partial = CSS_PARTS_DIR / f"{name}.css"
            if partial.exists():
                chunks.append(partial.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_html_has_doctype() -> None:
    raw = HTML_FILE.read_text(encoding="utf-8")
    assert raw.strip().lower().startswith("<!doctype html")


def test_html_has_viewport_meta(soup: BeautifulSoup) -> None:
    meta = soup.find("meta", attrs={"name": "viewport"})
    assert meta is not None
    assert "width=device-width" in (meta.get("content") or "")


def test_html_loads_required_scripts(soup: BeautifulSoup) -> None:
    scripts = [script.get("src", "") for script in soup.find_all("script")]
    assert any("telegram-web-app.js" in script for script in scripts)
    assert any("app_bundle.js" in script for script in scripts)
    assert len([script for script in scripts if script.startswith("js/")]) == 1
    bundle = (FRONTEND / "js" / "app_bundle.js").read_text(encoding="utf-8")
    assert "function analyticsApp" in bundle
    assert "function createVirtualList" in bundle
    # Chart.js is now lazy-loaded by render_charts.js on first paint —
    # keep the loader file in the bundle but make sure no <script> tag
    # blocks the initial document on the chart library directly.
    assert "ensureChartLib" in bundle
    assert not any(
        "chart.umd" in script or "chart.min.js" in script for script in scripts
    ), "Chart.js must be lazy-loaded, not preloaded by <script> tag"


def test_app_bundle_uses_single_namespace_wrapper() -> None:
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    assert bundle.startswith('(function (window) {\n"use strict";\nwindow.App = window.App || {};')
    assert "window.App = Object.assign(window.App || {}, {" in bundle
    for name in (
        "analyticsApp",
        "createAppCore",
        "domEl",
        "openModalAnimated",
        "closeModalAnimated",
    ):
        assert f"  {name}," in bundle
    assert "window.analyticsApp" not in bundle
    assert "window.createAppCore" not in bundle


def test_lazy_ai_modules_register_on_app_namespace() -> None:
    registrations = {
        "api_ai_modal.js": "createAiModal",
        "api_ai_render.js": "createAiRender",
        "api_ai_pdf.js": "createAiPdf",
        "api_ai.js": "createApiAi",
        "api_listing_assistant.js": "createApiListingAssistant",
    }
    for module, factory in registrations.items():
        text = (JS_DIR / module).read_text(encoding="utf-8")
        assert "(function (app) {" in text
        assert f"app.{factory} = {factory};" in text
        assert "})(window.App = window.App || {});" in text

    actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    assert "app.createApiAi(context)" in actions
    assert "createApiAi(context)" not in actions.replace("app.createApiAi(context)", "")


def test_lazy_script_cache_busters_match_main_bundle(soup: BeautifulSoup) -> None:
    scripts = [script.get("src", "") for script in soup.find_all("script")]
    app_bundle_src = next(script for script in scripts if "app_bundle.js" in script)
    version = app_bundle_src.split("?v=", 1)[1]
    actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    for module in (
        "api_ai_modal.js",
        "api_ai_render.js",
        "api_ai_pdf.js",
        "api_ai.js",
        "api_listing_assistant.js",
    ):
        assert f"js/{module}?v={version}" in actions


def test_chart_js_loads_lazily_only_on_first_chart_paint() -> None:
    """Chart.js is ~80 KB gzipped — users who only use watchlist /
    trackers / leads should never download it. render_charts.js must
    fetch it on demand the first time renderChart / renderHistoryChart
    needs to paint, and cache the resulting promise so a second chart
    on the same page doesn't trigger a second download."""
    text = (Path("frontend/js/render_charts.js")).read_text(encoding="utf-8")
    assert "ensureChartLib" in text
    assert "_chartLibPromise" in text
    assert "chart.umd" in text  # the URL the loader fetches
    # Both renderers must guard on window.Chart being available.
    assert text.count('typeof window.Chart !== "function"') >= 2


def test_html_has_stats_and_listings(soup: BeautifulSoup) -> None:
    canvases = soup.find_all("canvas")
    assert len(canvases) >= 2
    assert soup.find(attrs={"data-view": "deals"}) is not None


def test_css_has_required_building_blocks(css_text: str) -> None:
    assert ":root" in css_text
    # Dark defaults are in :root; light theme overrides in [data-theme="light"]
    assert '[data-theme="light"]' in css_text
    assert "@keyframes skeleton-shimmer" in css_text
    assert "@media" in css_text


def test_css_uses_color_tokens_outside_theme_blocks(css_text: str) -> None:
    """No hard-coded hex colour outside the :root / [data-theme] blocks.

    The codebase used to scatter `var(--red, #e11d48)` fallbacks and
    raw `#16a34a` greens in dozens of rules; that broke theme switching
    because the literals didn't shift with the dark/light palette.
    Lock that down: the palette lives in the theme blocks, every other
    rule must reach for a CSS variable."""
    import re as _re

    lines = css_text.split("\n")
    in_theme_block = False
    depth = 0
    offenders: list[tuple[int, str]] = []

    for i, line in enumerate(lines, 1):
        if ":root" in line or "[data-theme=" in line:
            in_theme_block = True
        if in_theme_block and "{" in line:
            depth += line.count("{")
        if in_theme_block:
            if "}" in line:
                depth -= line.count("}")
                if depth <= 0:
                    in_theme_block = False
                    depth = 0
            continue
        for match in _re.findall(r"#[0-9a-fA-F]{6}\b", line):
            if match.lower() in {"#ffffff", "#000000"}:
                continue
            if "var(" in line:
                continue
            offenders.append((i, line.strip()[:100]))
            break

    assert not offenders, "Hex literals outside theme blocks: " + "; ".join(
        f"L{i}: {snippet}" for i, snippet in offenders
    )


def test_css_defines_motion_tokens(css_text: str) -> None:
    """Standard transition durations + easing live as variables so future
    components don't reinvent the timing every time."""
    assert "--t-fast:" in css_text
    assert "--t-base:" in css_text
    assert "--t-slow:" in css_text
    assert "--easing-standard:" in css_text


def test_large_lists_do_not_use_noisy_live_regions(soup: BeautifulSoup) -> None:
    for element_id in (
        "listings-list",
        "trackers-list",
        "tracker-events-list",
        "history-deals-list",
    ):
        element = soup.find(id=element_id)
        assert element is not None
        assert element.get("aria-live") is None


def test_small_action_targets_keep_44px_minimum(css_text: str) -> None:
    assert ".summary-refinement-chip" in css_text
    assert ".empty-state-action" in css_text
    assert ".list-pagination-button" in css_text
    for selector in (
        ".summary-refinement-chip",
        ".empty-state-action",
        ".list-pagination-button",
    ):
        start = css_text.index(selector)
        block = css_text[start:css_text.index("}", start)]
        assert "min-height: 44px" in block
        assert "min-width: 44px" in block


def test_service_worker_present_with_safe_strategies() -> None:
    """The Mini App ships a service worker so the SPA shell + read-only
    API responses survive flaky networks. Make sure the worker:
      * is at /sw.js so it can claim the / scope without server config
        gymnastics,
      * versions its caches (CACHE_VERSION) so a deploy can invalidate
        old buckets in activate(),
      * never caches mutations (POST/PATCH/DELETE) — those need to hit
        the server,
      * never caches AI endpoints — results are tied to a specific
        ad/query and shouldn't bleed across users,
      * registers from app.js."""
    sw = (FRONTEND / "sw.js")
    assert sw.exists(), "frontend/sw.js missing"
    text = sw.read_text(encoding="utf-8")
    assert "CACHE_VERSION" in text
    assert "skipWaiting" in text
    assert "clients.claim" in text
    # Mutations must bypass.
    assert "request.method !== \"GET\"" in text
    # AI endpoints must bypass.
    assert "/api/v1/ai/" in text
    # Registration in app.js, gated on https / localhost.
    app_js = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
    assert 'serviceWorker.register("/sw.js"' in app_js or \
        "serviceWorker\n                .register(\"/sw.js\"" in app_js
    assert "https:" in app_js


def test_nginx_serves_sw_js_without_long_cache() -> None:
    """A 7-day Cache-Control on /sw.js would pin users to the old SW
    for a week. Ensure the nginx config has an explicit no-cache
    rule for the worker file plus the Service-Worker-Allowed header
    that lets it claim the / scope."""
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "location = /sw.js" in nginx_conf
    assert "no-cache" in nginx_conf
    assert "Service-Worker-Allowed" in nginx_conf
