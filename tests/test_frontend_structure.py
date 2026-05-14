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
    """Reconstruct the bundle the build script SHOULD have produced.

    OPUS-13: the script now pipes the concatenated source through
    ``scripts/minify_js.minify_js_text`` before writing to disk. We
    apply the same minifier here so the bundle-staleness assertion
    keeps catching forgotten rebuilds without false-positive-ing on
    every build-script side-effect.
    """
    from scripts.minify_js import minify_js_text

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
    return minify_js_text("".join(chunks))


@pytest.fixture(scope="module")
def soup() -> BeautifulSoup:
    assert HTML_FILE.exists()
    return BeautifulSoup(HTML_FILE.read_text(encoding="utf-8"), "html.parser")


@pytest.fixture(scope="module")
def css_text() -> str:
    """Concatenated source CSS for selector / declaration assertions.

    OPUS-13: ``style.css`` is now minified (whitespace + comments
    stripped). Asserting against the minified output would force
    every test to chase rcssmin's collapsing rules, so we read the
    source partials in cascade order instead — they're the same
    content the bundler concatenates BEFORE minification, so
    selector/declaration tests stay readable.
    """
    chunks: list[str] = []
    assert CSS_PARTS_DIR.is_dir(), "frontend/css/parts must exist"
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
    # OPUS-13 wave 72: render_charts.js (with ``ensureChartLib``) now
    # ships outside the bundle and is fetched on demand. The loader
    # itself must still exist on disk and Chart.js stays out of the
    # initial <script> tags so the document parse isn't blocked on
    # the chart library.
    render_charts = (FRONTEND / "js" / "render_charts.js").read_text(encoding="utf-8")
    assert "ensureChartLib" in render_charts
    assert "_lazyLoadChartsSources" in bundle
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


def test_nginx_disables_gzip_for_pii_endpoints() -> None:
    """OPUS-11: gzip is fine for anonymous analytics but the PII
    surfaces (account export/consent, leads, watchlist) must not
    travel through compression — removes the BREACH side-channel
    even if a future change makes attacker-controllable plaintext
    appear in those bodies.
    """
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    for prefix in ("/api/v1/account/", "/api/v1/leads", "/api/v1/watchlist"):
        # Each PII path needs its own location block with `gzip off`.
        marker = f"location ^~ {prefix}"
        assert marker in nginx_conf, f"missing dedicated nginx location for {prefix}"
        # The first `gzip off;` after the marker is the one that counts.
        block_start = nginx_conf.index(marker)
        block_end = nginx_conf.index("\n    }\n", block_start)
        block = nginx_conf[block_start:block_end]
        assert "gzip off;" in block, f"{prefix} location must turn gzip off"


def test_frontend_csp_pins_telegram_webview_inline_shim() -> None:
    """OPUS-14 wave 75: CSP must whitelist the sha256 hash of the
    inline script Telegram WebView injects on every Mini-App cold
    start (theme / viewport / keyboard hooks that live outside our
    source tree). Without it the browser console logs two CSP
    violations on every open and Telegram's native UI hooks
    silently degrade.

    Wave 62 dropped the hash on the assumption it was dead — the
    inline script DOES exist, it just lives in the Telegram client
    rather than in this repo, so `grep` couldn't find it. Wave 75
    restored the hash with a clearer comment. This test pins both
    sides of the policy so the hash doesn't accidentally get
    dropped again.
    """
    index_text = HTML_FILE.read_text(encoding="utf-8")
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    telegram_hash = "'sha256-ieoeWczDHkReVBsRBqaal5AFMlBtNjMzgwKvLqi/tSU='"
    assert telegram_hash in index_text, (
        "index.html CSP must keep Telegram WebView inline-shim hash"
    )
    # nginx CSP appears twice: server-level + /api/v1/* location.
    assert nginx_conf.count(telegram_hash) >= 2, (
        "nginx CSP must keep the Telegram hash in BOTH the server-level "
        "policy and the /api/v1/* location override (add_header doesn't "
        "inherit, so the override must repeat it)"
    )
    # The directive must still start with 'self' — no unsafe-inline.
    assert "script-src 'self' 'sha256-" in index_text
    assert "script-src 'self' 'sha256-" in nginx_conf


def test_service_worker_precaches_offline_stylesheet() -> None:
    sw = (FRONTEND / "sw.js").read_text(encoding="utf-8")
    assert '"/offline.css"' in sw
    assert "OFFLINE_FALLBACK_ASSETS" in sw
    assert "cache.addAll(OFFLINE_FALLBACK_ASSETS)" in sw


