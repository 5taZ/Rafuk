from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

FRONTEND = Path("frontend")
HTML_FILE = FRONTEND / "index.html"
CSS_FILE = FRONTEND / "css" / "style.css"
CSS_PARTS_DIR = FRONTEND / "css" / "parts"
JS_DIR = FRONTEND / "js"


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _frontend_bundle_modules() -> list[str]:
    script = Path("scripts/build_frontend_bundle.sh").read_text(encoding="utf-8")
    block = script.split("modules=(", 1)[1].split(")", 1)[0]
    modules: list[str] = []
    for line in block.splitlines():
        module = line.strip().strip("\"'")
        if module and not module.startswith("#"):
            modules.append(module)
    return modules


def _expected_app_bundle_text() -> str:
    chunks = [
        "(function (window) {\n",
        '"use strict";\n',
        "window.App = window.App || {};\n",
        "\n",
    ]
    for module in _frontend_bundle_modules():
        chunks.append("\n;\n")
        chunks.append((JS_DIR / module).read_text(encoding="utf-8"))
        chunks.append("\n")
    chunks.append(
        "\n"
        "window.App = Object.assign(window.App || {}, {\n"
        "  analyticsApp,\n"
        "  createAppCore,\n"
        "  domEl,\n"
        "  domFragment,\n"
        "  bindRovingTablist,\n"
        "  _prefersReducedMotion,\n"
        "  openModalAnimated,\n"
        "  closeModalAnimated,\n"
        "});\n"
        "})(window);\n"
    )
    return "".join(chunks)


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


def test_frontend_csp_does_not_allow_inline_styles() -> None:
    index_text = HTML_FILE.read_text(encoding="utf-8")
    offline_text = (FRONTEND / "offline.html").read_text(encoding="utf-8")
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    combined = "\n".join([index_text, offline_text, nginx_conf])
    assert "style-src 'unsafe-inline'" not in combined
    assert "style-src 'self';" in index_text
    assert "style-src 'self';" in nginx_conf
    assert "fonts.googleapis.com" not in index_text
    assert "fonts.gstatic.com" not in index_text
    assert "<style" not in offline_text
    assert 'href="/offline.css"' in offline_text


def test_service_worker_precaches_offline_stylesheet() -> None:
    sw = (FRONTEND / "sw.js").read_text(encoding="utf-8")
    assert '"/offline.css"' in sw
    assert "OFFLINE_FALLBACK_ASSETS" in sw
    assert "cache.addAll(OFFLINE_FALLBACK_ASSETS)" in sw


def test_app_bundle_uses_single_namespace_wrapper() -> None:
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    assert bundle.startswith('(function (window) {\n"use strict";\nwindow.App = window.App || {};')
    assert "window.App = Object.assign(window.App || {}, {" in bundle
    for name in (
        "analyticsApp",
        "createAppCore",
        "domEl",
        "domFragment",
        "_prefersReducedMotion",
        "bindRovingTablist",
        "openModalAnimated",
        "closeModalAnimated",
    ):
        assert f"  {name}," in bundle
    assert "window.analyticsApp" not in bundle
    assert "window.createAppCore" not in bundle


def test_app_bundle_matches_build_script_sources() -> None:
    actual = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    expected = _expected_app_bundle_text()
    assert _text_sha256(actual) == _text_sha256(expected), (
        "frontend/js/app_bundle.js is stale; run scripts/build_frontend_bundle.sh"
    )


def test_css_bundle_matches_parts_sources() -> None:
    from scripts.rebuild_css import build_css_text

    actual = CSS_FILE.read_text(encoding="utf-8")
    expected = build_css_text()
    assert _text_sha256(actual) == _text_sha256(expected), (
        "frontend/css/style.css is stale; run uv run python scripts/rebuild_css.py"
    )