def test_app_bundle_uses_single_namespace_wrapper() -> None:
    """OPUS-13: bundle is minified, so we can't pin exact whitespace
    around the wrapper any more. Pin the structural invariants
    instead — the IIFE, the namespace assignment, every export name —
    in a way that survives rjsmin's whitespace stripping."""
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    # IIFE wrapper still encloses the bundle.
    assert bundle.startswith("(function(window){")
    assert bundle.rstrip().endswith("})(window);")
    # ``"use strict"`` and the App namespace seed live in the prologue.
    assert '"use strict";' in bundle[:200]
    assert "window.App=window.App||{}" in bundle[:200]
    # Final Object.assign export shape.
    assert "window.App=Object.assign(window.App||{}," in bundle
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
        assert name in bundle, f"export {name} missing from bundle"
    # No accidental window.X = X aliases left over.
    assert "window.analyticsApp=" not in bundle
    assert "window.createAppCore=" not in bundle


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


def test_ai_consent_provider_copy_is_config_driven() -> None:
    index_text = HTML_FILE.read_text(encoding="utf-8")
    actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    assert "Together API-совместимая модель Gemini" not in index_text
    assert 'id="consent-ai-provider-label"' in index_text
    assert "/api/v1/account/ai-consent-info" in actions
    assert "display_label" in actions


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


def test_listing_assistant_photo_upload_uses_keyboard_button(soup: BeautifulSoup) -> None:
    trigger = soup.find(id="la-photo-add")
    assert trigger is not None
    assert trigger.name == "button"
    assert trigger.get("type") == "button"
    assert trigger.get("aria-controls") == "la-photo-input"
    assert trigger.find("input") is None

    photo_input = soup.find(id="la-photo-input")
    assert photo_input is not None
    assert photo_input.get("type") == "file"
    assert photo_input.has_attr("hidden")
    assert photo_input.get("aria-label")

    la_js = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")
    assert "const _photoOpenHandler = () => photoInput?.click();" in la_js
    assert 'photoAdd?.addEventListener("click", _photoOpenHandler);' in la_js
    assert 'photoAdd?.removeEventListener("click", _photoOpenHandler);' in la_js