def test_lazy_ai_modules_register_on_app_namespace() -> None:
    registrations = {
        "api_ai_modal.js": "createAiModal",
        "api_ai_render.js": "createAiRender",
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


def test_design_context_matches_loaded_font_stack(soup: BeautifulSoup) -> None:
    context_text = Path(".impeccable.md").read_text(encoding="utf-8")
    font_css = (FRONTEND / "vendor" / "fonts" / "fonts.css").read_text(encoding="utf-8")
    font_links = " ".join(link.get("href", "") for link in soup.find_all("link"))
    assert "vendor/fonts/fonts.css" in font_links
    assert "font-family: 'Geist'" in font_css
    assert "font-family: 'JetBrains Mono'" in font_css
    assert "Geist (sans-serif) + JetBrains Mono" in context_text
    assert "Rubik (sans-serif)" not in context_text


def test_overview_search_loading_state_is_wired(soup: BeautifulSoup, css_text: str) -> None:
    loader = soup.find(id="overview-loading")
    assert loader is not None
    assert loader.get("role") == "status"
    assert loader.has_attr("hidden")
    assert soup.find(id="overview-loading-query") is not None
    assert ".overview-loading-grid" in css_text
    assert ".overview-skeleton-card" in css_text
    assert "@keyframes overview-loader-orbit" not in css_text
    dom = (JS_DIR / "app_core_dom.js").read_text(encoding="utf-8")
    render_core = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    assert 'getElementById("overview-loading")' in dom
    assert "overviewLoading.hidden = !showOverviewLoading" in render_core
    assert "overviewLoadingQuery.textContent" in render_core


def test_lazy_script_cache_busters_match_main_bundle(soup: BeautifulSoup) -> None:
    scripts = [script.get("src", "") for script in soup.find_all("script")]
    app_bundle_src = next(script for script in scripts if "app_bundle.js" in script)
    version = app_bundle_src.split("?v=", 1)[1]
    actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    for module in (
        "api_ai_modal.js",
        "api_ai_render.js",
        "api_ai.js",
        "api_listing_assistant.js",
    ):
        assert f"js/{module}?v={version}" in actions


def test_ai_analysis_export_ui_is_removed() -> None:
    index_text = HTML_FILE.read_text(encoding="utf-8")
    actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    ai_js = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")
    assert "ai-export-pdf" not in index_text
    assert "api_ai_pdf.js" not in actions
    assert "createAiPdf" not in ai_js


def test_frontend_composition_clones_mutable_contexts() -> None:
    app = (JS_DIR / "app.js").read_text(encoding="utf-8")
    renderers = (JS_DIR / "app_renderers.js").read_text(encoding="utf-8")
    actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    assert "const actionRegistry = {}" in app
    assert "actions: actionRegistry" in app
    assert "Object.assign(actionRegistry, actions)" in app
    assert "function createAppRenderers(baseContext)" in renderers
    assert "function createAppActions(baseContext)" in actions
    assert "const context = { ...baseContext };" in renderers
    assert "const context = { ...baseContext };" in actions


def test_chart_js_loads_lazily_only_on_first_chart_paint() -> None:
    """Chart.js is ~80 KB gzipped — users who only use watchlist /
    trackers / leads should never download it. render_charts.js must
    fetch it on demand the first time renderChart / renderHistoryChart
    needs to paint, and cache the resulting promise so a second chart
    on the same page doesn't trigger a second download."""
    text = (Path("frontend/js/render_charts.js")).read_text(encoding="utf-8")
    assert "ensureChartLib" in text
    assert "_chartLibPromise" in text
    assert "js/vendor/chart.umd.min.js" in text
    # Both renderers must guard on window.Chart being available.
    assert text.count('typeof window.Chart !== "function"') >= 2


def test_chart_js_is_vendored_without_unconditional_preload(soup: BeautifulSoup) -> None:
    """Chart.js stays lazy-loaded from same-origin vendor assets."""
    chart_url = "js/vendor/chart.umd.min.js"
    preconnect = soup.find("link", attrs={"rel": "preconnect", "href": "https://cdn.jsdelivr.net"})
    assert preconnect is None
    preload = soup.find("link", attrs={"rel": "preload", "as": "script", "href": chart_url})
    assert preload is None
    render_charts = (JS_DIR / "render_charts.js").read_text(encoding="utf-8")
    assert chart_url in render_charts
    assert (
        "sha384-vsrfeLOOY6KuIYKDlmVH5UiBmgIdB1oEf7p01YgWHuqmOHfZr374+odEv96n9tNC"
    ) in render_charts
    index_text = HTML_FILE.read_text(encoding="utf-8")
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in index_text
    assert "cdn.jsdelivr.net" not in nginx_conf
    assert "connect-src 'self';" in index_text
    assert "connect-src 'self';" in nginx_conf


def test_skip_link_targets_populated_main_landmark(soup: BeautifulSoup) -> None:
    skip_link = soup.find("a", class_="skip-link")
    assert skip_link is not None
    assert skip_link.get("href") == "#main-content"
    main = soup.find("main", id="main-content")
    assert main is not None
    assert main.get("tabindex") == "-1"
    assert main.find(id="search-input") is not None
    assert main.find(id="overview-view") is not None
    assert main.get_text(" ", strip=True)


def test_ai_loading_states_use_minimal_status_visuals(soup: BeautifulSoup, css_text: str) -> None:
    loader = soup.find(id="ai-modal-loading")
    assert loader is not None
    assert loader.select(".ai-scan-chip") == []
    assert loader.select_one(".ai-loader-icon").get_text(strip=True) == "AI"

    ai_js = (JS_DIR / "api_ai_modal.js").read_text(encoding="utf-8")
    ai_render_js = (JS_DIR / "api_ai_render.js").read_text(encoding="utf-8")
    la_js = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")
    assert "const { domEl, domFragment } = app;" in ai_render_js
    assert 'text: "i"' in ai_render_js
    assert "_formatAiWarning" in ai_render_js
    assert "AI сейчас на лимите" in ai_render_js
    assert "la-competitor-thumb" in css_text
    assert "la-competitor-row" in css_text
    warning_start = css_text.index(".ai-warning-banner")
    warning_end = css_text.index("/* ── AI: Light theme adjustments ── */")
    warning_css = css_text[warning_start:warning_end]
    assert "var(--amber-soft)" not in warning_css
    assert "prefersReducedMotion()" in ai_js
    assert "prefersReducedMotion()" in la_js
    assert "_prefersReducedMotion()" not in ai_js
    assert "_prefersReducedMotion()" not in la_js
    assert "Сверяю похожие лоты" in ai_js
    assert "Оцениваю риски сделки" in ai_js
    assert "Сверяю похожие лоты" in la_js
    assert "la-scan-chips" not in la_js
    assert "ai-scan-chip" not in la_js

    radar_block = css_text[css_text.index(".ai-modal-loading"):css_text.index(".ai-time-notice")]
    assert "@keyframes aiRadarSweep" not in css_text
    assert "conic-gradient(from -90deg" not in radar_block
    assert "border-top-color: color-mix" in radar_block
    assert ".ai-scan-chips" in css_text
    assert ".ai-scan-chip {" not in css_text
    assert "@media (prefers-reduced-motion: reduce)" in css_text


def test_ai_loading_motion_avoids_layout_property_animation(css_text: str) -> None:
    ai_js = (JS_DIR / "api_ai_modal.js").read_text(encoding="utf-8")
    la_js = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")
    assert "barEl.style.transform = `scaleX(${_aiProgress / 100})`" in ai_js
    assert "barEl.style.transform = `scaleX(${_laProgress / 100})`" in la_js
    assert "barEl.style.width = _aiProgress" not in ai_js
    assert "barEl.style.width = _laProgress" not in la_js

    progress_start = css_text.index(".ai-progress-bar,\n.la-progress-bar")
    progress_end = css_text.index(".ai-progress-bar::after", progress_start)
    progress_css = css_text[progress_start:progress_end]
    assert "width: 100%" in progress_css
    assert "transform: scaleX(0.05)" in progress_css
    assert "transform-origin: left center" in progress_css
    assert "transition: transform 420ms" in progress_css
    assert "transition: width" not in progress_css

    recent_start = css_text.index(".recent-strip {")
    recent_end = css_text.index(".recent-kicker", recent_start)
    recent_css = css_text[recent_start:recent_end]
    assert "transition: opacity 0.15s ease-out, transform 0.2s ease-out" in recent_css
    assert "max-height 0.2s" not in recent_css
    assert "transform: translateY(-4px)" in recent_css


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


def test_secondary_tablists_have_roving_aria_relationships(soup: BeautifulSoup) -> None:
    for selector in (".items-tabs", ".la-tabs"):
        tablist = soup.select_one(selector)
        assert tablist is not None
        assert tablist.get("role") == "tablist"
        tabs = tablist.select('[role="tab"]')
        assert tabs
        active_tabs = [tab for tab in tabs if tab.get("aria-selected") == "true"]
        assert len(active_tabs) == 1
        for tab in tabs:
            tab_id = tab.get("id")
            controls = tab.get("aria-controls")
            assert tab_id, f"{selector} tab is missing id"
            assert controls, f"{selector} tab {tab_id} is missing aria-controls"
            panel = soup.find(id=controls)
            assert panel is not None, f"{selector} tab {tab_id} controls missing panel"
            assert panel.get("role") == "tabpanel"
            expected_tabindex = "0" if tab.get("aria-selected") == "true" else "-1"
            assert tab.get("tabindex") == expected_tabindex

    assert soup.find(id="lead-inbox-list").get("aria-labelledby") == "items-tab-purchases"
    assert soup.find(id="la-pane-form").get("aria-labelledby") == "la-tab-form"
    assert soup.find(id="la-pane-history").get("aria-labelledby") == "la-tab-history"


def test_filter_controls_have_accessible_state_and_labels(soup: BeautifulSoup) -> None:
    region = soup.find(id="filter-region")
    assert region is not None
    assert region.get("aria-label") == "Регион и город"

    edit_query = soup.find(id="edit-tracker-query")
    assert edit_query is not None
    edit_label = soup.find("label", attrs={"for": "edit-tracker-query"})
    assert edit_label is not None
    assert edit_label.get_text(strip=True) == "Запрос"

    for selector in (
        "[data-condition]",
        "[data-seller]",
        "[data-sort]",
        "[data-discount-from]",
        "[data-history-days]",
    ):
        controls = soup.select(selector)
        assert controls
        assert all(control.get("aria-pressed") in {"true", "false"} for control in controls)

    render_views = (JS_DIR / "render_views.js").read_text(encoding="utf-8")
    assert 'setAttribute("aria-pressed", String(active))' in render_views
    assert 'setAttribute("aria-pressed", String(allActive))' in render_views


def test_small_action_targets_keep_44px_minimum(css_text: str) -> None:
    assert ".summary-refinement-chip" in css_text
    assert ".empty-state-action" in css_text
    assert ".list-pagination-button" in css_text
    def declarations(selector: str, prop: str) -> list[str]:
        blocks: list[str] = []
        start = 0
        while True:
            pos = css_text.find(selector, start)
            if pos < 0:
                return blocks
            end = css_text.index("}", pos)
            block = css_text[pos:end]
            if prop in block:
                blocks.append(block)
            start = end + 1

    for selector in (
        ".theme-toggle",
        ".header-privacy-btn",
        ".summary-refinement-chip",
        ".empty-state-action",
        ".list-pagination-button",
        ".filter-chip",
        ".la-history-delete",
    ):
        height_blocks = declarations(selector, "min-height")
        width_blocks = declarations(selector, "min-width")
        assert height_blocks
        assert width_blocks
        assert "min-height: 44px" in height_blocks[-1]
        assert "min-width: 44px" in width_blocks[-1]


def test_listing_assistant_history_has_privacy_controls(
    soup: BeautifulSoup, css_text: str
) -> None:
    save_checkbox = soup.find(id="la-save-history-checkbox")
    assert save_checkbox is not None
    assert save_checkbox.get("type") == "checkbox"
    assert save_checkbox.has_attr("checked")

    clear_button = soup.find(id="la-history-clear")
    assert clear_button is not None
    assert clear_button.get("type") == "button"
    assert clear_button.has_attr("disabled")
    assert "Очистить историю" in clear_button.get_text(strip=True)
    page_text = soup.get_text(" ", strip=True)
    assert "Сохранять запросы в истории 30 дней" in page_text
    assert (
        "История с запросом и AI-ответом хранится только в этом браузере "
        "или Telegram WebView до 30 дней"
    ) in page_text
    assert "Запросы и AI-ответы хранятся локально до 30 дней" in page_text
    assert "серверное удаление аккаунта её не удаляет" in page_text

    la_js = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")
    assert 'const HISTORY_SAVE_KEY = "rafuk:listing-assistant:save-history";' in la_js
    assert "const HISTORY_TTL_MS = 30 * 24 * 60 * 60 * 1000;" in la_js
    assert "Date.parse(entry?.ts || \"\")" in la_js
    assert "if (!isHistorySavingEnabled()) return;" in la_js
    assert "localStorage.removeItem(HISTORY_KEY)" in la_js
    assert 'saveHistoryCheckbox?.addEventListener("change"' in la_js
    assert 'historyClearBtn?.addEventListener("click"' in la_js
    assert ".la-history-tools" in css_text
    assert ".la-history-privacy-note" in css_text


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