def test_edit_tracker_strict_toggle_has_no_nested_labels(soup: BeautifulSoup) -> None:
    modal = soup.find(id="edit-tracker-modal")
    assert modal is not None
    assert all(label.find("label") is None for label in modal.find_all("label"))

    strict_labels = modal.find_all("label", attrs={"for": "edit-strict-mode-toggle"})
    assert any("Строгий режим" in label.get_text(" ", strip=True) for label in strict_labels)
    assert any("strict-toggle" in (label.get("class") or []) for label in strict_labels)


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
    """No hard-coded colour literals outside the :root / [data-theme] blocks.

    The codebase used to scatter `var(--red, #e11d48)` fallbacks and
    raw `#16a34a` greens / `rgba(...)` shadows in dozens of rules; that
    broke theme switching because the literals didn't shift with the
    dark/light palette. Lock that down: the palette lives in the theme
    blocks, every other rule must reach for a CSS variable."""
    import re as _re

    lines = css_text.split("\n")
    in_theme_block = False
    depth = 0
    offenders: list[tuple[int, str]] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if _re.fullmatch(r'(:root|\[data-theme="(?:light|dark)"\])\s*\{', stripped):
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
        for match in _re.findall(r"\b(?:rgb|hsl)a?\([^)]*\)", line):
            if "var(" in line:
                continue
            offenders.append((i, f"{match} in {line.strip()[:100]}"))
            break

    assert not offenders, "Colour literals outside theme blocks: " + "; ".join(
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


def test_totals_refresh_buttons_force_refresh_search(soup: BeautifulSoup, css_text: str) -> None:
    """SEARCH-7: the analytics cache (`cache_ttl_seconds=300`) means a
    freshly opened result panel can lag kufar.by by a few ads on a
    fast-churning query like `iPhone 14 Pro`. Surface a tiny refresh
    button next to each totals badge so the user can opt into a fresh
    count without re-typing the query.
    """
    overview = soup.find(id="overview-refresh-btn")
    listings = soup.find(id="listings-refresh-btn")
    assert overview is not None, "overview totals must have a refresh button"
    assert listings is not None, "listings totals must have a refresh button"
    for button in (overview, listings):
        assert button.name == "button"
        assert button.get("type") == "button"
        # aria-label keeps the icon-only button accessible
        assert button.get("aria-label") == "Обновить количество объявлений"
        # share class with other ghost buttons + the dedicated refresh
        # variant so the spinner CSS can hook in
        classes = button.get("class") or []
        assert "ghost-btn" in classes
        assert "totals-refresh-btn" in classes
        assert button.find("svg") is not None

    # Refresh buttons sit inside the relevant section heads.
    stats_head = soup.find(id="stats-section").find(class_="sec-head")
    assert stats_head.find(id="overview-refresh-btn") is not None
    listings_head = soup.find(id="listings-section").find(class_="listings-head")
    assert listings_head.find(id="listings-refresh-btn") is not None
    sample_badge = listings_head.find(id="listings-sample-badge")
    assert sample_badge is not None
    assert sample_badge.get("hidden") is not None
    assert "badge--sample" in (sample_badge.get("class") or [])

    dom = (JS_DIR / "app_core_dom.js").read_text(encoding="utf-8")
    assert 'elements.overviewRefreshBtn = document.getElementById("overview-refresh-btn");' in dom
    assert 'elements.listingsRefreshBtn = document.getElementById("listings-refresh-btn");' in dom
    assert (
        'elements.listingsSampleBadge = document.getElementById("listings-sample-badge");'
        in dom
    )

    events = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    assert 'elements.overviewRefreshBtn?.addEventListener("click"' in events
    assert 'elements.listingsRefreshBtn?.addEventListener("click"' in events
    assert "{ forceRefresh: true }" in events
    # No-op when nothing has been searched yet so the button can't fire
    # an empty `query=` request.
    assert "if (!state.search.query?.trim()) return;" in events

    # CSS hooks for the spinning aria-busy state survive minification
    # via the `parts/` source files that the css_text fixture stitches.
    assert ".totals-refresh-btn" in css_text
    assert ".totals-refresh-btn[aria-busy=\"true\"]" in css_text
    assert "@keyframes totals-refresh-spin" in css_text


def test_limited_listings_show_persistent_sample_badge(css_text: str) -> None:
    cards_js = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")

    assert "function _updateListingsSampleBadge(forceHidden = false)" in cards_js
    assert "const badge = elements.listingsSampleBadge;" in cards_js
    assert "total > servedCap" in cards_js
    assert (
        "badge.textContent = `выборка ${_formatCount(servedCap)} "
        "из ${_formatCount(total)}`;" in cards_js
    )
    assert "badge.setAttribute(\"aria-label\", label);" in cards_js
    assert "_updateListingsSampleBadge(true);" in cards_js
    assert "_updateListingsSampleBadge();" in cards_js

    assert ".badge--sample" in css_text


def test_listings_section_stays_visible_on_zero_result_queries() -> None:
    """SEARCH-10: a zero-result search used to set
    `elements.listingsSection.hidden = !hasContent && !hasData`,
    which collapsed the entire #listings-section — taking the
    filter button, the totals badge, the SEARCH-7 refresh button
    and the SEARCH-9 "Снять фильтры" CTA out of the DOM with it.
    The fix keeps the section visible whenever a query is active
    so the recovery affordances remain reachable.
    """
    cards_js = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    core_js = (JS_DIR / "render_core.js").read_text(encoding="utf-8")

    # Section visibility now factors in `hasQuery` so 0-result
    # queries don't collapse the whole panel.
    assert "const hasQuery = Boolean(trimmedQuery);" in cards_js
    assert (
        "elements.listingsSection.hidden = !hasContent && !hasData && !hasQuery;"
        in cards_js
    )
    # Old shape (without hasQuery) is gone so a future revert can't
    # silently reintroduce the regression.
    assert (
        "elements.listingsSection.hidden = !hasContent && !hasData;"
        not in cards_js
    )

    # Empty-state copy now names the failing query.
    assert "По запросу «${trimmedQuery}» ничего не найдено." in cards_js
    # And distinguishes filtered vs. broad zero-result.
    assert "Возможно, фильтры слишком узкие — попробуйте снять часть." in cards_js
    assert "Попробуйте изменить запрос или сделать его короче." in cards_js
    # Badge stays in unit-consistent shape ("0 объявлений") instead
    # of shrinking to a bare "0".
    assert 'badge.textContent = "0 объявлений";' in cards_js

    # Overview summary signal explicitly says "ничего не найдено"
    # for the 0-result case instead of the misleading "small sample"
    # branch.
    assert "if (totalResults === 0 && analyzedCount === 0) {" in core_js
    assert (
        "По запросу ничего не найдено. Откройте «Объявления» "
        "и попробуйте снять фильтры или изменить запрос."
    ) in core_js


def test_empty_state_offers_clear_filters_when_filters_narrow_to_zero() -> None:
    """SEARCH-9: a filter-narrowed empty state without an inline escape
    forces the user to find the dropdown, open it, and clear each
    field by hand. Wire `actions.clearListingFilters` so the empty
    state shows a "Снять фильтры" CTA whenever the listings panel
    renders 0 cards while any filter is applied.
    """
    listings_js = (JS_DIR / "api_listings.js").read_text(encoding="utf-8")
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    cards_js = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")

    # The action exists, calls `resetCategoryFilter` and re-runs the
    # search using the existing pipeline.
    assert "async function clearListingFilters()" in listings_js
    assert "if (!state.search.query?.trim()) return;" in listings_js
    assert "if (!_hasActiveListingFilters()) return;" in listings_js
    assert "resetCategoryFilter();" in listings_js
    assert (
        'await search(state.ui.activeView || "ads", { keepFilters: true });'
        in listings_js
    )
    # Exported from the module's public surface.
    assert "clearListingFilters," in listings_js

    # Plumbed through both the cross-module context AND the public
    # action surface that app.js mirrors into actionRegistry.
    assert "clearListingFilters: listings.clearListingFilters," in actions_js
    # Make sure the line shows up at least twice (context + return).
    assert actions_js.count("clearListingFilters: listings.clearListingFilters,") >= 2

    # render_cards.js detects active filters and dispatches the action
    # via the shared `actions` registry.
    assert "function _hasActiveListingFilters()" in cards_js
    assert "actionLabel = \"Снять фильтры\"" in cards_js
    assert "onAction = () => { void clearAction(); };" in cards_js
    # Stay defensive against the action not being wired yet (e.g. on
    # a partially-loaded page) so the CTA simply doesn't render.
    assert "const clearAction = actions?.clearListingFilters;" in cards_js


def test_client_side_price_filter_uses_byn_against_filter_byn() -> None:
    """SEARCH-8: the filter dropdown labels its inputs "Цена, BYN", so
    `state.filters.minPrice` / `maxPrice` are always in BYN. The
    defensive client-side pass in `render_cards.js::matchesFilters`
    must compare them to `item.price_byn` (also BYN) — never
    `item.price`, which is in `state.misc.currency` and silently
    diverges when a future currency selector lands.
    """
    text = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    assert "item.price_byn" in text, (
        "matchesFilters must read price_byn so the comparison stays in BYN"
    )
    assert "itemPrice < state.filters.minPrice" not in text, (
        "old display-currency comparison must be removed"
    )
    assert "itemPrice > state.filters.maxPrice" not in text, (
        "old display-currency comparison must be removed"
    )
    # Stay defensive against the "Договорная" (price=null) case.
    assert "if (hasPriceRange && itemPriceByn == null) return false;" in text


def test_filter_controls_have_accessible_state_and_labels(soup: BeautifulSoup) -> None:
    region = soup.find(id="filter-region")
    assert region is not None
    assert region.get("aria-label") == "Регион и город"

    edit_query = soup.find(id="edit-tracker-query")
    assert edit_query is not None
    edit_label = soup.find("label", attrs={"for": "edit-tracker-query"})
    assert edit_label is not None
    assert edit_label.get_text(strip=True) == "Запрос"
    edit_category = soup.find(id="edit-category-select")
    assert edit_category is not None
    default_category_option = edit_category.find("option", attrs={"value": ""})
    assert default_category_option.get_text(strip=True) == "Любая категория"

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
        ".totals-refresh-btn",
        ".lead-btn--emoji",
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
    assert not save_checkbox.has_attr("checked")

    clear_button = soup.find(id="la-history-clear")
    assert clear_button is not None
    assert clear_button.get("type") == "button"
    assert clear_button.has_attr("disabled")
    assert "Очистить историю" in clear_button.get_text(strip=True)
    page_text = soup.get_text(" ", strip=True)
    assert "Сохранять запросы в истории 30 дней" in page_text
    assert (
        "История с запросом и AI-ответом по умолчанию выключена"
    ) in page_text
    assert "если включено сохранение истории" in page_text
    assert "История AI-помощника продавцу по умолчанию выключена" in page_text
    assert "серверное удаление аккаунта её не удаляет" in page_text

    la_js = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")
    assert 'const HISTORY_SAVE_KEY = "rafuk:listing-assistant:save-history";' in la_js
    assert "const HISTORY_TTL_MS = 30 * 24 * 60 * 60 * 1000;" in la_js
    assert 'localStorage.getItem(HISTORY_SAVE_KEY) === "1"' in la_js
    assert 'localStorage.getItem(HISTORY_SAVE_KEY) !== "0"' not in la_js
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
