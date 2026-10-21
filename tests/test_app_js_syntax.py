from __future__ import annotations

import re
import subprocess
from pathlib import Path

APP_JS = Path("frontend/js/app.js")
JS_DIR = Path("frontend/js")
JS_MODULES = sorted(JS_DIR.glob("*.js"))
CSS_DIR = Path("frontend/css")


def _read_all_css() -> str:
    """Concatenate every CSS file under frontend/css/.

    style.css is now a thin @import loader; the actual rules live
    under parts/. Tests asserting against rule presence shouldn't
    care which partial owns a class, so we glob the whole tree.
    """
    chunks: list[str] = []
    for css_path in sorted(CSS_DIR.rglob("*.css")):
        chunks.append(css_path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_runtime_frontend_sources_do_not_use_gradients() -> None:
    runtime_paths = [
        Path("frontend/index.html"),
        *sorted((CSS_DIR / "parts").glob("*.css")),
        CSS_DIR / "style.css",
        *[path for path in JS_MODULES if path.parent.name != "vendor"],
    ]
    gradient_re = re.compile(
        r"\b(?:linear|radial|conic|repeating-linear|repeating-radial)-gradient\b"
    )
    offenders = [
        str(path)
        for path in runtime_paths
        if gradient_re.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_listing_detail_ai_action_uses_primary_accent_not_violet() -> None:
    css = _read_all_css()
    index_html = Path("frontend/index.html").read_text(encoding="utf-8")
    ai_rules = "\n".join(
        match.group(0)
        for match in re.finditer(r"\.listing-btn--ai[^{]*\{[^}]+\}", css)
    )
    assert "var(--accent-secondary)" not in ai_rules
    assert "var(--violet)" not in ai_rules
    assert "var(--accent)" in ai_rules
    assert 'id="detail-ai-btn" type="button">Анализ</button>' in index_html


def test_file_is_not_empty() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert len(text.strip()) > 200


def test_defines_analytics_app_function() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert re.search(r"function\s+analyticsApp\b|analyticsApp\s*=\s*function", text)


def test_has_required_methods_and_endpoints() -> None:
    all_js = "\n".join(p.read_text(encoding="utf-8") for p in JS_MODULES)
    assert re.search(r"\bsearch\s*\(", all_js)
    assert re.search(r"\bloadListings\s*\(", all_js)
    assert re.search(r"\bloadDeals\s*\(", all_js)
    assert re.search(r"\brenderChart\b|\brenderBoxPlot\b", all_js)
    assert "/api/v1/price-stats" in all_js
    assert "/api/v1/price-history" in all_js
    assert "/api/v1/listings" in all_js
    assert "Telegram.WebApp" in all_js


def test_price_format_is_plain_byn_everywhere() -> None:
    dom_helpers_js = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    app_core_dom_js = (JS_DIR / "app_core_dom.js").read_text(encoding="utf-8")
    app_core_js = (JS_DIR / "app_core.js").read_text(encoding="utf-8")
    render_core_js = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    trackers_js = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")

    harness = (
        dom_helpers_js
        + app_core_dom_js
        + app_core_js
        + r"""
const assert = require("assert");
global.document = { documentElement: { setAttribute() {}, getAttribute() { return "dark"; } } };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.window = { Telegram: null, matchMedia: null };
const core = createAppCore();
assert.strictEqual(core.formatPrice(1150), "1150 BYN");
assert.strictEqual(core.formatPrice(2500), "2500 BYN");
assert.strictEqual(core.formatPrice(999.4), "999 BYN");
assert.strictEqual(core.formatPrice(null), "Договорная");
assert.strictEqual(core.formatPrice(0, "free"), "Бесплатно");
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "тыс. р." not in app_core_js
    assert "`к`" not in render_core_js
    assert " р." not in trackers_js


def test_listing_filter_query_omits_non_finite_prices() -> None:
    api_listings = (JS_DIR / "api_listings.js").read_text(encoding="utf-8")
    harness = (
        api_listings
        + r"""
const assert = require("assert");
// RV-FE-03: requests are built from the active query, not the draft.
const state = {
    search: {
        draft: "",
        activeQuery: "iphone",
        strictSearch: true,
        sort: "newest",
        searchRequestId: 1,
    },
    filters: {
        category: null,
        minPrice: "1000",
        maxPrice: NaN,
        condition: "used",
        sellerType: "private",
        regionName: "Минск",
        discountFromPercent: 10,
        discountToPercent: 30,
    },
    listings: {
        items: [],
        _loadedAt: 0,
        _loadedSort: null,
        total: 0,
        hasMore: false,
        loading: false,
        loadingMore: false,
        _requestId: 0,
        _pending: false,
        fallbackUsed: false,
    },
    misc: { currency: "BYN" },
    detail: { _requestId: 0 },
    charts: { _historyRequestId: 0 },
    ui: {},
};
let requestedUrl = "";
const api = createApiListings({
    state,
    elements: {},
    hasTelegramInitData: () => false,
    renderAll() {},
    scheduleRender() {},
    markDirty() {},
    renderLoading() {},
    renderError() {},
    renderHistory() {},
    setPanelOpen() {},
    renderDetailModal() {},
    closeDetailModal() {},
    showToast() {},
    buildCommonQuery(params = {}) {
        const query = new URLSearchParams({
            query: state.search.activeQuery,
            currency: state.misc.currency,
            strict_search: String(state.search.strictSearch),
        });
        for (const [key, value] of Object.entries(params)) {
            if (value != null && value !== "") query.set(key, String(value));
        }
        return query.toString();
    },
    getJson(url) {
        requestedUrl = url;
        return Promise.resolve({ listings: [], total: 0, has_more: false });
    },
    deleteJson() {},
});
api.loadListings(true).then(() => {
    const params = new URLSearchParams(requestedUrl.split("?")[1]);
    assert.strictEqual(params.get("min_price"), "1000");
    assert.strictEqual(params.has("max_price"), false);
    assert.strictEqual(params.get("condition"), "used");
    assert.strictEqual(params.get("seller_type"), "private");
    assert.strictEqual(params.get("region_name"), "Минск");
}).catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_request_json_summarizes_fastapi_validation_arrays() -> None:
    api_core_js = (JS_DIR / "api_core.js").read_text(encoding="utf-8")
    harness = (
        api_core_js
        + r"""
const assert = require("assert");
global.window = { Telegram: { WebApp: { initData: "" } } };
global.fetch = async () => ({
    ok: false,
    status: 422,
    headers: { get() { return null; } },
    json: async () => ({
        detail: [
            { loc: ["body", "status_code"], msg: "Field required" },
            { loc: ["body", "ai_daily_limit"], msg: "Input should be greater than or equal to 0" },
        ],
    }),
});
const api = createApiCore({
    state: { search: { query: "" }, misc: { currency: "BYN" }, filters: {} },
    elements: {},
});
api.requestJson("/api/v1/admin/statuses/scout", { method: "PATCH" })
    .then(() => {
        console.error("request unexpectedly succeeded");
        process.exit(1);
    })
    .catch((error) => {
        assert.strictEqual(error.status, 422);
        assert.match(error.message, /status_code: Field required/);
        assert.match(error.message, /ai_daily_limit: Input should be greater than or equal to 0/);
        assert.notStrictEqual(error.message, "Не удалось выполнить запрос.");
    });
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_admin_users_ignores_stale_responses() -> None:
    api_admin_js = (JS_DIR / "api_admin.js").read_text(encoding="utf-8")
    harness = (
        api_admin_js
        + r"""
const assert = require("assert");
const state = {
    profile: { data: { permissions: { is_admin: true } } },
    admin: {
        query: "older",
        selectedStatus: "",
        loading: false,
        error: "",
        users: [],
        savingUserIds: new Set(),
        savingStatusCodes: new Set(),
    },
};
const pending = [];
const api = createApiAdmin({
    state,
    getJson(url) {
        return new Promise((resolve) => pending.push({ url, resolve }));
    },
    requestJson: async () => ({}),
    markDirty() {},
    renderAll() {},
    showToast() {},
});
(async () => {
    const first = api.loadAdminUsers();
    state.admin.query = "newer";
    const second = api.loadAdminUsers();
    assert.match(pending[0].url, /query=older/);
    assert.match(pending[1].url, /query=newer/);
    pending[1].resolve([{ telegram_user_id: 2, first_name: "Newer" }]);
    await second;
    pending[0].resolve([{ telegram_user_id: 1, first_name: "Older" }]);
    await first;
    assert.deepStrictEqual(state.admin.users.map((u) => u.telegram_user_id), [2]);
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_ai_modal_access_error_handler_suppresses_duplicate_error_ui() -> None:
    api_ai_js = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")
    harness = (
        r"""
const assert = require("assert");
global.window = { App: {} };
let loaderErrorCalls = 0;
let renderErrorCalls = 0;
window.App.createAiModal = () => ({
    openAIModal() {},
    stopLoadingAnimation() {},
    showLoaderError() { loaderErrorCalls += 1; },
    closeAIModal() {},
});
window.App.createAiRender = () => ({
    renderAIModalResult() {},
    renderAiErrorState() { renderErrorCalls += 1; },
});
"""
        + api_ai_js
        + r"""
const state = {
    search: { query: "iphone" },
    filters: {},
    detail: { data: { query: "iphone", title: "iPhone" }, ai: null },
};
const handledError = new Error("AI доступен со статуса Скаут Барахолки");
handledError.detail = { error: "premium_required", message: handledError.message };
handledError.errorCode = "premium_required";
const api = window.App.createApiAi({
    state,
    elements: {
        aiModalLoading: { hidden: false },
        aiModalResult: { hidden: false },
        aiModalError: { hidden: true },
    },
    postJson: async () => { throw handledError; },
    getJson: async () => ({}),
    handleAiAccessError: async () => true,
    logClientError() {},
});
(async () => {
    await api.loadAIAnalysis(42);
    await new Promise((resolve) => setTimeout(resolve, 800));
    assert.strictEqual(loaderErrorCalls, 0);
    assert.strictEqual(renderErrorCalls, 0);
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_listing_dependency_does_not_overwrite_changed_filters() -> None:
    api_listings = (JS_DIR / "api_listings.js").read_text(encoding="utf-8")
    harness = (
        api_listings
        + r"""
const assert = require("assert");
const state = {
    search: {
        draft: "",
        activeQuery: "iphone",
        strictSearch: true,
        sort: "newest",
        searchRequestId: 7,
        searchAbortController: null,
    },
    filters: {
        category: null,
        minPrice: null,
        maxPrice: null,
        condition: "",
        sellerType: "",
        regionName: "",
        discountFromPercent: 10,
        discountToPercent: 30,
    },
    listings: {
        items: [],
        _loadedAt: 0,
        _loadedSort: null,
        total: 0,
        hasMore: false,
        loading: false,
        loadingMore: false,
        _requestId: 0,
        _pending: true,
        fallbackUsed: false,
    },
    misc: { currency: "BYN", segments: null, geography: [] },
    detail: { _requestId: 0 },
    charts: { _historyRequestId: 0, historyData: [] },
    ui: {},
};
let resolveListings;
const listingsPromise = new Promise((resolve) => { resolveListings = resolve; });
const api = createApiListings({
    state,
    elements: {},
    hasTelegramInitData: () => false,
    renderAll() {},
    scheduleRender() {},
    markDirty() {},
    renderLoading() {},
    renderError() {},
    renderHistory() {},
    setPanelOpen() {},
    renderDetailModal() {},
    closeDetailModal() {},
    showToast() {},
    buildCommonQuery(params = {}) {
        const query = new URLSearchParams({
            query: state.search.activeQuery,
            currency: state.misc.currency,
            strict_search: String(state.search.strictSearch),
        });
        for (const [key, value] of Object.entries(params)) {
            if (value != null && value !== "") query.set(key, String(value));
        }
        return query.toString();
    },
    getJson(url) {
        if (url.startsWith("/api/v1/listings?")) {
            return listingsPromise;
        }
        return Promise.resolve({ points: [], regions: [] });
    },
    deleteJson() {},
});
api.loadSearchDependencies(7);
state.filters.minPrice = 1000;
resolveListings({ listings: [{ ad_id: 1 }], total: 1, has_more: false });
setImmediate(() => {
    assert.deepStrictEqual(state.listings.items, []);
    assert.strictEqual(state.listings.total, 0);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_search_refresh_and_cap_disclosure_contracts() -> None:
    app_js = (JS_DIR / "app.js").read_text(encoding="utf-8")
    events_js = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    listings_js = (JS_DIR / "api_listings.js").read_text(encoding="utf-8")
    render_cards_js = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    render_views_js = (JS_DIR / "render_views.js").read_text(encoding="utf-8")
    core_js = (JS_DIR / "app_core.js").read_text(encoding="utf-8")

    # RV-FE-03: pull-to-refresh re-runs the active query, not the draft.
    assert "{ forceRefresh: true, query }" in app_js
    assert "forceRefresh: true" in events_js
    assert "force_refresh: forceRefresh ? \"1\" : \"\"" in listings_js
    assert "state.listings.servedCap" in render_cards_js
    assert "Показана быстрая выборка" not in render_cards_js
    assert "elements.listingsSampleBadge" not in render_cards_js
    assert "выборка ${_formatCount(servedCap)} из ${_formatCount(total)}" not in render_cards_js
    assert "categories_limited" in render_views_js
    assert "const displayTotal = totalResults || totalCount;" in render_views_js
    assert "Math.max(totalResults, totalCount)" not in render_views_js
    assert "resultCap: 0" in core_js


def test_telegram_back_button_stack_is_registered() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert "setupTelegramBackButton" in text
    assert "BackButton" in text
    assert ".onClick(onBack)" in text
    assert ".offClick?.(onBack)" in text
    assert "MutationObserver" in text


def test_frontend_requests_and_ai_polling_have_jittered_retries() -> None:
    api_core = (JS_DIR / "api_core.js").read_text(encoding="utf-8")
    api_ai = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")
    assert "RETRYABLE_STATUSES" in api_core
    assert "retry-after" in api_core
    assert "Math.random()" in api_core
    assert "nextPollDelay" in api_ai
    assert "Math.random()" in api_ai


def test_retryable_statuses_excludes_429() -> None:
    """OPUS-3: 429 must NOT be in the retryable set. The AI rate
    limiter increments before checking the cap, so a retried 429
    silently drains another quota point even though the original
    request was already refused. Each retry only makes the user's
    backoff window longer.

    OPUS-13: bundle is minified — rjsmin collapses ``[502, 503,
    504]`` to ``[502,503,504]``. Match against both the spaced
    source form and the unspaced minified form.
    """
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    api_core = (JS_DIR / "api_core.js").read_text(encoding="utf-8")
    spaced = "const RETRYABLE_STATUSES = new Set([502, 503, 504]);"
    minified = "const RETRYABLE_STATUSES=new Set([502,503,504])"
    assert spaced in api_core, "api_core.js must keep 429 out of RETRYABLE_STATUSES"
    assert minified in bundle or spaced in bundle, (
        "app_bundle.js must mirror api_core.js (minified or pre-build)"
    )
    forbidden_spaced = "const RETRYABLE_STATUSES = new Set([429"
    forbidden_minified = "const RETRYABLE_STATUSES=new Set([429"
    assert forbidden_spaced not in api_core
    assert forbidden_minified not in bundle and forbidden_spaced not in bundle


def test_cards_split_uses_lazy_stub_in_bundle() -> None:
    """OPUS-13 wave 73: render_card_builders.js + render_cards.js
    ship as ``_lazy_cards_stub.js`` and load in tandem (builders
    first, renderer second) on first use. virtual_list.js stays
    in the bundle because trackers + cards both need
    ``createVirtualList`` reachable through context.
    """
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    stub = (JS_DIR / "_lazy_cards_stub.js").read_text(encoding="utf-8")
    cards = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    builders = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")

    assert "_lazyLoadCardsSources" in bundle
    assert "_realCreateRenderCards" in bundle
    assert "_realCreateRenderCardBuilders" in bundle

    assert "window.App._realCreateRenderCards = createRenderCards" in cards
    assert "window.App._realCreateRenderCardBuilders = createRenderCardBuilders" in builders

    cache_buster = re.compile(
        r'"js/(?:render_card_builders|render_cards)\.js\?v=[A-Za-z0-9._-]+"'
    )
    assert len(cache_buster.findall(stub)) == 2

    build = Path("scripts/build_frontend_bundle.sh").read_text(encoding="utf-8")
    for bare in ("render_card_builders", "render_cards"):
        leak = re.search(rf"^\s+{bare}\.js\s*$", build, re.MULTILINE)
        assert leak is None, f"{bare}.js leaked back into bundle modules list"
    # virtual_list.js MUST remain in the bundle — both trackers (lazy)
    # and cards (lazy) reach it through context.createVirtualList.
    assert re.search(r"^\s+virtual_list\.js\s*$", build, re.MULTILINE) is not None

    # Eager preload from app.js avoids a stub-returns-undefined flash
    # on overview / ads / deals / tracking cold paint.
    app_js = (JS_DIR / "app.js").read_text(encoding="utf-8")
    assert "_cardsEnsureLoaded" in app_js

    # Renderer bundle exposes the preload hook.
    app_renderers = (JS_DIR / "app_renderers.js").read_text(encoding="utf-8")
    assert "_cardsEnsureLoaded: cards && cards._ensureLoaded" in app_renderers

    # render_trackers / render_cards read createVirtualList from context.
    trackers = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")
    assert "createVirtualList," in trackers, (
        "render_trackers must destructure createVirtualList from context"
    )
    assert "createVirtualList," in cards, (
        "render_cards must destructure createVirtualList from context"
    )


def test_lazy_modules_destructure_dom_helpers_from_context() -> None:
    """OPUS-13 wave 74: every dom_helpers / dom_helpers-like callable
    used inside a lazy <script> must be destructured from context.

    The bundle wraps everything in an IIFE, so module-level functions
    in dom_helpers.js (``domEl``, ``domClear``, ``domFragment``,
    ``openModalAnimated``, ``closeModalAnimated``, ``attachLongPress``,
    ``attachPinchZoom``) are scoped to the IIFE and unreachable from
    a separately-loaded lazy <script>. Wave 74 funnels them through
    context.

    Without these destructures the production console floods with
    ``ReferenceError: domClear is not defined`` the moment a tab
    opens; this test pins each lazy file's destructure block so the
    regression doesn't sneak back when someone splits a new chunk.
    """
    requirements = {
        "render_trackers.js": ("domEl", "domClear"),
        "render_cards.js": ("domEl", "domClear"),
        "render_card_builders.js": ("domEl", "domFragment", "attachLongPress"),
        "render_modals.js": (
            "domClear",
            "openModalAnimated",
            "closeModalAnimated",
            "attachPinchZoom",
        ),
        "render_charts.js": ("domEl", "domClear", "domFragment"),
        "api_trackers.js": ("domEl", "openModalAnimated", "closeModalAnimated"),
        "api_watchlist.js": ("inflightGuardMs",),
    }
    for filename, helpers in requirements.items():
        src = (JS_DIR / filename).read_text(encoding="utf-8")
        # destructure block sits between ``function create.*(context) {``
        # and the next ``} = context;``. Pin each helper inside it.
        match = re.search(
            r"function\s+create\w+\s*\(context\)\s*\{\s*const\s*\{([^}]+)\}\s*=\s*context;",
            src,
        )
        assert match is not None, f"{filename}: no destructure block found"
        block = match.group(1)
        for helper in helpers:
            assert re.search(rf"\b{helper}\b", block), (
                f"{filename}: must destructure ``{helper}`` from context "
                f"(otherwise ReferenceError when lazy-loaded)"
            )

    # The supplier side: app_renderers.js + app_actions.js must put
    # those helpers on context BEFORE the lazy factory is invoked.
    app_renderers = (JS_DIR / "app_renderers.js").read_text(encoding="utf-8")
    for fn in (
        "domEl",
        "domClear",
        "domFragment",
        "openModalAnimated",
        "closeModalAnimated",
        "attachLongPress",
        "attachPinchZoom",
    ):
        assert f"context.{fn} = {fn};" in app_renderers, (
            f"app_renderers.js must expose ``{fn}`` on context"
        )

    app_actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    for fn in ("domEl", "openModalAnimated", "closeModalAnimated"):
        assert f"context.{fn} = {fn};" in app_actions, (
            f"app_actions.js must expose ``{fn}`` on context for lazy api_*"
        )
    assert 'typeof INFLIGHT_GUARD_MS === "number" ? INFLIGHT_GUARD_MS : 30_000' in app_actions
    assert "context.inflightGuardMs = inflightGuardMs;" in app_actions


def test_lazy_watchlist_add_uses_context_guard_timer() -> None:
    """The watchlist API is lazy-loaded outside app_bundle's IIFE.

    Referencing ``INFLIGHT_GUARD_MS`` directly from api_watchlist.js
    throws ``ReferenceError`` before the POST request when a user taps
    "В избранное". The real module must use the value supplied through
    context instead.
    """
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")
    harness = (
        watchlist_js
        + r"""
const assert = require("assert");
const calls = [];
const api = createApiWatchlist({
    state: {
        watchlist: { items: [], _requestId: 0 },
        leads: { items: [] },
        search: { query: "iphone" },
        misc: { stats: {} },
    },
    elements: {},
    hasTelegramInitData: () => true,
    renderAll: () => {},
    renderError: () => {},
    renderWatchlist: () => {},
    renderLeads: () => {},
    renderDetailModal: () => {},
    showToast: (...args) => calls.push(["toast", ...args]),
    getJson: async () => [],
    postJson: async (...args) => {
        calls.push(["post", ...args]);
        return {};
    },
    deleteJson: async () => {},
    requestJson: async () => {},
    buildCommonQuery: () => "",
    inflightGuardMs: 1,
});

api.addWatchlistFromListing({
    ad_id: "ad-1",
    title: "iPhone",
    link: "https://example.test/ad-1",
    price_byn: 1000,
}).then(() => {
    assert.strictEqual(calls[0][0], "post");
}).catch((err) => {
    console.error(err && err.stack || err);
    process.exit(1);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_charts_split_uses_lazy_stub_in_bundle() -> None:
    """OPUS-13 wave 72: render_charts.js ships as
    ``_lazy_charts_stub.js`` and is loaded on demand the first
    time a chart paints. Chart.js itself was already lazy; this
    defers the renderer too.
    """
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    stub = (JS_DIR / "_lazy_charts_stub.js").read_text(encoding="utf-8")
    real = (JS_DIR / "render_charts.js").read_text(encoding="utf-8")

    assert "_lazyLoadChartsSources" in bundle
    assert "_realCreateRenderCharts" in bundle
    assert "window.App._realCreateRenderCharts = createRenderCharts" in real

    cache_buster = re.compile(r'"js/render_charts\.js\?v=[A-Za-z0-9._-]+"')
    assert len(cache_buster.findall(stub)) == 1

    build = Path("scripts/build_frontend_bundle.sh").read_text(encoding="utf-8")
    leak = re.search(r"^\s+render_charts\.js\s*$", build, re.MULTILINE)
    assert leak is None, "render_charts.js leaked back into bundle modules list"
    assert "_lazy_charts_stub.js" in build


def test_deals_split_uses_lazy_stub_in_bundle() -> None:
    """OPUS-13 wave 71: api_leads.js + api_watchlist.js +
    render_modals.js are no longer concatenated into
    app_bundle.js; the bundle ships ``_lazy_deals_stub.js``
    instead. Same delegation pattern as the trackers split — see
    test_trackers_split_uses_lazy_stub_in_bundle for the rationale.
    """
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    stub = (JS_DIR / "_lazy_deals_stub.js").read_text(encoding="utf-8")
    leads = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    watchlist = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")
    modals = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")

    assert "_lazyLoadDealsSources" in bundle
    for global_key in (
        "_realCreateApiLeads",
        "_realCreateApiWatchlist",
        "_realCreateRenderModals",
    ):
        assert global_key in bundle

    assert "window.App._realCreateApiLeads = createApiLeads" in leads
    assert "window.App._realCreateApiWatchlist = createApiWatchlist" in watchlist
    assert "window.App._realCreateRenderModals = createRenderModals" in modals

    cache_buster = re.compile(
        r'"js/(?:api_leads|api_watchlist|render_modals)\.js\?v=[A-Za-z0-9._-]+"'
    )
    assert len(cache_buster.findall(stub)) == 3

    build = Path("scripts/build_frontend_bundle.sh").read_text(encoding="utf-8")
    for bare in ("api_leads", "api_watchlist", "render_modals"):
        leak = re.search(rf"^\s+{bare}\.js\s*$", build, re.MULTILINE)
        assert leak is None, f"{bare}.js leaked back into bundle modules list"
    assert "_lazy_deals_stub.js" in build


def test_trackers_split_uses_lazy_stub_in_bundle() -> None:
    """OPUS-13 wave 70: render_trackers.js + api_trackers.js are no
    longer concatenated into app_bundle.js; the bundle ships
    ``_lazy_trackers_stub.js`` which delegates through
    ``window.App._realCreate{Api,Render}Trackers`` once the user
    opens the Автопоиск tab. Pin every contract in this test so
    the next refactor that breaks the wiring fails loudly.
    """
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    stub = (JS_DIR / "_lazy_trackers_stub.js").read_text(encoding="utf-8")
    real_render = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")
    real_api = (JS_DIR / "api_trackers.js").read_text(encoding="utf-8")

    # 1. Stub ships in the bundle.
    assert "_lazyLoadTrackerSources" in bundle
    assert "_realCreateRenderTrackers" in bundle
    assert "_realCreateApiTrackers" in bundle

    # 2. Real modules register themselves on window.App so the stub
    #    can resolve them after _loadScript completes.
    assert "window.App._realCreateRenderTrackers = createRenderTrackers" in real_render
    assert "window.App._realCreateApiTrackers = createApiTrackers" in real_api

    # 3. Stub holds hard-coded ?v= tags so bump_static_version.sh
    #    keeps both URLs in sync with the rest of the cache-busters.
    cache_buster = re.compile(r'"js/(?:render_trackers|api_trackers)\.js\?v=[A-Za-z0-9._-]+"')
    assert len(cache_buster.findall(stub)) == 2

    # 4. Build script no longer concatenates the heavy files.
    build = Path("scripts/build_frontend_bundle.sh").read_text(encoding="utf-8")
    # The names appear in a comment but NOT as bare list entries.
    bare_render = re.search(r"^\s+render_trackers\.js\s*$", build, re.MULTILINE)
    bare_api = re.search(r"^\s+api_trackers\.js\s*$", build, re.MULTILINE)
    assert bare_render is None, "render_trackers.js leaked back into bundle modules list"
    assert bare_api is None, "api_trackers.js leaked back into bundle modules list"
    assert "_lazy_trackers_stub.js" in build


def test_svg_sanitizer_drops_xmlns_attribute() -> None:
    """OPUS-21: ``xmlns`` belongs in the auto-namespacing path the
    browser already runs on innerHTML — keeping it in the explicit
    whitelist only invites namespace-confusion if a future caller
    routes user-supplied SVG through showLongPressMenu.
    """
    bundle = (JS_DIR / "app_bundle.js").read_text(encoding="utf-8")
    helpers = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    forbidden = '"xmlns",'
    assert forbidden not in helpers, "dom_helpers.js still allowlists xmlns"
    assert forbidden not in bundle, "app_bundle.js still allowlists xmlns"


def test_pull_to_refresh_does_not_translate_app_root() -> None:
    text = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    setup = text[text.index("function setupPullToRefresh"):text.index("/* ─── Pinch-zoom")]
    assert "document.querySelector('.app')" not in setup
    assert "target.style.transform" not in setup
    assert "indicatorEl.style.transform" in setup


def test_pinch_zoom_caches_viewport_size_for_gestures() -> None:
    text = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    assert "let viewportSize = null" in text
    assert "function ensureViewportSize()" in text
    assert "const viewport = ensureViewportSize()" in text
    pinch = text[
        text.index("function attachPinchZoom"):
        text.index("/* ─── Long-press action menu")
    ]
    assert "getBoundingClientRect" not in pinch
    assert "style.transform = \"none\"" not in pinch


def test_node_syntax_check() -> None:
    for script in JS_MODULES:
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_frontend_url_helpers_are_host_aware() -> None:
    render_core = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    harness = (
        r"""
const tgCalls = [];
const windowOpenCalls = [];
global.window = {
    location: { origin: "https://rafuk.local", href: "" },
    Telegram: { WebApp: { openLink: (url, options) => tgCalls.push({ url, options }) } },
    open: (url, target, features) => windowOpenCalls.push({ url, target, features }),
};
"""
        + render_core
        + r"""
const core = createRenderCore({ state: {}, elements: {} });

function assertEq(actual, expected, label) {
    if (actual !== expected) {
        throw new Error(
            `${label}: expected ${JSON.stringify(expected)}, ` +
            `got ${JSON.stringify(actual)}`
        );
    }
}

const sameOriginPath = "/api/v1/account?tab=privacy#top";
const galleryImage = "https://rms.kufar.by/v1/gallery/a/b.jpg";
const cases = [
    ["same-origin path", core.safeUrl(sameOriginPath), sameOriginPath],
    ["same-origin absolute", core.safeUrl("https://rafuk.local/app"), "https://rafuk.local/app"],
    ["protocol-relative external", core.safeUrl("//evil.example/pixel"), ""],
    ["javascript scheme", core.safeUrl("javascript:alert(1)"), ""],
    ["same-origin blob", core.safeUrl("blob:https://rafuk.local/payload"), ""],
    ["external https", core.safeUrl("https://evil.example/"), ""],
    ["external http", core.safeUrl("http://www.kufar.by/item/1"), ""],
    ["valid kufar www", core.safeKufarUrl("https://www.kufar.by/item/1?utm=rafuk"), "https://www.kufar.by/item/1?utm=rafuk"],
    ["valid kufar apex", core.safeKufarUrl("https://kufar.by/item/2"), "https://kufar.by/item/2"],
    ["valid kufar redirect host", core.safeKufarUrl("https://re.kufar.by/abc123"), "https://re.kufar.by/abc123"],
    ["valid kufar auto", core.safeKufarUrl("https://auto.kufar.by/vi/10?size=xl"), "https://auto.kufar.by/vi/10?size=xl"],
    ["invalid auto non-vehicle path", core.safeKufarUrl("https://auto.kufar.by/item/10"), ""],
    ["non-kufar listing", core.safeKufarUrl("https://evil.example/item/1"), ""],
    ["protocol-relative kufar", core.safeKufarUrl("//www.kufar.by/item/1"), ""],
    ["encoded traversal kufar",
        core.safeKufarUrl("https://www.kufar.by/item/%2e%2e/admin"), ""],
    ["encoded traversal auto",
        core.safeKufarUrl("https://auto.kufar.by/vi/%2e%2e/admin"), ""],
    ["valid kufar image", core.safeImageUrl(galleryImage), galleryImage],
    ["non-gallery kufar image", core.safeImageUrl("https://rms.kufar.by/other/a.jpg"), ""],
    ["encoded traversal image",
        core.safeImageUrl("https://rms.kufar.by/v1/gallery/%2e%2e/a.jpg"), ""],
    ["non-kufar image", core.safeImageUrl("https://evil.example/a.jpg"), ""],
    ["data image", core.safeImageUrl("data:image/png;base64,AA=="), ""],
    ["invalid optimized image", core.optimizedImage("https://evil.example/a.jpg"), ""],
    ["proxied gallery image",
        core.optimizedImage(galleryImage, { useProxy: true, width: 200.4 }),
        "/api/v1/img/a/b.jpg?w=200"],
];
for (const [label, actual, expected] of cases) assertEq(actual, expected, label);

assertEq(core.openExternalLink("https://www.kufar.by/item/1"), true, "Telegram openLink result");
assertEq(tgCalls[0].url, "https://www.kufar.by/item/1", "Telegram openLink URL");
assertEq(tgCalls[0].options.try_browser, true, "Telegram opens in browser");
global.window.Telegram = null;
assertEq(
    core.openExternalLink("https://auto.kufar.by/vi/10"),
    true,
    "window.open fallback result"
);
assertEq(windowOpenCalls[0].target, "_blank", "window.open target");
assertEq(windowOpenCalls[0].features, "noopener,noreferrer", "window.open features");
assertEq(core.openExternalLink("javascript:alert(1)"), false, "reject script URL");
assertEq(windowOpenCalls.length, 1, "invalid URL not opened");
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_frontend_image_proxy_policy_is_explicit() -> None:
    core_js = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    image_proxy_js = (JS_DIR / "api_image_proxy.js").read_text(encoding="utf-8")
    modals_js = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")
    carousel_js = (JS_DIR / "api_events_carousel.js").read_text(encoding="utf-8")
    cards_js = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    bundle_script = Path("scripts/build_frontend_bundle.sh").read_text(encoding="utf-8")

    assert "Plain <img> tags cannot send Telegram initData" in core_js
    assert "api_image_proxy.js" in bundle_script
    assert "createImageProxyLoader(core)" in actions_js
    assert "async function fetchProxyImageObjectUrl" in image_proxy_js
    assert "...core.telegramHeaders()" in image_proxy_js
    assert 'Accept: "image/avif,image/webp,image/*,*/*"' in image_proxy_js
    assert "URL.createObjectURL" in image_proxy_js
    assert "function clearProxyImageObjectUrls" in image_proxy_js

    assert "setDetailMainImage(currentImage, 800" in modals_js
    assert "actions.fetchProxyImageObjectUrl(proxyUrl)" in modals_js
    assert "img.src = optimizeWith(image, 120, false)" in modals_js
    assert "actions.clearProxyImageObjectUrls()" in modals_js

    assert "ctx.fetchProxyImageObjectUrl(proxyUrl)" in carousel_js
    assert "ctx.optimizedImage(validated, { width: 800, useProxy: true })" in carousel_js

    build_media = cards_js[
        cards_js.index("function buildMediaNode"):
        cards_js.index("\n    /** Watchlist", cards_js.index("function buildMediaNode"))
    ]
    assert "useProxy" not in build_media


def test_detail_proxy_image_keeps_direct_src_while_object_url_loads() -> None:
    modals_js = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")
    harness = (
        modals_js
        + r"""
const assert = require("assert");

function makeElement() {
    return {
        hidden: false,
        textContent: "",
        className: "",
        children: [],
        style: {},
        classList: { contains: () => false, add() {}, remove() {} },
        append(...items) { this.children.push(...items); },
        appendChild(item) { this.children.push(item); return item; },
        prepend(item) { this.children.unshift(item); return item; },
        addEventListener() {},
        removeEventListener() {},
        setAttribute(name, value) { this[name] = String(value); },
        removeAttribute(name) { delete this[name]; },
        getAttribute(name) { return this[name] || null; },
        querySelector() { return null; },
    };
}

global.document = { createElement: () => makeElement() };

const img = makeElement();
img._src = "https://cdn.example/old.jpg";
Object.defineProperty(img, "src", {
    get() { return this._src || ""; },
    set(value) { this._src = value; },
});
img.removeAttribute = function(name) {
    if (name === "src") {
        this.srcRemoved = true;
        this._src = "";
        return;
    }
    delete this[name];
};
img.getAttribute = function(name) {
    if (name === "src") return this._src || null;
    return this[name] || null;
};

const elements = {
    detailTitle: makeElement(),
    detailPrice: makeElement(),
    detailLink: makeElement(),
    detailAiBlock: makeElement(),
    detailAiContent: makeElement(),
    detailDescription: makeElement(),
    detailProfitBlock: makeElement(),
    detailLiquidity: makeElement(),
    detailLiquidityBlock: makeElement(),
    detailMeta: makeElement(),
    detailMainImage: img,
    detailNoImage: makeElement(),
    detailMedia: makeElement(),
    detailThumbs: makeElement(),
    detailParams: makeElement(),
    detailParamsBlock: makeElement(),
    detailSeller: makeElement(),
    detailSellerBlock: makeElement(),
    detailAddWatchlistButton: makeElement(),
    detailModal: makeElement(),
};
elements.detailModal.hidden = true;
elements.detailModal.querySelector = () => ({ scrollTop: 10 });

const state = {
    detail: {
        data: {
            title: "Игровая консоль",
            price: 100,
            images: ["https://img.example/photo.jpg"],
            parameters: [],
            seller_fields: [],
        },
        imageIndex: 0,
        fromWatchlist: false,
        ai: {},
    },
    misc: {},
};
let proxyCalled = "";
let proxyResolve;
const renderer = createRenderModals({
    state,
    elements,
    actions: {
        fetchProxyImageObjectUrl(url) {
            proxyCalled = url;
            return new Promise((resolve) => { proxyResolve = resolve; });
        },
    },
    formatPrice: () => "100 BYN",
    formatCondition: (value) => value,
    formatSeller: (value) => value,
    formatDelta: (value) => value,
    formatDate: (value) => value,
    trapFocus: () => {},
    safeKufarUrl: () => "",
    safeImageUrl: (url) => url,
    optimizedImage: (url, options = {}) => options.useProxy ? `${url}?proxy` : `${url}?direct`,
    safeRender: (_label, fn) => fn(),
    escapeHtml: (value) => String(value),
    domClear: (el) => { el.children = []; },
    openModalAnimated: (el) => { el.hidden = false; },
    closeModalAnimated: (el) => { el.hidden = true; },
    attachPinchZoom: () => ({ reset() {} }),
});

(async () => {
    renderer.renderDetailModal();
    assert.strictEqual(proxyCalled, "https://img.example/photo.jpg?proxy");
    assert.strictEqual(img.src, "https://img.example/photo.jpg?direct");
    assert.strictEqual(img.srcRemoved, undefined);
    proxyResolve("blob:proxied-photo");
    await Promise.resolve();
    assert.strictEqual(img.src, "blob:proxied-photo");
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_listing_and_detail_duplicate_actions_render_disabled_labels() -> None:
    builders_js = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    modals_js = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")
    harness = (
        builders_js
        + "\n"
        + modals_js
        + r"""
const assert = require("assert");

class FakeNode {
    constructor(tagName) {
        this.tagName = tagName;
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.className = "";
        this.textContent = "";
        this.hidden = false;
        this.disabled = false;
        this.style = {};
        this.classList = { contains: () => false, add() {}, remove() {}, toggle() {} };
    }
    appendChild(child) { this.children.push(child); return child; }
    append(...items) { this.children.push(...items); }
    prepend(item) { this.children.unshift(item); return item; }
    replaceChildren(...items) { this.children = [...items]; }
    addEventListener() {}
    removeEventListener() {}
    setAttribute(name, value) {
        this.attributes[name] = value === "" ? "" : String(value);
        if (name === "disabled") this.disabled = true;
    }
    removeAttribute(name) {
        delete this.attributes[name];
        if (name === "disabled") this.disabled = false;
    }
    getAttribute(name) { return this.attributes[name] || null; }
    querySelector() { return null; }
}

global.Node = FakeNode;
global.document = {
    createElement: (tagName) => new FakeNode(tagName),
    createDocumentFragment: () => new FakeNode("#fragment"),
    createTextNode: (text) => {
        const node = new FakeNode("#text");
        node.textContent = String(text);
        return node;
    },
};

function appendChild(target, child) {
    if (child == null || child === false) return;
    if (Array.isArray(child)) {
        for (const nested of child) appendChild(target, nested);
        return;
    }
    if (child instanceof FakeNode) {
        target.appendChild(child);
        return;
    }
    target.appendChild(document.createTextNode(String(child)));
}

function domEl(tagName, options, ...children) {
    const node = document.createElement(tagName);
    const config = options || {};
    if (config.className) node.className = config.className;
    if (config.text != null) node.textContent = String(config.text);
    if (config.hidden != null) node.hidden = Boolean(config.hidden);
    if (config.type) node.type = config.type;
    if (config.attrs) {
        for (const [name, value] of Object.entries(config.attrs)) {
            if (value == null || value === false) continue;
            node.setAttribute(name, value === true ? "" : String(value));
        }
    }
    if (config.dataset) {
        for (const [name, value] of Object.entries(config.dataset)) {
            if (value != null) node.dataset[name] = String(value);
        }
    }
    for (const child of children) appendChild(node, child);
    return node;
}

function domFragment(...children) {
    const fragment = document.createDocumentFragment();
    for (const child of children) appendChild(fragment, child);
    return fragment;
}

function findByRole(root, role) {
    if (root.dataset?.role === role) return root;
    for (const child of root.children || []) {
        const found = findByRole(child, role);
        if (found) return found;
    }
    return null;
}

const state = {
    leads: { items: [{ ad_id: "lead-1", status: "new" }] },
    watchlist: { items: [{ ad_id: "watch-1" }] },
    misc: { stats: {} },
    detail: { data: null, imageIndex: 0, fromWatchlist: false, ai: {} },
};
const menus = [];
const builders = createRenderCardBuilders({
    state,
    elements: {},
    actions: { addLeadFromListing() {}, addWatchlistFromListing() {} },
    formatPrice: () => "100 BYN",
    formatCondition: (value) => value,
    formatSeller: (value) => value,
    formatDelta: (value) => value,
    deltaClass: () => "",
    hasTelegramInitData: () => true,
    safeKufarUrl: () => "",
    openExternalLink() {},
    safeImageUrl: () => "",
    optimizedImage: (url) => url,
    escapeHtml: (value) => String(value),
    domEl,
    domFragment,
    attachLongPress: (_node, menuBuilder) => menus.push(menuBuilder()),
});

const leadListing = builders.buildListingNode(
    { ad_id: "lead-1", title: "Консоль", price: 100 },
    () => "neutral",
);
assert.strictEqual(findByRole(leadListing, "lead").textContent, "В покупках");
assert.strictEqual(findByRole(leadListing, "lead").disabled, true);
assert.strictEqual(findByRole(leadListing, "watch").textContent, "В избранное");
assert.strictEqual(findByRole(leadListing, "watch").disabled, true);
assert.strictEqual(menus[0][0].label, "В покупках");
assert.strictEqual(menus[0][0].disabled, true);
assert.strictEqual(menus[0][1].label, "В избранное");
assert.strictEqual(menus[0][1].disabled, true);

const watchListing = builders.buildListingNode(
    { ad_id: "watch-1", title: "Ноутбук", price: 200 },
    () => "neutral",
);
assert.strictEqual(findByRole(watchListing, "lead").textContent, "В покупки");
assert.strictEqual(findByRole(watchListing, "lead").disabled, false);
assert.strictEqual(findByRole(watchListing, "watch").textContent, "В избранном");
assert.strictEqual(findByRole(watchListing, "watch").disabled, true);
assert.strictEqual(menus[1][1].label, "В избранном");
assert.strictEqual(menus[1][1].disabled, true);

const elements = {
    detailTitle: new FakeNode("div"),
    detailPrice: new FakeNode("div"),
    detailLink: new FakeNode("a"),
    detailAiBlock: new FakeNode("div"),
    detailAiContent: new FakeNode("div"),
    detailDescription: new FakeNode("div"),
    detailProfitBlock: new FakeNode("div"),
    detailLiquidity: new FakeNode("div"),
    detailLiquidityBlock: new FakeNode("div"),
    detailMeta: new FakeNode("div"),
    detailMainImage: new FakeNode("img"),
    detailNoImage: new FakeNode("div"),
    detailMedia: new FakeNode("div"),
    detailThumbs: new FakeNode("div"),
    detailParams: new FakeNode("div"),
    detailParamsBlock: new FakeNode("div"),
    detailSeller: new FakeNode("div"),
    detailSellerBlock: new FakeNode("div"),
    detailAddLeadButton: new FakeNode("button"),
    detailAddWatchlistButton: new FakeNode("button"),
    detailModal: new FakeNode("div"),
};
elements.detailModal.hidden = true;
elements.detailModal.querySelector = () => ({ scrollTop: 0 });
const renderer = createRenderModals({
    state,
    elements,
    actions: {},
    formatPrice: () => "100 BYN",
    formatCondition: (value) => value,
    formatSeller: (value) => value,
    formatDelta: (value) => value,
    formatDate: (value) => value,
    trapFocus: () => {},
    safeKufarUrl: () => "",
    safeImageUrl: () => "",
    optimizedImage: (url) => url,
    safeRender: (_label, fn) => fn(),
    escapeHtml: (value) => String(value),
    domClear: (node) => { node.children = []; },
    openModalAnimated: (node) => { node.hidden = false; },
    closeModalAnimated: (node) => { node.hidden = true; },
    attachPinchZoom: () => ({ reset() {} }),
});

state.detail.data = {
    ad_id: "lead-1",
    title: "Консоль",
    price: 100,
    images: [],
    parameters: [],
    seller_fields: [],
};
renderer.renderDetailModal();
assert.strictEqual(elements.detailAddLeadButton.textContent, "В покупках");
assert.strictEqual(elements.detailAddLeadButton.disabled, true);
assert.strictEqual(elements.detailAddWatchlistButton.textContent, "В избранное");
assert.strictEqual(elements.detailAddWatchlistButton.disabled, true);

state.detail.data = {
    ad_id: "watch-1",
    title: "Ноутбук",
    price: 200,
    images: [],
    parameters: [],
    seller_fields: [],
};
state.detail.fromWatchlist = true;
renderer.renderDetailModal();
assert.strictEqual(elements.detailAddLeadButton.textContent, "В покупки");
assert.strictEqual(elements.detailAddLeadButton.disabled, false);
assert.strictEqual(elements.detailAddWatchlistButton.hidden, false);
assert.strictEqual(elements.detailAddWatchlistButton.textContent, "В избранном");
assert.strictEqual(elements.detailAddWatchlistButton.disabled, true);
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_duplicate_action_state_covers_secondary_surfaces() -> None:
    builders_js = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    modals_js = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")
    trackers_js = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")
    events_js = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")
    css = _read_all_css()

    assert "_buildWatchlistActions" in builders_js
    assert "const inLeads = _isActiveLeadAd(item.ad_id)" in builders_js
    assert "text: leadText" in builders_js
    assert "if (event.currentTarget.disabled) return;" in builders_js

    assert "_setDetailActionButton(" in modals_js
    assert "detailAddWatchlistButton.hidden = state.detail.fromWatchlist" not in modals_js
    assert "collectionState.inWatchlist" in modals_js

    assert "const inLeads = _isActiveLeadAd(event.ad_id)" in trackers_js
    assert 'text: leadText' in trackers_js
    assert "if (button.disabled) return;" in trackers_js

    assert "detailAddLeadButton.disabled" in events_js
    assert "detailAddWatchlistButton.disabled" in events_js
    assert "Promise.resolve(actions.addLeadFromListing" in (JS_DIR / "render_cards.js").read_text(
        encoding="utf-8",
    )

    action_refresh_start = actions_js.index("function _refreshCollectionActionSurfaces")
    action_refresh = actions_js[
        action_refresh_start:
        actions_js.index("\n    function _validVersion", action_refresh_start)
    ]
    watch_refresh_start = watchlist_js.index("function refreshCollectionActionSurfaces")
    watch_refresh = watchlist_js[
        watch_refresh_start:
        watchlist_js.index("\n    // Tracks", watch_refresh_start)
    ]
    assert "markDirty('listings'" not in action_refresh
    assert "renderAll();" not in action_refresh
    assert "markDirty('listings'" not in watch_refresh
    assert "renderAll();" not in watch_refresh

    for selector in (
        ".listing-btn:disabled",
        ".wl-btn:disabled",
        ".lead-btn:disabled",
        ".lp-menu-item:disabled",
    ):
        assert selector in css


def test_external_kufar_links_open_outside_telegram_webview() -> None:
    core_js = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    events_js = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    cards_js = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    modals_js = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")

    assert '"auto.kufar.by"' in core_js
    assert 'parsed.pathname.startsWith("/vi/")' in core_js
    assert "function openExternalLink" in core_js
    assert "tg.openLink(href, { try_browser: true })" in core_js
    assert "window.open(href, \"_blank\", \"noopener,noreferrer\")" in core_js

    assert 'event.target?.closest?.("a[target=\'_blank\']")' in events_js
    assert 'link?.getAttribute("href")' in events_js
    assert "openExternalLink(rawHref)" in events_js
    assert "window.Telegram.WebApp.openLink(link.href)" not in events_js

    assert "openExternalLink," in cards_js
    assert "openExternalLink(link)" in cards_js
    assert "window.Telegram.WebApp.openLink(link)" not in cards_js

    assert 'elements.detailLink.removeAttribute("href")' in modals_js
    assert 'elements.detailLink.setAttribute("aria-disabled", "true")' in modals_js


def test_image_proxy_fetch_uses_telegram_headers_and_object_url_cache() -> None:
    image_proxy_js = (JS_DIR / "api_image_proxy.js").read_text(encoding="utf-8")
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    harness = (
        r"""
function noop() {}
function stubModule() { return new Proxy({}, { get: () => noop }); }
function createApiCore() {
    return {
        telegramHeaders: () => ({ "X-Telegram-Init-Data": "signed-init-data" }),
        requestJson: noop,
        getJson: noop,
        postJson: noop,
        deleteJson: noop,
        buildCommonQuery: noop,
    };
}
const createApiListings = stubModule;
const createApiTrackers = stubModule;
const createApiLeads = stubModule;
const createApiWatchlist = stubModule;
function createApiEvents() { return { bindEvents: noop }; }
const domEl = noop;
const domClear = noop;
const domFragment = noop;
const openModalAnimated = noop;
const closeModalAnimated = noop;
const bindRovingTablist = noop;
const _prefersReducedMotion = () => true;
globalThis.window = { location: { hostname: "app.example", protocol: "https:" }, App: {} };
globalThis.localStorage = { length: 0, key: () => null, removeItem: noop };
const revoked = [];
globalThis.URL = {
    createObjectURL: () => "blob:proxy-1",
    revokeObjectURL: (url) => revoked.push(url),
};
const fetchCalls = [];
globalThis.fetch = async (url, options) => {
    fetchCalls.push({ url, options });
    return { ok: true, blob: async () => ({}) };
};
"""
        + image_proxy_js
        + actions_js
        + r"""
(async () => {
    const actions = createAppActions({
        state: { search: { recentSearches: [] }, misc: {} },
        elements: { views: {} },
        markDirty: noop,
        renderAll: noop,
        showToast: noop,
    });
    const url = "/api/v1/img/ad/photo.jpg?w=800";
    const first = await actions.fetchProxyImageObjectUrl(url);
    const second = await actions.fetchProxyImageObjectUrl(url);
    if (first !== "blob:proxy-1" || second !== first) throw new Error("bad object URL cache");
    if (fetchCalls.length !== 1) throw new Error("proxy image should be fetched once");
    const headers = fetchCalls[0].options.headers;
    if (headers["X-Telegram-Init-Data"] !== "signed-init-data") {
        throw new Error("missing Telegram initData header");
    }
    if (!headers.Accept.includes("image/avif") || !headers.Accept.includes("image/webp")) {
        throw new Error("missing modern image Accept header");
    }
    actions.clearProxyImageObjectUrls();
    if (revoked[0] !== "blob:proxy-1") throw new Error("object URL was not revoked");
    try {
        await actions.fetchProxyImageObjectUrl("https://evil.example/a.jpg");
        throw new Error("external proxy URL accepted");
    } catch (err) {
        if (!String(err.message).includes("Invalid image proxy URL")) throw err;
    }
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_inert_walk_handles_nested_modals() -> None:
    """Wave 25.3 regression test: _applyInertToSiblings must walk DOWN
    from <body> to the modal, inerting siblings at each level. The
    Wave 22 version assumed modals were direct children of <body>,
    but in this codebase they live inside ``<div class="app">``.
    The broken version inerted ``<div class="app">`` and the
    inheritance propagated to the modal itself — symptom was
    no scroll / no clicks inside any modal.

    This test runs the actual JS function against a minimal DOM
    mock through Node, exercising both the flat case (modal as
    body child) and the nested case (modal inside #app-root).
    """
    helpers_src = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    # Extract just the two inert functions so the runner doesn't need
    # to load the rest of dom_helpers.js (which references browser-
    # specific APIs we don't mock).
    apply_match = re.search(
        r"function _applyInertToSiblings\(modalEl\)\s*\{.*?\n\}\n",
        helpers_src, re.DOTALL,
    )
    restore_match = re.search(
        r"function _restoreInertSiblings\(modalEl\)\s*\{.*?\n\}\n",
        helpers_src, re.DOTALL,
    )
    assert apply_match and restore_match, "inert helpers not found"

    harness = r"""
// Minimal element mock supporting the operations the inert helpers use.
function makeEl(name) {
    const el = {
        _name: name,
        _parent: null,
        children: [],
        _attrs: new Map(),
        isConnected: true,
        setAttribute(k, v) { this._attrs.set(k, v); },
        removeAttribute(k) { this._attrs.delete(k); },
        hasAttribute(k) { return this._attrs.has(k); },
        contains(other) {
            if (other === this) return true;
            for (const c of this.children) if (c.contains(other)) return true;
            return false;
        },
        _appendChild(child) { child._parent = this; this.children.push(child); },
    };
    return el;
}

function isInert(el) { return el.hasAttribute("inert"); }

// __APPLY__
// __RESTORE__

// ── Case 1: flat (modal is direct body child) ─────────────────────────
{
    const body = makeEl("body");
    const offline = makeEl("offline");
    const ptr = makeEl("ptr");
    const modal = makeEl("modal");
    body._appendChild(offline);
    body._appendChild(ptr);
    body._appendChild(modal);
    globalThis.document = { body };

    _applyInertToSiblings(modal);
    if (isInert(modal)) throw new Error("flat: modal must not be inert");
    if (!isInert(offline)) throw new Error("flat: offline must be inert");
    if (!isInert(ptr)) throw new Error("flat: ptr must be inert");

    _restoreInertSiblings(modal);
    if (isInert(offline) || isInert(ptr)) throw new Error("flat: restore failed");
}

// ── Case 2: nested (modal inside #app-root, matching real index.html) ─
{
    const body = makeEl("body");
    const offline = makeEl("offline");
    const ptr = makeEl("ptr");
    const appRoot = makeEl("appRoot");
    const header = makeEl("header");
    const main = makeEl("main");
    const modal = makeEl("modal");
    body._appendChild(offline);
    body._appendChild(ptr);
    body._appendChild(appRoot);
    appRoot._appendChild(header);
    appRoot._appendChild(main);
    appRoot._appendChild(modal);
    globalThis.document = { body };

    _applyInertToSiblings(modal);
    if (isInert(modal)) throw new Error("nested: modal itself must not be inert");
    if (isInert(appRoot)) throw new Error("nested: app-root must not be inert (it's on the path)");
    if (!isInert(offline)) throw new Error("nested: offline must be inert");
    if (!isInert(ptr)) throw new Error("nested: ptr must be inert");
    if (!isInert(header)) throw new Error("nested: header must be inert");
    if (!isInert(main)) throw new Error("nested: main must be inert");

    _restoreInertSiblings(modal);
    if (isInert(offline) || isInert(ptr) || isInert(header) || isInert(main)) {
        throw new Error("nested: restore must un-inert everything we set");
    }
}

// ── Case 3: nested modal opened over another modal ────────────────────
{
    const body = makeEl("body");
    const appRoot = makeEl("appRoot");
    const header = makeEl("header");
    const outer = makeEl("outer");
    const inner = makeEl("inner");
    body._appendChild(appRoot);
    appRoot._appendChild(header);
    appRoot._appendChild(outer);
    appRoot._appendChild(inner);  // sibling of outer at app-root level
    globalThis.document = { body };

    _applyInertToSiblings(outer);
    if (!isInert(inner)) throw new Error("stacked: inner must be inert after outer opens");
    if (!isInert(header)) throw new Error("stacked: header must be inert");
    if (isInert(outer)) throw new Error("stacked: outer must not be inert");

    _applyInertToSiblings(inner);
    if (isInert(inner)) throw new Error("stacked: inner must not be inert after IT opens (lift)");
    if (!isInert(outer)) throw new Error("stacked: outer stays inert (it's behind inner)");
    if (!isInert(header)) throw new Error("stacked: header stays inert");

    _restoreInertSiblings(inner);
    // Inner closing restores the state-before-inner-opened: inner was
    // inert (from outer's sweep), outer was active (it was the open
    // modal), header was inert.
    if (!isInert(inner)) throw new Error("stacked: closing inner re-applies inert it lifted");
    if (isInert(outer)) throw new Error("stacked: outer active modal is inert");
    if (!isInert(header)) throw new Error("stacked: header stays inert (outer is still open)");

    _restoreInertSiblings(outer);
    if (isInert(inner) || isInert(outer) || isInert(header)) {
        throw new Error("stacked: closing outer un-inerts everything");
    }
}

console.log("OK");
"""
    harness = harness.replace("// __APPLY__", apply_match.group(0))
    harness = harness.replace("// __RESTORE__", restore_match.group(0))

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
        tmp.write(harness)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=15,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    assert result.returncode == 0, (
        f"inert behaviour test failed:\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "OK" in result.stdout, f"unexpected output: {result.stdout!r}"


def test_focus_trap_recomputes_focusable_elements() -> None:
    helpers_src = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    trap_match = re.search(
        r"function trapFocus\(container\)\s*\{.*?\n\}\n\nfunction domAppend",
        helpers_src,
        re.DOTALL,
    )
    assert trap_match, "trapFocus helper not found"

    trap_src = trap_match.group(0).removesuffix("\n\nfunction domAppend")
    harness = f"""
globalThis.document = {{ activeElement: null }};
{trap_src}

function focusable(name) {{
    return {{
        name,
        focus() {{ document.activeElement = this; }},
    }};
}}

const first = focusable("first");
const second = focusable("second");
const third = focusable("third");
let focusables = [first, second];
let keydownHandler = null;
const container = {{
    querySelectorAll() {{ return focusables; }},
    addEventListener(type, handler) {{
        if (type === "keydown") keydownHandler = handler;
    }},
    removeEventListener() {{}},
}};

trapFocus(container);
if (document.activeElement !== first) throw new Error("trapFocus must focus first item");
focusables = [first, second, third];
document.activeElement = third;
let prevented = false;
keydownHandler({{
    key: "Tab",
    shiftKey: false,
    preventDefault() {{ prevented = true; }},
}});
if (!prevented) throw new Error("Tab on dynamically-added last item must be trapped");
if (document.activeElement !== first) throw new Error("Tab should wrap to recomputed first item");
"""
    subprocess.run(["node", "-e", harness], check=True, text=True)


def test_secondary_tablists_share_roving_keyboard_helper() -> None:
    helpers = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    events = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    listing_assistant = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")
    bundle_script = Path("scripts/build_frontend_bundle.sh").read_text(encoding="utf-8")

    assert "function bindRovingTablist" in helpers
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert key in helpers
    assert "bindRovingTablist(tablist)" in events
    assert "bindRovingTablist(elements.itemsFilterRow)" in events
    assert "app.bindRovingTablist" in listing_assistant
    assert "bindRovingTablist," in bundle_script


def test_scroll_section_into_view_respects_reduced_motion() -> None:
    actions = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    assert "behavior: _prefersReducedMotion() ? \"auto\" : \"smooth\"" in actions
    assert 'section?.scrollIntoView({ behavior: "smooth"' not in actions


def test_no_modal_close_bypasses_close_modal_animated() -> None:
    """Wave 25.5 regression test: every modal close path must go through
    ``closeModalAnimated`` (or a wrapper that ultimately does) — never
    directly set ``modalEl.hidden = true``. The direct-hidden shortcut
    skips ``unlockBodyScroll``, the focus-trap cleanup, and the
    ``_restoreInertSiblings`` call, leaving the page scroll-locked
    and every background element ``inert``. User-visible symptom: UI
    appears frozen, no clicks register anywhere.

    The bug that triggered this test: the global Escape handler in
    api_events.js had a fast-path for the listing-assistant modal
    that did ``laModal.hidden = true`` directly. The listing-assistant
    module already had its OWN Escape handler that called
    ``closeModal()`` correctly; the duplicate global handler did its
    direct-hide first and the local handler then no-op'd (because
    modal was already hidden=true), leaving cleanup undone.

    This test scans api_events.js for any ``hidden = true`` set on a
    DOM element variable that LOOKS like a modal reference.
    """
    events_src = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    # Strip line comments so commented-out historical examples don't
    # trigger the matcher.
    lines = [
        ln for ln in events_src.splitlines()
        if not ln.strip().startswith("//")
    ]
    stripped_src = "\n".join(lines)
    bad_matches = re.findall(
        r"\b([a-zA-Z_]\w*[Mm]odal)\.hidden\s*=\s*true",
        stripped_src,
    )
    assert not bad_matches, (
        f"api_events.js sets ``.hidden = true`` directly on modals: "
        f"{sorted(set(bad_matches))}. Use the matching closeXModal() "
        f"helper instead — direct-hidden bypasses scroll-lock release "
        f"and the inert/focus-trap cleanup."
    )


def test_close_ai_modal_cancels_polling() -> None:
    """closeAIModal used to leave the AI polling loop running for up to 6
    minutes, blocking re-opening AI Analysis. Verify the cancel-by-session
    pattern is wired up so a regression here is caught statically.

    UX-M8 (Wave 25): api_ai.js was split — closeAIModal lives in
    api_ai_modal.js and the session counter is now aiCtx.pollSession
    (shared mutable state owned by the orchestrator)."""
    modal_text = (JS_DIR / "api_ai_modal.js").read_text(encoding="utf-8")
    orchestrator_text = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")

    assert "aiCtx.pollSession" in modal_text, (
        "session counter for poll cancellation is missing from api_ai_modal.js"
    )
    assert "aiCtx.pollSession" in orchestrator_text, (
        "orchestrator must read aiCtx.pollSession for isCancelled"
    )

    # closeAIModal in the modal module must bump the session and
    # release the loading slot.
    close_block = re.search(r"function closeAIModal\(\)\s*\{[^}]+\}", modal_text, re.DOTALL)
    assert close_block, "closeAIModal definition not found in api_ai_modal.js"
    body = close_block.group(0)
    assert "aiCtx.pollSession" in body, "closeAIModal must invalidate the poll session"
    assert "aiCtx.loading = false" in body, "closeAIModal must release aiCtx.loading"


def test_load_leads_and_watchlist_have_stale_response_guard() -> None:
    """Rapid tab toggling (Покупки ↔ Избранное) used to issue overlapping
    loadLeads()/loadWatchlist() — a stale response could resolve last
    and overwrite a fresher state. Both loaders must compare the request
    id captured at start against the latest one before assigning state."""
    leads_js = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")

    # state holds the monotonic counters; their names are referenced by the loaders.
    assert "_requestId" in leads_js
    assert "_requestId" in watchlist_js
    # Each loader bumps and then checks the counter before writing state.
    assert "state.leads._requestId" in leads_js
    assert "if (requestId !== state.leads._requestId)" in leads_js
    assert "state.watchlist._requestId" in watchlist_js
    assert "if (requestId !== state.watchlist._requestId)" in watchlist_js


def test_watchlist_reload_does_not_flash_empty_state() -> None:
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")
    render_cards_js = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")

    load_start = watchlist_js.index("async function loadWatchlist")
    load_body = watchlist_js[load_start:watchlist_js.index("\n    // ── Clear", load_start)]
    loading_start = load_body.index("state.watchlist._loading = true")
    stale_guard_start = load_body.index("// Stale-response guard")
    assert "state.watchlist.items = []" not in load_body[loading_start:stale_guard_start]

    delete_start = watchlist_js.index("async function deleteWatchlistItem")
    delete_end = watchlist_js.index("\n    // ── Delete all", delete_start)
    delete_body = watchlist_js[delete_start:delete_end]
    assert delete_body.index("_nextWatchlistRequestId();") < delete_body.index(
        "state.watchlist.items ="
    )
    delete_success_body = delete_body.split("} catch", 1)[0]
    assert "await loadWatchlist()" not in delete_success_body
    assert delete_body.index("state.watchlist._loading = false") < delete_body.index(
        "refreshAfterWatchlistChange();"
    )

    render_start = render_cards_js.index("function renderLeads")
    render_end = render_cards_js.index("\n    /* ===== Watchlist", render_start)
    render_body = render_cards_js[render_start:render_end]
    loading_empty_guard = 'filter === "watching" && state.watchlist._loading && !entries.length'
    assert loading_empty_guard in render_body
    assert render_body.index(loading_empty_guard) < render_body.index("if (!entries.length)")
    assert "_buildSkeletonCard()" in render_body


def test_watchlist_cards_do_not_attach_horizontal_swipe() -> None:
    builder_text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "_attachSwipeReveal" not in builder_text
    assert "swipe-btn" not in builder_text
    assert 'card.addEventListener("touchstart"' not in builder_text
    assert 'card.addEventListener("touchmove"' not in builder_text


def test_deals_empty_state_has_no_icon_box() -> None:
    render_cards_js = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    empty_start = render_cards_js.index("function buildItemsEmpty")
    purchases_start = render_cards_js.index('title: "Нет сделок в работе"', empty_start)
    purchases_block_start = render_cards_js.rindex(
        "return buildEmpty({",
        empty_start,
        purchases_start,
    )
    purchases_block_end = render_cards_js.index("});", purchases_start)
    purchases_block = render_cards_js[purchases_block_start:purchases_block_end]
    assert "icon:" not in purchases_block


def test_tracker_empty_state_has_no_icon_box() -> None:
    trackers_js = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")
    assert 'icon: "trackers"' not in trackers_js
    empty_start = trackers_js.index("if (!state.trackers.items.length)")
    title_start = trackers_js.index('title: "Создайте первый автопоиск"', empty_start)
    block_start = trackers_js.rindex(
        "buildEmpty({",
        empty_start,
        title_start,
    )
    block_end = trackers_js.index("});", title_start)
    empty_block = trackers_js[block_start:block_end]
    assert "icon:" not in empty_block


def test_empty_states_do_not_render_decorative_icon_boxes() -> None:
    render_core_js = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    render_cards_js = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    render_trackers_js = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")
    layout_css = (CSS_DIR / "parts" / "layout.css").read_text(encoding="utf-8")

    assert "_EMPTY_STATE_ICONS" not in render_core_js
    assert "empty-state-icon" not in render_core_js
    assert "empty-state-icon" not in layout_css
    assert 'icon: "listings"' not in render_cards_js
    assert 'icon: "watchlist"' not in render_cards_js
    assert 'icon: "events"' not in render_trackers_js


def test_lead_and_watchlist_mutations_have_inflight_guard() -> None:
    """A double-click on "В покупки" / "В избранное" used to fire two
    POST /api/v1/leads or /api/v1/watchlist round-trips because state
    hadn't reloaded between clicks. Each mutation must short-circuit
    when the same ad/row is already mid-flight."""
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")

    assert "_inflightAdMutations" in actions_js
    assert "_inflightAdMutations.has(item.ad_id)" in actions_js

    assert "_inflightAd" in watchlist_js
    assert "_inflightWatchId" in watchlist_js
    # promoteWatchlistToLead and deleteWatchlistItem both guard by row id.
    assert "_inflightWatchId.has(item.id)" in watchlist_js
    assert "_inflightWatchId.has(watchlistId)" in watchlist_js


def test_lead_and_watchlist_mutations_resolve_versions_before_patch() -> None:
    leads_js = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    core_js = (JS_DIR / "api_core.js").read_text(encoding="utf-8")

    assert "async function _resolveLeadVersion" in leads_js
    for fn_name in (
        "confirmLead",
        "closeDeal",
        "revertLeadStage",
        "updateLeadMeta",
        "markLeadAsSold",
    ):
        fn_start = leads_js.index(f"function {fn_name}")
        fn_body = leads_js[fn_start:leads_js.index("\n    // ──", fn_start + 1)]
        assert "_resolveLeadVersion" in fn_body, f"{fn_name} can PATCH without fresh version"
        assert "version," in fn_body, f"{fn_name} must send resolved version"

    assert "async function _resolveWatchlistVersion" in watchlist_js
    for fn_name in ("updateWatchlistMeta", "promoteWatchlistToLead"):
        fn_start = watchlist_js.index(f"function {fn_name}")
        fn_body = watchlist_js[fn_start:watchlist_js.index("\n    // ──", fn_start + 1)]
        assert "_resolveWatchlistVersion" in fn_body, f"{fn_name} can PATCH without version"
        assert "version," in fn_body, f"{fn_name} must send resolved version"

    add_lead_start = actions_js.index("async function addLeadFromListing")
    add_lead_body = actions_js[add_lead_start:actions_js.index("\n    // Make", add_lead_start)]
    assert "_resolveWatchingVersion" in add_lead_body
    assert "_resolveWatchingItem" in add_lead_body
    assert "version," in add_lead_body
    assert "Lead version is required for updates" in core_js
    assert "Данные устарели" in core_js


def test_consent_modal_cancel_emits_toast() -> None:
    """FE-08: tapping "Отмена" on the AI consent modal used to be a
    silent close — the rejection bubbled back as a swallowed
    `consent_denied` and the user got zero feedback that AI features
    were now blocked. The Cancel handler must call ``showToast`` with
    a message about consent so the dismissal is acknowledged before
    the promise rejects."""
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")

    # Locate the function the Cancel button is wired to.
    on_cancel_match = re.search(
        r"function onCancel\(\)\s*\{(?P<body>[^}]+)\}",
        actions_js,
        re.DOTALL,
    )
    assert on_cancel_match, "onCancel() not found in app_actions.js"
    body = on_cancel_match.group("body")
    # Must hide the modal AND inform the user before rejecting.
    assert "modal.hidden = true" in body
    assert "showToast(" in body, (
        "onCancel must show a toast when the user dismisses the consent gate"
    )
    assert "AI" in body or "согласие" in body.lower(), (
        "the toast text should mention AI/consent so the user knows why"
    )
    assert 'reject(new Error("consent_denied"))' in body


def test_ai_consent_status_errors_fail_closed_outside_local_debug() -> None:
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    harness = (
        r"""
let getJsonImpl = async () => { throw new Error("offline"); };
function noop() {}
function stubModule() { return new Proxy({}, { get: () => noop }); }
function createApiCore() {
    return {
        telegramHeaders: () => ({}),
        requestJson: noop,
        getJson: (...args) => getJsonImpl(...args),
        postJson: noop,
        deleteJson: noop,
        buildCommonQuery: noop,
    };
}
const createApiListings = stubModule;
const createApiTrackers = stubModule;
const createApiLeads = stubModule;
const createApiWatchlist = stubModule;
function createApiEvents() { return { bindEvents: noop }; }
function createImageProxyLoader() {
    return {
        fetchProxyImageObjectUrl: noop,
        clearProxyImageObjectUrls: noop,
    };
}
const domEl = noop;
const domClear = noop;
const domFragment = noop;
const openModalAnimated = noop;
const closeModalAnimated = noop;
const bindRovingTablist = noop;
const _prefersReducedMotion = () => true;
globalThis.localStorage = { length: 0, key: () => null, removeItem: noop };
"""
        + actions_js
        + r"""
async function run(hostname, protocol) {
    const toasts = [];
    globalThis.window = {
        location: { hostname, protocol },
        App: {},
    };
    const actions = createAppActions({
        state: { search: { recentSearches: [] }, misc: {} },
        elements: { views: {} },
        markDirty: noop,
        renderAll: noop,
        showToast: (...args) => toasts.push(args),
    });
    try {
        await actions.checkAiConsent();
        return { ok: true, toasts };
    } catch (err) {
        return { ok: false, message: err.message, toasts };
    }
}

(async () => {
    const prod = await run("app.example", "https:");
    if (prod.ok) throw new Error("production consent status errors must fail closed");
    if (prod.message !== "consent_check_failed") throw new Error("wrong prod error");
    if (prod.toasts.length !== 1) throw new Error("missing production toast");
    if (!String(prod.toasts[0][0]).includes("согласие")) {
        throw new Error("toast must mention consent");
    }

    const local = await run("localhost", "http:");
    if (!local.ok) throw new Error("localhost debug flow should keep working");
    if (local.toasts.length) throw new Error("localhost debug flow should not show error toast");
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
"""
    )
    result = subprocess.run(["node"], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_consent_save_failure_does_not_unlock_ai() -> None:
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    on_accept_match = re.search(
        r"async function onAccept\(\)\s*\{(?P<body>.*?)\n        \}",
        actions_js,
        re.DOTALL,
    )
    assert on_accept_match, "onAccept() not found in app_actions.js"
    body = on_accept_match.group("body")
    catch_start = body.index("} catch (err) {")
    cleanup_start = body.index("cleanup();")
    assert "showToast(err.message || \"Не удалось сохранить согласие\")" in body
    assert "return;" in body[catch_start:cleanup_start], (
        "failed consent persistence must not resolve the AI gate"
    )


def test_app_init_does_not_show_ai_consent_on_startup() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    init_start = app_js.index("function init()")
    init_body = app_js[init_start:app_js.index("\n    // Detach listeners", init_start)]
    assert "actions.checkAiConsent" not in init_body


def test_all_ai_intent_paths_check_ai_consent_lazily() -> None:
    ai_js = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")
    listing_js = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")

    ai_start = ai_js.index("async function loadAIAnalysis")
    ai_body = ai_js[ai_start:ai_js.index("\n    /** Hybrid error UI", ai_start)]
    assert "context.checkAiConsent" in ai_body

    submit_start = listing_js.index("async function handleSubmit")
    submit_body = listing_js[submit_start:listing_js.index("\n    // ── Wire DOM", submit_start)]
    assert "checkAiConsent" in submit_body
    assert submit_body.index("await checkAiConsent();") < submit_body.index(
        'postJson("/api/v1/ai/listing-assistant"'
    )


def test_delete_account_clears_local_data_and_never_reloads() -> None:
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    clear_start = actions_js.index("function clearLocalAccountData()")
    clear_body = actions_js[clear_start:actions_js.index("\n    /**", clear_start)]
    assert '"recentSearches"' in clear_body
    assert 'key.startsWith("rafuk:")' in clear_body
    assert "localStorage.removeItem(key)" in clear_body
    assert "state.search.recentSearches = []" in clear_body
    assert "state.misc.listingAssistantResult = null" in clear_body

    delete_start = actions_js.index("async function deleteAccount()")
    delete_body = actions_js[
        delete_start:actions_js.index("\n    function _showTypedConfirmDialog", delete_start)
    ]
    server_delete = delete_body.index('await core.deleteJson("/api/v1/account"')
    local_cleanup = delete_body.index("clearLocalAccountData();")
    # RV-SEC-05: a reload re-used the still-valid initData and the first
    # request re-created the deleted user; the app now closes instead
    # (behaviour: test_rv_sec05_account_deletion_closes_app_without_reprovisioning).
    finish = delete_body.index("_finishAccountDeletion();")
    assert server_delete < local_cleanup < finish
    assert "location.reload" not in delete_body


def test_collection_actions_only_show_final_toasts() -> None:
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")
    leads_js = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    listings_js = (JS_DIR / "api_listings.js").read_text(encoding="utf-8")
    trackers_js = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")

    add_lead = actions_js[
        actions_js.index("async function addLeadFromListing"):
        actions_js.index("\n    // Make", actions_js.index("async function addLeadFromListing"))
    ]
    assert 'showToast("Добавляю…", "info"' not in add_lead
    assert 'showToast("В покупках", "success"' in add_lead
    assert 'showToast("Уже в покупках", "info"' in add_lead

    for fn_name, success_text in (
        ("addWatchlistFromListing", "В избранном"),
        ("promoteWatchlistToLead", "В покупках"),
    ):
        fn_start = watchlist_js.index(f"function {fn_name}")
        fn_body = watchlist_js[fn_start:watchlist_js.index("\n    // ──", fn_start + 1)]
        assert 'showToast("Добавляю…", "info"' not in fn_body
        assert f'showToast("{success_text}", "success"' in fn_body
        assert 'showToast("Уже в покупках", "info"' in fn_body
        if fn_name == "addWatchlistFromListing":
            assert 'showToast("Уже в избранном", "info"' in fn_body

    confirm_start = leads_js.index("async function confirmLead")
    confirm_body = leads_js[confirm_start:leads_js.index("\n    // ──", confirm_start + 1)]
    assert 'showToast("Сохраняю…", "info"' not in confirm_body
    assert "_resolveLeadVersion" in confirm_body
    assert 'showToast(error.message || "Не удалось подтвердить сделку", "error")' in confirm_body
    for text in (actions_js, watchlist_js, leads_js, listings_js, trackers_js):
        assert 'showToast("Загружаю..."' not in text


def test_trackers_preserve_category_scope_in_frontend() -> None:
    api_trackers = (JS_DIR / "api_trackers.js").read_text(encoding="utf-8")
    render_trackers = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")
    app_core_dom = (JS_DIR / "app_core_dom.js").read_text(encoding="utf-8")
    app_renderers = (JS_DIR / "app_renderers.js").read_text(encoding="utf-8")

    assert "function currentCategoryPayload()" in api_trackers
    assert "category_id: categoryPayload.category_id" in api_trackers
    assert "category_label: categoryPayload.category_label" in api_trackers
    assert "populateEditCategorySelect(tracker)" in api_trackers
    assert "function editCategoryPayload()" in api_trackers
    assert 'document.getElementById("edit-category-select")' in app_core_dom
    assert "tracker.category_label || `категория ${tracker.category_id}`" in render_trackers
    assert "state.filters.category = tracker.category_id ?? null" in render_trackers
    assert 'actions.search("overview", { keepFilters: true })' in render_trackers
    assert "context._hooks.renderFilterDropdown = renderFilterDropdown" in app_renderers


def test_toasts_are_minimal_and_fast() -> None:
    render_core = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    css = _read_all_css()

    assert "toast-dot" not in render_core
    assert "toast-label" not in render_core
    assert "iconMap" not in render_core
    assert "const animationDuration = prefersReducedMotion ? 10 : 120" in render_core
    assert ".toast::before" in css
    assert ".toast-success { --toast-accent: var(--green); }" in css
    assert ".toast-error { --toast-accent: var(--red); }" in css
    assert ".toast-dot" not in css
    assert ".toast-label" not in css
    assert ".toast-icon" not in css
    assert ".toast.entering:nth-child(2)" not in css
    assert "animation: toast-in 120ms" in css
    assert "pointer-events: auto;" in css
    assert 'closeBtn.addEventListener("click", (event) => {' in render_core
    assert "event.preventDefault();" in render_core
    assert "event.stopPropagation();" in render_core


def test_toast_messages_do_not_duplicate_status_symbols() -> None:
    decorative_prefix_re = re.compile(
        r"showToast\(\s*([\"'`])[\s✓✔✅☑✕✖❌×↩←→★⭐❤🔥⚠\ufe0f]+"
    )
    for path in sorted(JS_DIR.glob("*.js")):
        if path.name == "app_bundle.js" or path.parent.name == "vendor":
            continue
        text = path.read_text(encoding="utf-8")
        assert not decorative_prefix_re.search(text), f"{path} has decorative toast prefix"

    render_core = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    for symbol in ("✓", "✔", "✅", "✕", "✖", "❌", "↩", "⚠"):
        assert symbol in render_core


def test_summary_refinements_are_filtered_before_rendering() -> None:
    render_core = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    api_events = (JS_DIR / "api_events.js").read_text(encoding="utf-8")

    assert "const MAX_REFINEMENT_CHIPS = 4;" in render_core
    assert "function normaliseRefinementText(value)" in render_core
    assert "function queryContainsRefinement(query, token)" in render_core
    assert "function visibleRefinementTokens(refinements, query)" in render_core
    assert "seen.has(key)" in render_core
    assert "queryContainsRefinement(query, token)" in render_core
    assert "if (visible.length >= MAX_REFINEMENT_CHIPS) break;" in render_core
    assert (
        "const visibleRefinements = "
        "visibleRefinementTokens(refinements, state.search.activeQuery);"
    ) in render_core
    assert "for (const token of visibleRefinements)" in render_core
    assert "function _queryContainsRefinement(query, token)" in api_events
    assert "const merged = _queryContainsRefinement(current, token)" in api_events


def test_unified_item_card_replaces_lead_and_watchlist_builders() -> None:
    """Roadmap milestone: lead/watchlist surfaces share one builder.
    The wrappers stay only as thin aliases for the unified function."""
    text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    # The unified entry point exists.
    assert "function buildItemCard(" in text
    # The wrappers became thin one-liners delegating to it.
    assert 'buildItemCard(lead, { mode: "lead", signal })' in text
    assert 'buildItemCard(item, { mode: "watching"' in text
    # buildItemCard is exported alongside the legacy names.
    assert "buildItemCard," in text


def test_make_swipeable_helper_kept_for_future_surfaces() -> None:
    """Swipe-to-promote was removed from watching cards because the
    swipe-bg DOM left thin colored slivers visible at the rounded
    corners on Telegram WebView. The helper itself stays in
    dom_helpers.js (still respects reduced-motion + haptics) so
    future surfaces can opt back in without re-implementing the
    gesture math from scratch.
    """
    text = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    assert "function makeSwipeable" in text
    assert "(prefers-reduced-motion: reduce)" in text
    assert "HapticFeedback" in text
    assert "impactOccurred" in text
    # Watching cards no longer wrap themselves with makeSwipeable —
    # confirm the call was removed and the explanatory comment stays.
    builder_text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "makeSwipeable(card" not in builder_text, (
        "swipe wrap re-introduced — re-evaluate the visual artifact "
        "before shipping"
    )


def test_listing_detail_loaders_share_stale_response_guard() -> None:
    """Three loaders open the listing-detail modal: openListingDetail
    (search results), openLeadDetail, openWatchlistDetail. They share
    state.detail and state.detailAi, so without a unified guard the
    older fetch can resolve last and pop the wrong content into the
    modal. Ensure all three bump the same _detailRequestId."""
    listings_js = (JS_DIR / "api_listings.js").read_text(encoding="utf-8")
    leads_js = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")

    surfaces = (
        ("api_listings.js", listings_js),
        ("api_leads.js", leads_js),
        ("api_watchlist.js", watchlist_js),
    )
    for path, text in surfaces:
        assert "state.detail._requestId" in text, f"{path} missing detail-request-id guard"
        assert "if (requestId !== state.detail._requestId)" in text, (
            f"{path} missing stale-response check"
        )

    # loadHistory has its own dedicated counter.
    assert "state.charts._historyRequestId" in listings_js


def test_long_press_action_menu_helper_and_listing_card_wiring() -> None:
    """A long press on a listing card surfaces a quick-action sheet
    (В покупки / В избранное / Открыть на Kufar) so the inline
    buttons stay scannable. Tapping the card itself opens the detail
    view, so "Подробнее" is no longer in the long-press menu. The
    helper must:
      * cancel on movement past the tolerance (treat as scroll),
      * suppress the synthetic click after a long press fires,
      * fire haptics on commit (Telegram-native feel),
      * be wired into buildListingNode in render_card_builders.js."""
    helpers = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    assert "function attachLongPress" in helpers
    assert "function showLongPressMenu" in helpers
    assert "function hideLongPressMenu" in helpers
    assert "moveTolerancePx" in helpers
    assert "suppressClick" in helpers
    assert "HapticFeedback" in helpers
    show_start = helpers.index("function showLongPressMenu")
    show_body = helpers[show_start:helpers.index("\nfunction attachLongPress", show_start)]
    assert "hideLongPressMenu();" in show_body
    assert "_longPressPreviousFocus = document.activeElement" in show_body
    assert "_applyInertToSiblings(overlay)" in show_body
    assert "trapFocus(sheet)" in show_body
    assert "_restoreInertSiblings(_longPressOverlay)" in show_body
    assert "_longPressPreviousFocus.focus" in show_body

    builder = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "attachLongPress(listing" in builder
    # The three action labels must be present in the menu so a casual
    # rename here trips the test instead of silently shipping.
    for label in (
        '"В покупки"',
        '"В избранное"',
        '"Открыть на Kufar"',
    ):
        assert label in builder, f"long-press menu missing item {label}"

    # Tapping .listing-top opens the detail view via delegation
    assert "listing._item = item" in builder
    cards = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    assert "_delegateListingClick" in cards

    css = _read_all_css()
    for cls in (
        ".lp-menu-overlay",
        ".lp-menu-sheet",
        ".lp-menu-item",
        ".lp-menu-item--accent",
    ):
        assert cls in css, f"missing CSS for {cls}"


def test_watchlist_renders_price_sparkline_when_history_present() -> None:
    """Watchlist cards now show a 30-day price-trend sparkline next
    to the current price. The renderer must:
      * read item.price_history (the inline series the backend
        returns from /api/v1/watchlist),
      * skip rendering when the series has fewer than 2 points,
      * pick a direction (down/up/flat) so CSS can colour the line —
        green for buyer-friendly drops, red for rises, muted for flat."""
    text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "function _buildPriceSparkline" in text
    assert "item.price_history" in text
    # Direction class is built as `wl-sparkline--${direction}` and the
    # three literal direction values must exist for the CSS selectors
    # to match. Stylesheet itself is checked separately.
    assert "wl-sparkline--" in text
    for direction in ('"down"', '"up"', '"flat"'):
        assert direction in text, f"missing direction literal {direction}"

    css_text = _read_all_css()
    for cls in (".wl-sparkline--down", ".wl-sparkline--up", ".wl-sparkline--flat"):
        assert cls in css_text, f"sparkline CSS for {cls} missing"


def test_detail_modal_supports_pinch_zoom_with_swipe_deferral() -> None:
    """The listing-detail modal hosts the photo gallery. Two-finger
    pinch + double-tap zoom on the main image is provided by
    attachPinchZoom (dom_helpers.js); when the image is zoomed
    (`.is-zoomed`) the swipe-between-photos handler in api_events.js
    must defer to the zoom interaction so panning a magnified shot
    doesn't accidentally jump to the next photo.

    The zoom uses a transform-origin: 0 0 model with anchor-point
    math so the zoom focuses on the pinch center, not the image
    center. No getBoundingClientRect() is called in the pinch helper;
    it clamps against cached viewport dimensions instead."""
    helpers = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    modals = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")

    assert "function attachPinchZoom" in helpers
    assert "is-zoomed" in helpers
    assert "reset" in helpers

    # Anchor-point model: transform-origin 0 0, viewportToImage helper
    assert 'transformOrigin = "0 0"' in helpers or "transformOrigin: '0 0'" in helpers
    assert "viewportToImage" in helpers
    assert "pinchAnchorPx" in helpers
    assert "pinchAnchorPy" in helpers

    # Clamping uses cached viewport dimensions instead of layout reads
    pinch = helpers[
        helpers.index("function attachPinchZoom"):
        helpers.index("/* ─── Long-press action menu")
    ]
    assert "getBoundingClientRect" not in pinch
    assert "style.transform = \"none\"" not in pinch
    assert "clampTranslate" in helpers

    # The swipe-between-photos handler must short-circuit while zoomed
    # OR while the user has more than one finger on the screen.
    carousel = (JS_DIR / "api_events_carousel.js").read_text(encoding="utf-8")
    assert "is-zoomed" in carousel
    assert "e.touches.length > 1" in carousel or "touches.length > 1" in carousel

    # render_modals.js wires the helper at render time and resets the
    # transform on close so the next lot opens at 1×.
    assert "_pinchController" in modals
    assert "attachPinchZoom" in modals


def test_theme_init_does_not_inherit_telegram_palette_colours() -> None:
    """The Mini App keeps its own palette so the brand stays consistent
    across Telegram clients with custom themes / AMOLED / Premium
    gradients. We mirror Telegram's coarse dark/light preference at
    startup but never bridge the per-colour theme params (bg_color,
    text_color, button_color, …) onto our CSS variables."""
    text = (JS_DIR / "app_core.js").read_text(encoding="utf-8")
    # We DO honour the binary dark/light hint…
    assert "colorScheme" in text
    assert '"themeChanged"' in text
    assert "syncTelegramChromeTheme" in text
    assert '"setHeaderColor"' in text
    assert '"setBottomBarColor"' in text
    assert 'meta[name="theme-color"]' in text
    # …but we DO NOT pull individual palette colours.
    forbidden_keys = (
        "themeParams.bg_color",
        "themeParams.text_color",
        "themeParams.button_color",
        "themeParams.hint_color",
        "applyTelegramThemeColors",
    )
    for needle in forbidden_keys:
        assert needle not in text, (
            f"theme integration leaked back: {needle} must not be referenced — "
            "the Mini App keeps its own palette"
        )


def test_tracker_event_card_buttons_refactored() -> None:
    """Tracker event cards: 'Открыть' button removed, 'В избранное'
    added, header click opens detail via openListingDetail."""
    src = (JS_DIR / "render_trackers.js").read_text(encoding="utf-8")

    # (a) old open-query button must be gone
    assert 'role: "open-query"' not in src
    assert "open-query" not in src

    # (b) favorites button present
    assert 'role: "watch"' in src

    # (c) watchlist action wired
    assert "addWatchlistFromListing" in src

    # (d) header click opens detail
    assert "event-header" in src
    assert "openListingDetail" in src


def test_render_charts_hex_literals_only_in_token_fallbacks() -> None:
    """FE-NEW-5: render_charts.js must not contain bare hex color literals
    outside of _token("--name", "#fallback") calls. All chart colors
    should be read from CSS tokens at render time."""
    charts_js = (JS_DIR / "render_charts.js").read_text(encoding="utf-8")
    hex_re = re.compile(r'#[0-9a-fA-F]{3,8}\b')
    token_fallback_re = re.compile(r'_token\(\s*"[^"]+"\s*,\s*"(#[0-9a-fA-F]{3,8})"\s*\)')

    # Collect all hex literals that are inside _token() fallback positions
    allowed_hexes_in_context = set()
    for m in token_fallback_re.finditer(charts_js):
        allowed_hexes_in_context.add(m.start())

    # Find all hex literals and check they appear inside _token() calls
    bare_hex_lines = []
    for i, line in enumerate(charts_js.splitlines(), 1):
        # Skip lines that are _token() calls (fallbacks are fine)
        if '_token(' in line:
            continue
        # Skip comments
        stripped = line.strip()
        if stripped.startswith('//') or stripped.startswith('*'):
            continue
        if hex_re.search(line):
            bare_hex_lines.append((i, line.strip()))

    assert bare_hex_lines == [], (
        "render_charts.js has bare hex literals outside _token() fallbacks:\n"
        + "\n".join(f"  L{n}: {ln}" for n, ln in bare_hex_lines[:10])
    )


def test_hot_path_view_switches_do_not_double_render() -> None:
    """FE-NEW-7: hot paths (view switch, tab click) must NOT call
    renderAll() immediately after setActiveView() — setActiveView
    already calls markDirty('tabs', 'views') + renderAll() internally.
    A redundant renderAll doubles the frame work on budget Android
    Telegram WebView devices."""
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    events_js = (JS_DIR / "api_events.js").read_text(encoding="utf-8")

    # openOverview and openProfile must not have renderAll() right after setActiveView
    overview_start = actions_js.index("function openOverview()")
    overview_end = actions_js.index(
        "\n    function openProfile", overview_start,
    )
    overview_body = actions_js[overview_start:overview_end]
    assert "setActiveView" in overview_body
    # After setActiveView line, renderAll should NOT appear
    after_set = overview_body[overview_body.index("setActiveView"):]
    assert "renderAll();" not in after_set.split("\n", 1)[1] if "\n" in after_set else True

    # api_events.js tab click handler: setActiveView(view) should not be
    # followed immediately by renderAll()
    tab_handler_start = events_js.index("_confirmTimers.watchlist = undefined;")
    tab_section = events_js[tab_handler_start:tab_handler_start + 200]
    set_idx = tab_section.index("setActiveView(view);")
    after_set_tab = tab_section[set_idx + len("setActiveView(view);"):]
    # The next non-comment, non-blank line should NOT be renderAll()
    for line in after_set_tab.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        assert stripped != "renderAll();", (
            "tab click handler calls renderAll() after setActiveView — "
            "setActiveView already renders internally (FE-NEW-7)"
        )
        break


def test_nginx_brotli_config_documented() -> None:
    """PERF-NEW-5: nginx/default.conf must contain the brotli
    configuration block (commented or active) so the compression
    policy is documented and ready to activate."""
    nginx_conf = Path("nginx/default.conf").read_text(encoding="utf-8")
    assert "PERF-NEW-5" in nginx_conf
    assert "brotli_comp_level 6" in nginx_conf
    assert "brotli_types" in nginx_conf


# ── RV wave E (review stage 1, frontend) ──────────────────────────────────
# Behavioural harnesses for RV-FE-02…RV-FE-05, RV-UI-01…RV-UI-03, RV-SEC-05
# and RV-AI-07: the real modules run in node against a small fake DOM
# instead of being grepped for substrings.

_FAKE_DOM_JS = r"""
// Minimal DOM for node harnesses: tree ops, attributes/dataset/classList,
// capture/bubble events, and the selector subset the app uses
// (tag, #id, .class, [attr], [attr="v"], :not(), descendant, child).
class FakeEvent {
    constructor(type, init = {}) {
        Object.assign(this, init);
        this.type = type;
        this.bubbles = init.bubbles !== false;
        this.defaultPrevented = false;
        this._stopped = false;
        this._stoppedImmediate = false;
        this.target = null;
        this.currentTarget = null;
    }
    preventDefault() { this.defaultPrevented = true; }
    stopPropagation() { this._stopped = true; }
    stopImmediatePropagation() { this._stopped = true; this._stoppedImmediate = true; }
}

class FakeNode {
    constructor(nodeType) {
        this.nodeType = nodeType;
        this.parentNode = null;
        this.childNodes = [];
        this._listeners = [];
    }
    get children() { return this.childNodes.filter((node) => node.nodeType === 1); }
    get firstElementChild() { return this.children[0] || null; }
    get childElementCount() { return this.children.length; }
    get firstChild() { return this.childNodes[0] || null; }
    get parentElement() {
        return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null;
    }
    get isConnected() {
        let node = this;
        while (node) {
            if (node === document) return true;
            node = node.parentNode;
        }
        return false;
    }
    get textContent() {
        if (this.nodeType === 3) return this._text;
        return this.childNodes.map((node) => node.textContent).join("");
    }
    set textContent(value) {
        if (this.nodeType === 3) {
            this._text = String(value);
            return;
        }
        this.replaceChildren();
        const text = value == null ? "" : String(value);
        if (text) this.appendChild(document.createTextNode(text));
    }
    _adopt(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = this;
    }
    appendChild(child) {
        if (child.nodeType === 11) {
            for (const node of [...child.childNodes]) this.appendChild(node);
            return child;
        }
        this._adopt(child);
        this.childNodes.push(child);
        return child;
    }
    insertBefore(child, ref) {
        if (!ref) return this.appendChild(child);
        if (child.nodeType === 11) {
            for (const node of [...child.childNodes]) this.insertBefore(node, ref);
            return child;
        }
        this._adopt(child);
        const index = this.childNodes.indexOf(ref);
        this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, child);
        return child;
    }
    append(...items) {
        for (const item of items) this.appendChild(_asNode(item));
    }
    prepend(...items) {
        const first = this.childNodes[0] || null;
        for (const item of items) this.insertBefore(_asNode(item), first);
    }
    removeChild(child) {
        const index = this.childNodes.indexOf(child);
        if (index >= 0) this.childNodes.splice(index, 1);
        child.parentNode = null;
        return child;
    }
    replaceChild(next, old) {
        this.insertBefore(next, old);
        this.removeChild(old);
        return old;
    }
    replaceChildren(...items) {
        for (const node of [...this.childNodes]) this.removeChild(node);
        this.append(...items);
    }
    remove() {
        if (this.parentNode) this.parentNode.removeChild(this);
    }
    contains(other) {
        let node = other;
        while (node) {
            if (node === this) return true;
            node = node.parentNode;
        }
        return false;
    }
    addEventListener(type, fn, options) {
        if (typeof fn !== "function") return;
        const capture = options === true || Boolean(options && options.capture);
        const same = (l) => l.type === type && l.fn === fn && l.capture === capture;
        if (this._listeners.some(same)) return;
        const signal = options && typeof options === "object" ? options.signal : null;
        if (signal && signal.aborted) return;
        this._listeners.push({ type, fn, capture, once: Boolean(options && options.once) });
        if (signal) {
            signal.addEventListener("abort", () => this.removeEventListener(type, fn, capture));
        }
    }
    removeEventListener(type, fn, options) {
        const capture = options === true || Boolean(options && options.capture);
        this._listeners = this._listeners.filter(
            (l) => !(l.type === type && l.fn === fn && l.capture === capture),
        );
    }
    _invoke(event, phase) {
        for (const entry of [...this._listeners]) {
            if (entry.type !== event.type) continue;
            if (phase === "capture" && !entry.capture) continue;
            if (phase === "bubble" && entry.capture) continue;
            if (!this._listeners.includes(entry)) continue;
            if (entry.once) this.removeEventListener(entry.type, entry.fn, entry.capture);
            event.currentTarget = this;
            entry.fn.call(this, event);
            if (event._stoppedImmediate) return;
        }
    }
    dispatchEvent(event) {
        event.target = this;
        const path = [];
        let node = this.parentNode;
        while (node) {
            path.unshift(node);
            node = node.parentNode;
        }
        for (const ancestor of path) {
            ancestor._invoke(event, "capture");
            if (event._stopped) return !event.defaultPrevented;
        }
        this._invoke(event, "target");
        if (event._stopped || !event.bubbles) return !event.defaultPrevented;
        for (const ancestor of [...path].reverse()) {
            ancestor._invoke(event, "bubble");
            if (event._stopped) break;
        }
        return !event.defaultPrevented;
    }
}

function _asNode(item) {
    return item instanceof FakeNode ? item : document.createTextNode(String(item));
}

class FakeText extends FakeNode {
    constructor(text) {
        super(3);
        this._text = String(text);
    }
}

class FakeFragment extends FakeNode {
    constructor() { super(11); }
}

function _camelData(name) {
    return name.replace(/-([a-z])/g, (_, chr) => chr.toUpperCase());
}

const _ATTR_RE = /^\s*([\w-]+)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*$/;

function _parseCompound(text) {
    const parts = [];
    let i = 0;
    const tag = text.match(/^[a-zA-Z*][\w-]*/);
    if (tag) {
        if (tag[0] !== "*") parts.push({ kind: "tag", value: tag[0].toUpperCase() });
        i = tag[0].length;
    }
    while (i < text.length) {
        const chr = text[i];
        if (chr === "#" || chr === ".") {
            const name = text.slice(i + 1).match(/^[\w-]+/)[0];
            parts.push({ kind: chr === "#" ? "id" : "class", value: name });
            i += 1 + name.length;
        } else if (chr === "[") {
            const end = text.indexOf("]", i);
            const body = text.slice(i + 1, end);
            const attr = body.match(_ATTR_RE);
            parts.push({ kind: "attr", name: attr[1], value: attr[2] ?? attr[3] ?? attr[4] });
            i = end + 1;
        } else if (text.startsWith(":not(", i)) {
            let depth = 0;
            let j = i + 4;
            for (; j < text.length; j++) {
                if (text[j] === "(") depth += 1;
                else if (text[j] === ")") {
                    depth -= 1;
                    if (depth === 0) break;
                }
            }
            parts.push({ kind: "not", inner: _parseCompound(text.slice(i + 5, j)) });
            i = j + 1;
        } else {
            throw new Error(`fake DOM: unsupported selector "${text}"`);
        }
    }
    return parts;
}

function _matchesCompound(el, parts) {
    return parts.every((part) => {
        if (part.kind === "tag") return el.tagName === part.value;
        if (part.kind === "id") return el.id === part.value;
        if (part.kind === "class") return el.classList.contains(part.value);
        if (part.kind === "attr") {
            const value = el.getAttribute(part.name);
            return part.value === undefined ? value != null : value === part.value;
        }
        if (part.kind === "not") return !_matchesCompound(el, part.inner);
        return false;
    });
}

function _splitTopLevel(text, separator) {
    const out = [];
    let current = "";
    let depth = 0;
    for (const chr of text) {
        if (chr === "[" || chr === "(") depth += 1;
        if (chr === "]" || chr === ")") depth -= 1;
        if (depth === 0 && chr === separator) {
            out.push(current);
            current = "";
            continue;
        }
        current += chr;
    }
    out.push(current);
    return out;
}

function _complexTokens(selector) {
    const tokens = [];
    let current = "";
    let depth = 0;
    for (const chr of selector.trim()) {
        if (chr === "[" || chr === "(") depth += 1;
        if (chr === "]" || chr === ")") depth -= 1;
        if (depth === 0 && (chr === " " || chr === ">")) {
            if (current) {
                tokens.push(current);
                current = "";
            }
            const last = tokens[tokens.length - 1];
            if (chr === ">") {
                if (last === " ") tokens.pop();
                tokens.push(">");
            } else if (last !== undefined && last !== " " && last !== ">") {
                tokens.push(" ");
            }
            continue;
        }
        current += chr;
    }
    if (current) tokens.push(current);
    return tokens;
}

function _matchFrom(el, tokens, index) {
    if (!_matchesCompound(el, _parseCompound(tokens[index]))) return false;
    if (index === 0) return true;
    if (tokens[index - 1] === ">") {
        const parent = el.parentNode;
        return Boolean(parent && parent.nodeType === 1 && _matchFrom(parent, tokens, index - 2));
    }
    let ancestor = el.parentNode;
    while (ancestor && ancestor.nodeType === 1) {
        if (_matchFrom(ancestor, tokens, index - 2)) return true;
        ancestor = ancestor.parentNode;
    }
    return false;
}

function _matchesSelector(el, selectorList) {
    return _splitTopLevel(selectorList, ",").some((selector) => {
        const tokens = _complexTokens(selector);
        return _matchFrom(el, tokens, tokens.length - 1);
    });
}

class FakeElement extends FakeNode {
    constructor(tagName) {
        super(1);
        this.tagName = String(tagName).toUpperCase();
        this._attrs = new Map();
        this.dataset = {};
        this.style = {};
        this.value = "";
        this.checked = false;
        const self = this;
        this.classList = {
            add(...names) { self._setClasses([...self._classes(), ...names]); },
            remove(...names) {
                self._setClasses(self._classes().filter((name) => !names.includes(name)));
            },
            contains(name) { return self._classes().includes(name); },
            toggle(name, force) {
                const on = force === undefined ? !self._classes().includes(name) : Boolean(force);
                if (on) this.add(name);
                else this.remove(name);
                return on;
            },
        };
    }
    _classes() { return (this.getAttribute("class") || "").split(/\s+/).filter(Boolean); }
    _setClasses(names) { this.setAttribute("class", [...new Set(names)].join(" ")); }
    get className() { return this.getAttribute("class") || ""; }
    set className(value) { this.setAttribute("class", String(value)); }
    get id() { return this.getAttribute("id") || ""; }
    set id(value) { this.setAttribute("id", String(value)); }
    get hidden() { return this._attrs.has("hidden"); }
    set hidden(value) {
        if (value) this._attrs.set("hidden", "");
        else this._attrs.delete("hidden");
    }
    get disabled() { return this._attrs.has("disabled"); }
    set disabled(value) {
        if (value) this._attrs.set("disabled", "");
        else this._attrs.delete("disabled");
    }
    get tabIndex() {
        const value = this.getAttribute("tabindex");
        if (value != null) return Number(value);
        return ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(this.tagName) ? 0 : -1;
    }
    set tabIndex(value) { this.setAttribute("tabindex", String(value)); }
    setAttribute(name, value) {
        const key = String(name).toLowerCase();
        if (key.startsWith("data-")) {
            this.dataset[_camelData(key.slice(5))] = String(value);
            return;
        }
        this._attrs.set(key, String(value));
    }
    getAttribute(name) {
        const key = String(name).toLowerCase();
        if (key.startsWith("data-")) {
            const value = this.dataset[_camelData(key.slice(5))];
            return value == null ? null : String(value);
        }
        return this._attrs.has(key) ? this._attrs.get(key) : null;
    }
    hasAttribute(name) { return this.getAttribute(name) != null; }
    removeAttribute(name) {
        const key = String(name).toLowerCase();
        if (key.startsWith("data-")) delete this.dataset[_camelData(key.slice(5))];
        this._attrs.delete(key);
    }
    focus() { document.activeElement = this; }
    blur() {
        if (document.activeElement === this) document.activeElement = document.body;
    }
    click() {
        if (this.disabled) return;
        this.dispatchEvent(new FakeEvent("click"));
    }
    matches(selector) { return _matchesSelector(this, selector); }
    closest(selector) {
        let node = this;
        while (node && node.nodeType === 1) {
            if (node.matches(selector)) return node;
            node = node.parentNode;
        }
        return null;
    }
    querySelectorAll(selector) {
        const out = [];
        const walk = (node) => {
            for (const child of node.children) {
                if (child.matches(selector)) out.push(child);
                walk(child);
            }
        };
        walk(this);
        return out;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    scrollIntoView() {}
    getBoundingClientRect() {
        return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
    }
}

class FakeDocument extends FakeNode {
    constructor() {
        super(9);
        this.documentElement = new FakeElement("html");
        this.head = new FakeElement("head");
        this.body = new FakeElement("body");
        this.documentElement.appendChild(this.head);
        this.documentElement.appendChild(this.body);
        this.documentElement.parentNode = this;
        this.childNodes = [this.documentElement];
        this.activeElement = this.body;
        this.hidden = false;
    }
    createElement(tag) { return new FakeElement(tag); }
    createElementNS(_ns, tag) { return new FakeElement(tag); }
    createTextNode(text) { return new FakeText(text); }
    createDocumentFragment() { return new FakeFragment(); }
    getElementById(id) {
        return this.documentElement.querySelectorAll("*").find((el) => el.id === id) || null;
    }
    querySelector(selector) { return this.documentElement.querySelector(selector); }
    querySelectorAll(selector) { return this.documentElement.querySelectorAll(selector); }
}

function makeStorage() {
    const map = new Map();
    return {
        get length() { return map.size; },
        key(index) { return [...map.keys()][index] ?? null; },
        getItem(key) { return map.has(key) ? map.get(key) : null; },
        setItem(key, value) { map.set(key, String(value)); },
        removeItem(key) { map.delete(key); },
        clear() { map.clear(); },
    };
}

const document = new FakeDocument();
globalThis.document = document;
globalThis.Node = FakeNode;
globalThis.window = globalThis;
globalThis.App = globalThis.App || {};
globalThis.localStorage = makeStorage();
globalThis.location = {
    hostname: "app.example",
    protocol: "https:",
    search: "",
    origin: "https://app.example",
};
// Reduced motion: modal helpers finalise synchronously.
globalThis.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
globalThis.scrollY = 0;
globalThis.pageYOffset = 0;
globalThis.scrollTo = () => {};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
        if (name === "hidden") node.hidden = Boolean(value);
        else node.setAttribute(name, value === true ? "" : String(value));
    }
    node.append(...children);
    return node;
}

function fire(target, type, init = {}) {
    return target.dispatchEvent(new FakeEvent(type, init));
}

function keydown(target, key, init = {}) {
    return target.dispatchEvent(new FakeEvent("keydown", { key, ...init }));
}

const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
"""


def _js(*names: str) -> str:
    return "\n".join((JS_DIR / name).read_text(encoding="utf-8") for name in names)


def _node_ok(script: str) -> None:
    result = subprocess.run(["node"], input=script, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr or result.stdout


_NODE_FAIL = r"""
    .catch((err) => {
        console.error(err && err.stack || err);
        process.exit(1);
    });
"""


def test_rv_fe03_result_actions_use_active_query_not_draft() -> None:
    """RV-FE-03: paging, detail, sort reload and filter re-runs build
    their requests from the query of the results on screen while the
    search box holds a half-typed draft."""
    harness = _FAKE_DOM_JS + _js(
        "dom_helpers.js", "app_core_dom.js", "app_core.js", "api_core.js", "api_listings.js",
    ) + r"""
const assert = require("assert");
const core = createAppCore();
const state = core.state;
const requests = [];
const elements = { searchInput: el("input") };
document.body.append(elements.searchInput);
function payloadFor(url) {
    if (url.startsWith("/api/v1/price-stats")) return { categories: [], total_results: 3 };
    if (url.startsWith("/api/v1/listings")) {
        return { listings: [{ ad_id: 1 }], total: 120, has_more: true };
    }
    if (url.startsWith("/api/v1/listing-detail")) return { ad_id: 5, title: "Lot" };
    return { points: [], regions: [] };
}
const apiCore = createApiCore({ state });
const api = createApiListings({
    state,
    elements,
    hasTelegramInitData: () => true,
    renderAll() {},
    scheduleRender() {},
    markDirty() {},
    renderLoading() {},
    renderError() {},
    renderHistory() {},
    setPanelOpen() {},
    renderDetailModal() {},
    closeDetailModal() {},
    showToast() {},
    focusTarget() {},
    buildCommonQuery: apiCore.buildCommonQuery,
    getJson(url) {
        requests.push(url);
        return Promise.resolve(payloadFor(url));
    },
    deleteJson() {},
});
const queryOf = (url) => new URLSearchParams(url.split("?")[1]).get("query");

(async () => {
    elements.searchInput.value = "iphone 13";
    await api.search("ads");
    await tick(5);
    assert.strictEqual(state.search.activeQuery, "iphone 13");
    assert.strictEqual(state.search.draft, "iphone 13");
    assert.ok(requests.length >= 5, requests.join("\n"));
    assert.ok(requests.every((url) => queryOf(url) === "iphone 13"), requests.join("\n"));

    // The user starts typing a new query but has not searched it yet.
    elements.searchInput.value = "i";
    state.search.draft = "i";
    requests.length = 0;
    await api.loadMoreListings();
    await api.openListingDetail({ ad_id: 5 });
    await api.loadListings(true);
    await api.search("ads", { keepFilters: true, query: state.search.activeQuery });
    await tick(5);
    assert.ok(requests.length >= 8, requests.join("\n"));
    for (const url of requests) assert.strictEqual(queryOf(url), "iphone 13", url);
    assert.strictEqual(state.search.draft, "i", "re-running the results keeps the draft");

    // Only an explicit search of the typed text commits it.
    await api.search("overview");
    assert.strictEqual(state.search.activeQuery, "i");

    // Clearing the box retires the in-flight search and drops the cards.
    const requestId = state.search.searchRequestId;
    elements.searchInput.value = "";
    await api.search("overview");
    assert.strictEqual(state.search.activeQuery, "");
    assert.notStrictEqual(state.search.searchRequestId, requestId);
    assert.strictEqual(state.listings._pending, false);
    assert.deepStrictEqual(state.listings.items, []);
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_fe03_input_events_edit_only_the_draft() -> None:
    """RV-FE-03: keystrokes edit ``draft``; refinement chips, the error
    retry, the strict toggle, filters and sort act on ``activeQuery``."""
    harness = _FAKE_DOM_JS + _js("dom_helpers.js") + r"""
function _bindCarouselEvents() {}
function _bindExpenseEvents() {}
""" + _js("api_events.js") + r"""
const assert = require("assert");
const searches = [];
const listingLoads = [];
const state = {
    ui: { activeView: "ads", error: "boom" },
    search: { draft: "iphone 13", activeQuery: "iphone 13", strictSearch: true, sort: "newest" },
    filters: {
        category: null, pendingCategory: 7, condition: "", pendingCondition: "",
        sellerType: "", pendingSellerType: "", minPrice: null, pendingMinPrice: null,
        maxPrice: null, pendingMaxPrice: null, regionName: "", pendingRegionName: "",
        discountFromPercent: 10, discountToPercent: 30, filterDropdownOpen: true,
    },
    trackers: { events: [] },
    leads: { items: [] },
    watchlist: { items: [] },
    detail: { data: null },
    admin: {},
};
const refinementChip = el("button", { "data-refinement": "pro" });
const priceSort = el("button", { "data-sort": "price_asc" });
const newestSort = el("button", { "data-sort": "newest" });
const elements = {
    searchInput: el("input"),
    errorRetry: el("button"),
    summaryRefinements: el("div", {}, refinementChip),
    strictSearchToggle: el("input"),
    filterApplyBtn: el("button"),
    sortButtons: [priceSort, newestSort],
    viewTabs: [],
};
document.body.append(
    elements.searchInput, elements.errorRetry, elements.summaryRefinements,
    elements.strictSearchToggle, elements.filterApplyBtn, priceSort, newestSort,
);
createApiEvents({
    state,
    elements,
    renderAll() {},
    renderLoading() {},
    renderStrictSearch() {},
    renderSortButtons() {},
    search: (...args) => { searches.push(args); return Promise.resolve(); },
    loadListings: (...args) => { listingLoads.push(args); return Promise.resolve(); },
    startTrackerRefresh() {},
    stopTrackerRefresh() {},
    showToast() {},
}).bindEvents();

elements.searchInput.value = "i";
fire(elements.searchInput, "input");
assert.strictEqual(state.search.draft, "i");
assert.strictEqual(state.search.activeQuery, "iphone 13");
assert.strictEqual(searches.length, 0);

fire(refinementChip, "click");
assert.strictEqual(elements.searchInput.value, "iphone 13 pro", "chips extend the active query");
assert.strictEqual(state.search.draft, "iphone 13 pro");

searches.length = 0;
fire(elements.errorRetry, "click");
assert.deepStrictEqual(searches[0][1], { forceRefresh: true, query: "iphone 13" });

searches.length = 0;
elements.strictSearchToggle.checked = false;
fire(elements.strictSearchToggle, "change");
assert.strictEqual(searches[0][1].query, "iphone 13");

searches.length = 0;
fire(elements.filterApplyBtn, "click");
assert.deepStrictEqual(searches[0][1], { keepFilters: true, query: "iphone 13" });

fire(priceSort, "click");
assert.strictEqual(listingLoads.length, 1);

// Nothing searched yet: a draft alone must not reload results.
state.search.activeQuery = "";
fire(newestSort, "click");
assert.strictEqual(listingLoads.length, 1);
process.exit(0);
"""
    _node_ok(harness)


def test_rv_fe02_load_script_times_out_removes_tag_and_retries() -> None:
    """RV-FE-02: concurrent callers share one <script>; a failed or
    stalled tag is removed and the next call inserts a fresh one."""
    harness = _FAKE_DOM_JS + _js("dom_helpers.js", "app_core_dom.js", "app_core.js") + r"""
const assert = require("assert");
const realSetTimeout = setTimeout;
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
globalThis.clearTimeout = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; };
const flush = () => new Promise((resolve) => realSetTimeout(resolve, 0));
const scripts = () => document.head.querySelectorAll("script");
const core = createAppCore();

(async () => {
    const first = core._loadScript("js/x.js?v=1");
    assert.strictEqual(core._loadScript("js/x.js?v=1"), first, "one in-flight load per src");
    assert.strictEqual(scripts().length, 1);
    scripts()[0].onerror();
    await assert.rejects(first, /Failed to load/);
    assert.strictEqual(scripts().length, 0, "the failed tag is removed");

    const retry = core._loadScript("js/x.js?v=1");
    assert.strictEqual(scripts().length, 1, "retry inserts a fresh tag");
    scripts()[0].onload();
    await retry;
    await core._loadScript("js/x.js?v=1");
    assert.strictEqual(scripts().length, 1, "a loaded script is not requested again");

    const stalled = core._loadScript("js/y.js?v=1");
    const timeout = timers.find((t) => !t.cleared && t.ms >= 10000);
    assert.ok(timeout, "loads are bounded by a timeout");
    timeout.fn();
    await assert.rejects(stalled, /Timed out/);
    assert.strictEqual(scripts().filter((s) => s.src === "js/y.js?v=1").length, 0);
    await flush();
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_fe02_render_stub_shows_error_state_and_recovers() -> None:
    """RV-FE-02: a failed chunk paints an error state with "Повторить"
    instead of eternal skeletons, and recovers on retry or after the
    cooldown instead of staying broken until a restart."""
    harness = _FAKE_DOM_JS + _js(
        "dom_helpers.js", "app_core_dom.js", "app_core.js", "_lazy_cards_stub.js",
    ) + r"""
const assert = require("assert");
let now = 1_000_000;
Date.now = () => now;
let fail = true;
let scheduled = 0;
const loads = [];
const rendered = [];
const context = {
    state: { search: { activeQuery: "iphone" } },
    elements: {
        listingsList: el("div"),
        leadInboxList: el("div"),
        listingsSection: el("section", { hidden: true }),
    },
    logClientError() {},
    scheduleRender() { scheduled += 1; },
    _loadScript(src) {
        loads.push(src);
        if (fail) return Promise.reject(new Error("offline"));
        window.App._realCreateRenderCardBuilders = () => ({});
        window.App._realCreateRenderCards = () => ({
            renderListings() { rendered.push("listings"); },
            renderLeads() { rendered.push("leads"); },
        });
        return Promise.resolve();
    },
};
document.body.append(context.elements.listingsSection, context.elements.leadInboxList);
context.elements.listingsSection.append(context.elements.listingsList);
const cards = createRenderCards(context);

(async () => {
    cards.renderListings();
    await tick(5);
    assert.strictEqual(loads.length, 1);
    assert.ok(scheduled >= 1, "a failed load repaints into the error state");

    cards.renderListings();
    cards.renderLeads();
    assert.strictEqual(loads.length, 1, "no reload on every repaint inside the cooldown");
    const listingsError = context.elements.listingsList.querySelector(".lazy-load-error");
    assert.ok(listingsError, "listings show an error state");
    assert.ok(
        context.elements.leadInboxList.querySelector(".lazy-load-error"), "deals show it too",
    );
    assert.strictEqual(context.elements.listingsSection.hidden, false);
    cards.renderListings();
    assert.strictEqual(
        context.elements.listingsList.querySelector(".lazy-load-error"), listingsError,
        "the error state is not rebuilt on every repaint",
    );

    // After the cooldown the next repaint retries on its own.
    now += 9000;
    cards.renderListings();
    await tick(5);
    assert.strictEqual(loads.length, 2, "the rejected init promise was not pinned");

    // "Повторить" retries immediately and recovers.
    fail = false;
    const button = context.elements.leadInboxList.querySelector(".lazy-load-error button");
    assert.strictEqual(button.textContent, "Повторить");
    const before = scheduled;
    button.click();
    assert.strictEqual(button.disabled, true);
    await tick(5);
    assert.ok(scheduled > before);
    cards.renderListings();
    cards.renderLeads();
    assert.deepStrictEqual(rendered, ["listings", "leads"]);
    assert.ok(await cards._ensureLoaded());
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_fe02_api_stubs_report_missing_chunk_instead_of_rejecting() -> None:
    """RV-FE-02: list loaders turn a missing deals chunk into the list's
    error state, explicit actions get a toast, teardown needs no chunk,
    and the next call retries the download."""
    harness = _FAKE_DOM_JS + _js(
        "dom_helpers.js", "app_core_dom.js", "app_core.js", "_lazy_deals_stub.js",
    ) + r"""
const assert = require("assert");
let fail = true;
const loads = [];
const toasts = [];
const renders = [];
const realLoads = [];
const state = {
    leads: { items: [{ id: 1 }], error: null },
    watchlist: { items: [], error: null, _loading: true },
    detail: { data: { ad_id: 7 } },
};
const context = {
    state,
    logClientError() {},
    showToast: (...args) => toasts.push(args),
    renderLeads: () => renders.push("leads"),
    renderWatchlist: () => renders.push("watchlist"),
    _loadScript(src) {
        loads.push(src);
        if (fail) return Promise.reject(new Error("offline"));
        window.App._realCreateApiLeads = () => ({
            loadLeads: async () => realLoads.push("leads"),
        });
        window.App._realCreateApiWatchlist = () => ({});
        window.App._realCreateRenderModals = () => ({
            closeDetailModal() {},
            renderDetailModal() {},
        });
        return Promise.resolve();
    },
};
const leads = createApiLeads(context);
const watchlist = createApiWatchlist(context);
const modals = createRenderModals(context);

(async () => {
    assert.strictEqual(await leads.loadLeads(), undefined);
    assert.strictEqual(state.leads.error, LAZY_LOAD_FAILED_MESSAGE);
    assert.deepStrictEqual(state.leads.items, [{ id: 1 }], "previous data survives");
    assert.ok(renders.includes("leads"));
    assert.strictEqual(toasts.length, 0, "the list shows the error, no toast");

    await watchlist.loadWatchlist();
    assert.strictEqual(state.watchlist.error, LAZY_LOAD_FAILED_MESSAGE);
    assert.strictEqual(state.watchlist._loading, false);

    await watchlist.addWatchlistFromListing({ ad_id: 7 });
    assert.strictEqual(toasts.length, 1, "an explicit action reports the failure");

    const loadsBefore = loads.length;
    modals.closeDetailModal();
    assert.strictEqual(loads.length, loadsBefore, "closing never-opened modals loads nothing");

    modals.renderDetailModal();
    await tick(5);
    assert.strictEqual(state.detail.data, null, "a detail that never opened is not left in state");
    assert.strictEqual(toasts.length, 2);

    fail = false;
    await leads.loadLeads();
    assert.deepStrictEqual(realLoads, ["leads"], "the next call retried the download");
})()""" + _NODE_FAIL
    _node_ok(harness)


_ACTIONS_STUBS = r"""
function noop() {}
function stubModule() { return new Proxy({}, { get: () => noop }); }
const stubCalls = [];
function spyModule(label) {
    const record = (name) => () => { stubCalls.push(`${label}.${String(name)}`); };
    return new Proxy({}, { get: (_target, name) => record(name) });
}
function createImageProxyLoader() {
    return { fetchProxyImageObjectUrl: noop, clearProxyImageObjectUrls: noop };
}
const createApiListings = () => spyModule("listings");
const createApiTrackers = () => spyModule("trackers");
const createApiLeads = stubModule;
const createApiWatchlist = stubModule;
function createApiEvents() { return { bindEvents: noop }; }
"""


def test_rv_fe02_ai_modules_load_once_and_retry_after_failure() -> None:
    """RV-FE-02: a double tap shares one AI module load (it used to build
    two modules: double submit, two polling loops); a failure tells the
    user and the next tap retries."""
    harness = _FAKE_DOM_JS + _js("dom_helpers.js") + _ACTIONS_STUBS + r"""
function createApiCore() {
    return {
        telegramHeaders: () => ({}),
        requestJson: noop,
        getJson: noop,
        postJson: noop,
        deleteJson: noop,
        buildCommonQuery: noop,
    };
}
""" + _js("app_actions.js") + r"""
const assert = require("assert");
const loads = [];
const toasts = [];
const analyses = [];
let gate = null;
const actions = createAppActions({
    state: { search: { recentSearches: [] }, misc: {}, ui: {}, profile: {} },
    elements: { views: {} },
    markDirty: noop,
    renderAll: noop,
    showToast: (...args) => toasts.push(args),
    logClientError: noop,
    _loadScript(src) {
        loads.push(src);
        return gate.promise;
    },
});
function newGate() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}

(async () => {
    gate = newGate();
    const first = actions.loadAIAnalysis(1);
    const second = actions.loadAIAnalysis(1);
    await tick(0);
    assert.strictEqual(loads.length, 4, "both taps share one load of the 4 AI scripts");
    gate.reject(new Error("offline"));
    assert.strictEqual(await first, undefined);
    assert.strictEqual(await second, undefined);
    assert.ok(toasts.some(([text]) => text.includes("AI-модуль")), "the failure is reported");

    gate = newGate();
    window.App.createApiAi = () => ({
        loadAIAnalysis: (id) => analyses.push(id),
        closeAIModal: noop,
    });
    const third = actions.loadAIAnalysis(2);
    await tick(0);
    assert.strictEqual(loads.length, 8, "the next tap retries the download");
    gate.resolve();
    await third;
    assert.deepStrictEqual(analyses, [2]);
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_fe05_failed_reload_keeps_leads_and_watchlist() -> None:
    """RV-FE-05: a failed load (offline, 5xx, the 401 after initData's
    2 h lifetime) keeps the last good list and records the error instead
    of turning it into an empty list."""
    harness = _js("api_leads.js", "api_watchlist.js") + r"""
const assert = require("assert");
let next = () => Promise.reject(new Error("Сессия Telegram истекла — перезапустите приложение."));
const renders = [];
const state = {
    leads: { items: [{ id: 1 }], _requestId: 0, error: null, _loading: false },
    watchlist: { items: [{ id: 2 }], _requestId: 0, error: null, _loading: false },
    analytics: { loading: false, dashboard: null },
    detail: {},
    search: { activeQuery: "" },
};
const context = {
    state,
    hasTelegramInitData: () => true,
    renderLeads: () => renders.push("leads"),
    renderWatchlist: () => renders.push("watchlist"),
    renderDealsHeroStats() {},
    renderProfitDashboard() {},
    showToast() {},
    getJson: (url) => (url.includes("analytics") ? Promise.resolve({}) : next(url)),
};
const leads = createApiLeads(context);
const watchlist = createApiWatchlist(context);

(async () => {
    await leads.loadLeads();
    assert.deepStrictEqual(state.leads.items, [{ id: 1 }]);
    assert.strictEqual(state.leads.error, "Сессия Telegram истекла — перезапустите приложение.");
    assert.strictEqual(state.leads._loading, false);

    await watchlist.loadWatchlist();
    assert.deepStrictEqual(state.watchlist.items, [{ id: 2 }]);
    assert.ok(state.watchlist.error);
    assert.strictEqual(state.watchlist._loading, false);

    next = () => Promise.resolve([]);
    await leads.loadLeads();
    await watchlist.loadWatchlist();
    assert.deepStrictEqual(state.leads.items, []);
    assert.strictEqual(state.leads.error, null);
    assert.strictEqual(state.watchlist.error, null);
})()""" + _NODE_FAIL
    _node_ok(harness)


_CARDS_HARNESS = r"""
const domContext = {
    domEl, domClear, domFragment,
    createVirtualList,
    attachLongPress: () => {},
    safeRender: (_name, fn) => fn(),
    formatPrice: (value) => (value == null ? "Договорная" : `${value} BYN`),
    formatCondition: (value) => value,
    formatSeller: (value) => value,
    formatDelta: (value) => `${value > 0 ? "+" : ""}${value}%`,
    deltaClass: (value) => (value > 0 ? "over" : "under"),
    formatDate: (value) => String(value || ""),
    hasTelegramInitData: () => true,
    safeKufarUrl: () => "",
    safeImageUrl: (url) => url || "",
    optimizedImage: (url) => url,
    openExternalLink() {},
    escapeHtml: (value) => String(value),
};
"""


def test_rv_fe05_deals_list_shows_error_state_with_retry() -> None:
    """RV-FE-05: an error with no data is an error state with
    "Повторить" (never "Нет сделок"), an error over old data is a notice
    above the kept list."""
    harness = _FAKE_DOM_JS + _js(
        "dom_helpers.js", "virtual_list.js", "render_core.js",
        "render_card_builders.js", "render_cards.js",
    ) + _CARDS_HARNESS + r"""
const assert = require("assert");
const reloads = [];
const state = {
    leads: {
        items: [], error: "Нет соединения с сервером.", _loading: false, itemsFilter: "purchases",
    },
    watchlist: { items: [], error: null, _loading: false },
    misc: { stats: {} },
    filters: {},
};
const elements = { leadInboxList: el("div", { role: "tabpanel" }), itemsFilterButtons: [] };
document.body.append(elements.leadInboxList);
const actions = {
    loadLeads: () => reloads.push("leads"),
    loadWatchlist: () => reloads.push("watchlist"),
};
const context = { ...domContext, state, elements, actions };
context.buildEmptyState = createRenderCore(context).buildEmptyState;
const cards = createRenderCards(context);
const list = elements.leadInboxList;

cards.renderLeads();
const errorState = list.querySelector(".empty-state--error");
assert.ok(errorState, "error state instead of the empty state");
assert.ok(errorState.textContent.includes("Не удалось загрузить покупки"));
assert.ok(errorState.textContent.includes("Нет соединения с сервером."));
assert.ok(!list.textContent.includes("Нет сделок в работе"));
errorState.querySelector("button").click();
assert.deepStrictEqual(reloads, ["leads"]);

state.leads.items = [{ id: 1, ad_id: 9, title: "Лот", status: "new" }];
cards.renderLeads();
const notice = list.querySelector(".collection-error-notice");
assert.ok(notice, "a notice above the kept list");
assert.strictEqual(notice.closest("[role=list]"), null, "the notice is not a list child");
assert.strictEqual(list.querySelectorAll("[role=list] > [role=listitem]").length, 1);

state.leads.error = null;
state.leads.items = [];
cards.renderLeads();
assert.ok(list.textContent.includes("Нет сделок в работе"));

state.leads._loading = true;
cards.renderLeads();
assert.strictEqual(
    list.querySelectorAll(".skeleton-card").length, 3, "first load shows skeletons",
);
"""
    _node_ok(harness)


def test_rv_fe05_request_json_turns_401_into_session_banner() -> None:
    """RV-FE-05 / RV-FE-04 / RV-SEC-05: requestJson maps 401 to an
    actionable Russian message plus the session banner, network
    failures to a Russian offline message, announces a service-worker
    saved copy with its time, and refuses requests after account
    deletion."""
    harness = _js("api_core.js") + r"""
const assert = require("assert");
globalThis.window = globalThis;
const banners = [];
const toasts = [];
const state = { ui: {} };
const core = createApiCore({
    state,
    showSessionBanner: (text) => banners.push(text),
    showToast: (...args) => toasts.push(args),
});
let respond;
let fetchCalls = 0;
const reply = (status, payload) => new Response(JSON.stringify(payload), { status });
globalThis.fetch = async (...args) => { fetchCalls += 1; return respond(...args); };

(async () => {
    window.Telegram = { WebApp: { initData: "query_id=1" } };
    respond = () => reply(401, { detail: "Invalid Telegram authentication" });
    await assert.rejects(core.getJson("/api/v1/leads"), (err) => {
        assert.strictEqual(err.status, 401);
        assert.strictEqual(err.sessionExpired, true);
        assert.strictEqual(err.message, "Сессия Telegram истекла — перезапустите приложение.");
        return true;
    });
    assert.deepStrictEqual(banners, ["Сессия Telegram истекла — перезапустите приложение."]);

    window.Telegram = undefined;
    await assert.rejects(core.getJson("/api/v1/leads"), /Откройте Rafuk через Telegram/);

    respond = () => reply(403, { detail: "Account is suspended" });
    await assert.rejects(core.getJson("/api/v1/leads"), /Доступ к аккаунту временно ограничен/);

    respond = () => { throw new TypeError("Failed to fetch"); };
    await assert.rejects(core.getJson("/api/v1/leads", { retry: false }), (err) => {
        assert.strictEqual(err.offline, true);
        assert.ok(err.message.startsWith("Нет соединения с сервером"));
        return true;
    });

    respond = () => new Response(JSON.stringify({ listings: [] }), {
        status: 200,
        headers: { "X-SW-Fallback": "1", Date: "Tue, 20 Oct 2026 09:05:00 GMT" },
    });
    assert.deepStrictEqual(await core.getJson("/api/v1/listings?query=x"), { listings: [] });
    assert.strictEqual(toasts.length, 1);
    assert.ok(toasts[0][0].includes("сохранённые данные от"), toasts[0][0]);

    state.ui.accountDeleted = true;
    const calls = fetchCalls;
    await assert.rejects(core.getJson("/api/v1/profile/me"), (err) => err.accountDeleted === true);
    assert.strictEqual(fetchCalls, calls, "nothing leaves the app after deletion");
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_fe05_session_banner_is_single_and_closes_app() -> None:
    harness = _FAKE_DOM_JS + _js("dom_helpers.js", "render_core.js") + r"""
const assert = require("assert");
let closed = 0;
window.Telegram = { WebApp: { close: () => { closed += 1; } } };
const core = createRenderCore({ state: {}, elements: {} });
core.showSessionBanner("Сессия Telegram истекла — перезапустите приложение.");
core.showSessionBanner("Сессия Telegram истекла — перезапустите приложение.");
const banners = document.querySelectorAll(".session-banner");
assert.strictEqual(banners.length, 1);
assert.strictEqual(banners[0].getAttribute("role"), "alert");
banners[0].querySelector("button").click();
assert.strictEqual(closed, 1);
"""
    _node_ok(harness)


_SW_HARNESS = r"""
const assert = require("assert");
const ORIGIN = "https://app.example";
const nativeSetTimeout = setTimeout;
const handlers = {};
globalThis.self = {
    location: { origin: ORIGIN },
    navigator: { onLine: true },
    clients: { claim: async () => {} },
    skipWaiting() {},
    addEventListener(type, fn) { handlers[type] = fn; },
};
class FakeCache {
    constructor() { this.store = new Map(); }
    _key(request) {
        return typeof request === "string" ? new URL(request, ORIGIN).href : request.url;
    }
    async match(request) {
        const hit = this.store.get(this._key(request));
        return hit ? hit.clone() : undefined;
    }
    async put(request, response) { this.store.set(this._key(request), response); }
    async keys() { return [...this.store.keys()].map((url) => new Request(url)); }
    async delete(request) { return this.store.delete(this._key(request)); }
    async addAll() {}
}
const buckets = new Map();
globalThis.caches = {
    async open(name) {
        if (!buckets.has(name)) buckets.set(name, new FakeCache());
        return buckets.get(name);
    },
    async keys() { return [...buckets.keys()]; },
    async delete(name) { return buckets.delete(name); },
    async match(request) {
        for (const cache of buckets.values()) {
            const hit = await cache.match(request);
            if (hit) return hit;
        }
        return undefined;
    },
};
const bucket = (suffix) => [...buckets.entries()].find(([name]) => name.endsWith(suffix))?.[1];
let network;
let fetchCalls = 0;
globalThis.fetch = async (request) => { fetchCalls += 1; return network(request); };
function basic(body, init = {}) {
    const response = new Response(body, init);
    Object.defineProperty(response, "type", { value: "basic" });
    return response;
}
function json(payload, init = {}) {
    return basic(JSON.stringify(payload), {
        status: init.status || 200,
        headers: {
            "Content-Type": "application/json",
            Date: new Date().toUTCString(),
            ...(init.headers || {}),
        },
    });
}
async function dispatch(path, init = {}) {
    let responded = null;
    const waits = [];
    handlers.fetch({
        request: new Request(new URL(path, ORIGIN).href, init),
        respondWith(promise) { responded = Promise.resolve(promise); },
        waitUntil(promise) { waits.push(promise); },
    });
    const response = responded ? await responded : null;
    // Let cache writes land, without waiting on a deliberately hung fetch.
    await Promise.race([Promise.all(waits), new Promise((r) => nativeSetTimeout(r, 50))]);
    return response;
}
"""


def test_rv_fe04_service_worker_api_is_network_first_with_marked_fallback() -> None:
    """RV-FE-04: live answers win, the cache only stands in when the
    network fails (marked for the page), never for force_refresh, and
    being offline no longer short-circuits to a 503 before the cache."""
    harness = _SW_HARNESS + Path("frontend/sw.js").read_text(encoding="utf-8") + r"""
(async () => {
    const url = "/api/v1/listings?query=iphone";
    network = () => json({ fresh: 1 });
    let response = await dispatch(url);
    assert.deepStrictEqual(await response.json(), { fresh: 1 });
    assert.strictEqual(response.headers.get("X-SW-Fallback"), null);
    assert.ok(await bucket("-runtime").match(new URL(url, ORIGIN).href), "kept as fallback");

    network = () => json({ fresh: 2 });
    response = await dispatch(url);
    assert.deepStrictEqual(await response.json(), { fresh: 2 }, "no stale answer while online");

    self.navigator.onLine = false;
    network = () => { throw new TypeError("Failed to fetch"); };
    response = await dispatch(url);
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.headers.get("X-SW-Fallback"), "1");
    assert.deepStrictEqual(await response.json(), { fresh: 2 });

    response = await dispatch("/api/v1/segments?query=never-seen");
    assert.strictEqual(response.status, 503);
    const body = await response.json();
    assert.strictEqual(body.detail.error, "offline");
    assert.ok(body.detail.message.startsWith("Нет соединения"));
    self.navigator.onLine = true;

    const calls = fetchCalls;
    const forced = await dispatch(`${url}&force_refresh=1`);
    assert.strictEqual(forced, null, "force_refresh passes through");
    const noStore = await dispatch(url, { cache: "no-store" });
    assert.strictEqual(noStore, null, "no-store passes through");
    assert.strictEqual(fetchCalls, calls);

    network = () => json({ detail: "bad gateway" }, { status: 502 });
    response = await dispatch(url);
    assert.strictEqual(response.headers.get("X-SW-Fallback"), "1", "gateway errors fall back");
    network = () => json({ detail: "boom" }, { status: 500 });
    response = await dispatch(url);
    assert.strictEqual(response.status, 500, "application errors pass through");

    network = () => json({ private: true }, { headers: { "Cache-Control": "private, no-store" } });
    await dispatch("/api/v1/geography?query=p");
    const privateHit = await bucket("-runtime").match(`${ORIGIN}/api/v1/geography?query=p`);
    assert.strictEqual(privateHit, undefined, "private responses are not stored");

    const old = "/api/v1/price-history?query=old";
    const twoHoursAgo = new Date(Date.now() - 2 * 3600 * 1000).toUTCString();
    network = () => json({ old: true }, { headers: { Date: twoHoursAgo } });
    await dispatch(old);
    network = () => { throw new TypeError("Failed to fetch"); };
    response = await dispatch(old);
    assert.strictEqual(response.status, 503, "copies older than the ceiling are not served");
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_fe04_service_worker_timeout_and_bounded_image_cache() -> None:
    """RV-FE-04: a hanging network falls back to a recent copy after the
    timeout (and keeps waiting when there is none); proxied photos live
    in their own bucket, capped instead of piling up in the static one."""
    harness = _SW_HARNESS + Path("frontend/sw.js").read_text(encoding="utf-8") + r"""
const realSetTimeout = setTimeout;
globalThis.setTimeout = (fn, ms) => realSetTimeout(fn, ms >= 10000 ? 0 : ms);
(async () => {
    const url = "/api/v1/price-stats?query=iphone";
    network = () => json({ live: 1 });
    await dispatch(url);
    network = () => new Promise(() => {});
    const hung = new Promise((resolve) => realSetTimeout(() => resolve("hung"), 500));
    const fallback = await Promise.race([dispatch(url), hung]);
    assert.notStrictEqual(fallback, "hung", "a hanging network must not hang the page");
    assert.strictEqual(fallback.headers.get("X-SW-Fallback"), "1");

    let release;
    network = () => new Promise((resolve) => { release = () => resolve(json({ late: true })); });
    const pending = dispatch("/api/v1/price-stats?query=fresh");
    realSetTimeout(() => release(), 20);
    assert.deepStrictEqual(await (await pending).json(), { late: true }, "no copy: keep waiting");

    network = (request) => basic(`img:${request.url}`, {
        headers: { "Content-Type": "image/webp" },
    });
    for (let i = 0; i < 130; i += 1) {
        await dispatch(`/api/v1/img/adim1/${i}.jpg?w=240`);
    }
    const images = bucket("-img");
    assert.ok(images, "photos go to their own bucket");
    assert.ok(images.store.size <= 120, `bounded, got ${images.store.size}`);
    const staticBucket = bucket("-static");
    const staticKeys = staticBucket ? [...staticBucket.store.keys()] : [];
    assert.ok(!staticKeys.some((key) => key.includes("/api/v1/img/")), "not in the static bucket");
    const calls = fetchCalls;
    await dispatch("/api/v1/img/adim1/129.jpg?w=240");
    assert.strictEqual(fetchCalls, calls, "a cached photo is served without the network");
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_ui01_privacy_policy_stacks_over_consent_and_owns_escape() -> None:
    """RV-UI-01: the policy opened from the consent gate becomes the
    active dialog (focus, inert, scroll lock); Escape closes only it,
    then only the gate; nothing leaks to the app's global handler."""
    harness = _FAKE_DOM_JS + _js("dom_helpers.js") + _ACTIONS_STUBS + r"""
function createApiCore() {
    return {
        telegramHeaders: () => ({}),
        requestJson: noop,
        getJson: async (url) => (url.includes("ai-consent-info")
            ? { display_label: "AI", policy_version: "1" }
            : { granted: false }),
        postJson: noop,
        deleteJson: noop,
        buildCommonQuery: noop,
    };
}
""" + _js("app_actions.js") + r"""
const assert = require("assert");
const app = el("div", { id: "app-root" });
const main = el("main", {}, el("button", { id: "detail-ai" }, "AI"));
const consent = el("div", { id: "consent-modal", class: "modal-overlay", hidden: true },
    el("div", { class: "modal-content", role: "dialog" },
        el("span", { id: "consent-ai-provider-label" }),
        el("input", { id: "consent-ai-checkbox", type: "checkbox" }),
        el("input", { id: "consent-cross-border-checkbox", type: "checkbox" }),
        el("input", { id: "consent-pd-checkbox", type: "checkbox" }),
        el("a", { id: "consent-privacy-link", href: "#" }, "политикой"),
        el("button", { id: "consent-accept-btn" }, "Согласен"),
        el("button", { id: "consent-cancel-btn" }, "Отмена")));
const privacy = el("div", { id: "privacy-modal", class: "detail-modal", hidden: true },
    el("div", { id: "privacy-overlay", class: "detail-overlay" }),
    el("div", { class: "detail-sheet", role: "dialog" },
        el("div", { class: "modal-header" }, el("button", { id: "privacy-modal-close" }, "×")),
        el("div", { class: "detail-sheet-content privacy-content" }, "…")));
app.append(main, consent, privacy);
document.body.append(app);
let globalEscapes = 0;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") globalEscapes += 1; });
const actions = createAppActions({
    state: { search: { recentSearches: [] }, misc: {}, ui: {}, profile: {} },
    elements: { views: {} },
    markDirty: noop,
    renderAll: noop,
    showToast: noop,
    trapFocus,
});
const link = document.getElementById("consent-privacy-link");
const closeButton = document.getElementById("privacy-modal-close");

(async () => {
    const gate = actions.checkAiConsent();
    await tick(5);
    assert.strictEqual(consent.hidden, false);
    assert.ok(main.hasAttribute("inert"));
    assert.ok(privacy.hasAttribute("inert"), "the gate inerts its siblings");

    link.focus();
    link.click();
    assert.strictEqual(privacy.hidden, false);
    assert.ok(!privacy.hasAttribute("inert"), "the policy is lifted out of the gate's inert");
    assert.ok(consent.hasAttribute("inert"), "the gate underneath goes inert");
    assert.strictEqual(document.activeElement, closeButton, "focus moves into the policy");
    assert.ok(document.body.classList.contains("modal-open"));

    keydown(closeButton, "Escape");
    assert.strictEqual(privacy.hidden, true);
    assert.strictEqual(consent.hidden, false, "Escape closed only the policy");
    assert.ok(!consent.hasAttribute("inert"));
    assert.ok(privacy.hasAttribute("inert"), "the gate's inert is handed back");
    assert.strictEqual(document.activeElement, link, "focus returns to the link");
    assert.ok(!document.body.classList.contains("modal-open"));
    assert.strictEqual(globalEscapes, 0, "the app's Escape handler never saw it");

    keydown(link, "Escape");
    await assert.rejects(gate, /consent_denied/);
    assert.strictEqual(consent.hidden, true);
    assert.strictEqual(globalEscapes, 0, "the gate swallows its own Escape too");
    assert.ok(!main.hasAttribute("inert"));
})()""" + _NODE_FAIL
    _node_ok(harness)


def _css_rule_values(css: str, selector: str, prop: str) -> list[str]:
    values = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            for decl in match.group(2).split(";"):
                name, _, value = decl.partition(":")
                if name.strip() == prop:
                    values.append(value.strip())
    return values


def test_rv_ui01_privacy_modal_css_sits_above_dialogs_with_gutters() -> None:
    parts = CSS_DIR / "parts"
    css = "\n".join(
        (parts / f"{name}.css").read_text(encoding="utf-8")
        for name in ("tokens", "layout", "modals", "pipeline", "states", "ai", "brand")
    )
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    overlay_z = max(int(v) for v in _css_rule_values(css, ".modal-overlay", "z-index"))
    privacy_z = int(_css_rule_values(css, "#privacy-modal", "z-index")[-1])
    toast_z = int(_css_rule_values(css, ".toast-container", "z-index")[-1])
    assert overlay_z < privacy_z < toast_z
    padding = _css_rule_values(css, ".privacy-content", "padding")[-1]
    assert "safe-area-inset-left" in padding and "safe-area-inset-right" in padding
    assert "safe-area-inset-bottom" in padding
    assert not re.fullmatch(r"\S+\s+0", padding), "side gutters must not be zero"


def test_rv_ui02_listing_card_is_a_list_item_with_a_described_title_button() -> None:
    """RV-UI-02: no role=button around nested controls; the title button
    is the primary action and price, tags and badges are announced."""
    harness = _FAKE_DOM_JS + _js(
        "dom_helpers.js", "virtual_list.js", "render_core.js",
        "render_card_builders.js", "render_cards.js",
    ) + _CARDS_HARNESS + r"""
const assert = require("assert");
const opened = [];
const added = [];
const state = {
    leads: { items: [] }, watchlist: { items: [] }, misc: { stats: {} }, filters: {},
    listings: { items: [] }, search: { activeQuery: "iphone" }, ui: {},
};
const actions = {
    openListingDetail: (item) => opened.push(item.ad_id),
    addLeadFromListing: async (item) => { added.push(item.ad_id); return false; },
    addWatchlistFromListing: async () => false,
    openWatchlistDetail: (item) => opened.push(`w${item.id}`),
};
const context = { ...domContext, state, elements: {}, actions };
context.buildEmptyState = createRenderCore(context).buildEmptyState;
const builders = createRenderCardBuilders(context);
const item = {
    ad_id: 11, title: "iPhone 14 Pro", price: 1200, condition: "Б/у", seller_type: "Частное",
    list_time: new Date().toISOString(),
    price_vs_median: -15,
    thumbnail: "https://rms.kufar.by/v1/gallery/a.jpg",
};
const card = builders.buildListingNode(item, () => "neutral");
document.body.append(card);
assert.strictEqual(card.getAttribute("role"), "listitem");
assert.strictEqual(card.getAttribute("tabindex"), null);
assert.strictEqual(card.getAttribute("aria-label"), null);
const open = card.querySelector("h3 > button.card-open");
assert.ok(open, "the title is a button inside a heading");
assert.strictEqual(open.textContent, "iPhone 14 Pro");
const described = open.getAttribute("aria-describedby").split(" ")
    .map((id) => document.getElementById(id))
    .map((node) => node && node.textContent)
    .join(" | ");
for (const part of ["1200 BYN", "Б/у", "Частное", "Новое", "-15%", "ниже среднего по запросу"]) {
    assert.ok(described.includes(part), `${part} missing from "${described}"`);
}
const photoAlt = card.querySelector("img").getAttribute("alt");
assert.strictEqual(photoAlt, "", "the photo does not repeat the title");
for (const control of card.querySelectorAll("button, a, input")) {
    assert.strictEqual(control.parentNode.closest("button, a, input, [role=button]"), null,
        "no interactive control nested in another");
}

const container = el("div");
document.body.append(container);
const renderer = createRenderCards(context);
renderer.renderListingsCollection([item, { ...item, ad_id: 12 }], container, null, "");
assert.strictEqual(container.children.length, 1);
const list = container.firstElementChild;
assert.strictEqual(list.getAttribute("role"), "list");
const strayChildren = list.children.filter((node) => node.getAttribute("role") !== "listitem");
assert.strictEqual(strayChildren.length, 0, "the list holds only list items");
list.querySelector(".card-open").click();
list.querySelector(".listing-badges").click();
list.querySelector('[data-role="lead"]').click();
assert.deepStrictEqual(opened, [11, 11], "title and badges open the detail, actions don't");
assert.deepStrictEqual(added, [11]);

renderer.renderListingsCollection([], container, null, "Пусто");
assert.strictEqual(
    container.querySelector("[role=list]"), null, "the empty state is not inside a list",
);
assert.ok(container.querySelector(".empty-state"));
"""
    _node_ok(harness)


def test_rv_ui02_deal_cards_and_tracker_events_are_list_items() -> None:
    harness = _FAKE_DOM_JS + _js(
        "dom_helpers.js", "virtual_list.js", "render_core.js",
        "render_card_builders.js", "render_trackers.js",
    ) + _CARDS_HARNESS + r"""
const assert = require("assert");
const opened = [];
const state = {
    leads: { items: [] }, watchlist: { items: [] }, misc: { stats: {} },
    trackers: {
        items: [],
        events: [{
            ad_id: 5, title: "Велосипед", event_type: "price_drop", price_byn: 300,
            delta_byn: 50, region_name: "Минск", query: "велосипед", tracker_id: 1,
            created_at: "2026-10-20",
        }],
        eventFilter: "all",
        eventFilterTrackerId: null,
    },
};
const actions = {
    openWatchlistDetail: (item) => opened.push(`watch:${item.id}`),
    openListingDetail: (item) => opened.push(`event:${item.ad_id}`),
};
const elements = { trackerEventsList: el("div"), trackerEventFilterButtons: [] };
document.body.append(elements.trackerEventsList);
const context = { ...domContext, state, elements, actions };
context.buildEmptyState = createRenderCore(context).buildEmptyState;
const builders = createRenderCardBuilders(context);

const watchItem = { id: 3, ad_id: 8, title: "Ноутбук", current_price_byn: 900 };
const watch = builders.buildWatchlistNode(watchItem, (value) => value);
document.body.append(watch);
assert.strictEqual(watch.getAttribute("role"), "listitem");
assert.strictEqual(watch.getAttribute("tabindex"), null);
assert.ok(watch.querySelector(".watchlist-card-top .card-open"));
const watchDescribedBy = watch.querySelector(".card-open").getAttribute("aria-describedby");
const firstDescription = document.getElementById(watchDescribedBy.split(" ")[0]);
assert.ok(firstDescription.textContent.includes("900 BYN"));
watch.querySelector(".card-open").click();
assert.deepStrictEqual(opened, ["watch:3"]);

const leadItem = { id: 4, ad_id: 9, title: "Консоль", status: "new", price_byn: 100 };
const lead = builders.buildLeadNode(leadItem);
assert.strictEqual(lead.getAttribute("role"), "listitem");
assert.strictEqual(lead.getAttribute("tabindex"), null);
assert.strictEqual(lead.getAttribute("aria-label"), null);
assert.ok(lead.querySelector("h3 .sr-only").textContent.includes("ожидает подтверждения"));

const trackers = createRenderTrackers(context);
trackers.renderTrackerEvents();
const list = elements.trackerEventsList.querySelector("[role=list]");
assert.ok(list);
const eventCard = list.querySelector("[role=listitem]");
assert.ok(eventCard);
assert.strictEqual(elements.trackerEventsList.querySelectorAll("[role=button]").length, 0);
eventCard.querySelector(".event-header .card-open").click();
assert.deepStrictEqual(opened, ["watch:3", "event:5"]);
const eventDescribedBy = eventCard.querySelector(".card-open").getAttribute("aria-describedby");
const description = eventDescribedBy.split(" ")
    .map((id) => document.getElementById(id).textContent)
    .join(" ");
assert.ok(description.includes("Падение цены") && description.includes("300 BYN"), description);
"""
    _node_ok(harness)


def test_rv_ui02_virtual_list_keeps_a_single_list_role() -> None:
    harness = _FAKE_DOM_JS + _js("dom_helpers.js", "virtual_list.js") + r"""
const assert = require("assert");
const listContainer = el("div", { role: "list" });
const panel = el("div", { role: "tabpanel" });
const plain = el("div");
for (const container of [listContainer, panel, plain]) {
    document.body.append(container);
    createVirtualList(container, { renderFn: () => el("div", { role: "listitem" }) });
}
const viewportOf = (container) => container.children[1];
assert.strictEqual(viewportOf(listContainer).getAttribute("role"), "none");
assert.strictEqual(viewportOf(panel).getAttribute("role"), "list");
assert.strictEqual(panel.getAttribute("role"), "tabpanel");
assert.strictEqual(viewportOf(plain).getAttribute("role"), "list");
assert.strictEqual(plain.getAttribute("role"), null, "no second list role on the container");
"""
    _node_ok(harness)


def test_rv_ui02_card_containers_and_builders_drop_role_button() -> None:
    index_html = Path("frontend/index.html").read_text(encoding="utf-8")
    containers = ("listings-list", "trackers-list", "tracker-events-list", "history-deals-list")
    for element_id in containers:
        tag = re.search(rf'<div[^>]*id="{element_id}"[^>]*>', index_html).group(0)
        assert 'role="list"' not in tag, f"#{element_id}: the inner .card-list is the list"
    for name in ("render_card_builders.js", "render_trackers.js", "render_cards.js"):
        source = (JS_DIR / name).read_text(encoding="utf-8")
        assert 'role: "button"' not in source, name
    css = _read_all_css()
    assert ".card-open::after" in css and "inset: 0" in css


def _hex_luminance(color: str) -> float:
    color = color.lstrip("#")
    channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    high, low = sorted((_hex_luminance(fg), _hex_luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _blend(rgba: str, base: str = "#ffffff") -> str:
    r, g, b, a = (float(x) for x in re.findall(r"[\d.]+", rgba))
    base_rgb = [int(base.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    mixed = [round(a * c + (1 - a) * bc) for c, bc in zip((r, g, b), base_rgb, strict=True)]
    return "#" + "".join(f"{c:02x}" for c in mixed)


def _theme_block(css: str, selector: str) -> dict[str, str]:
    start = css.index(f"{selector} {{")
    body = css[start:css.index("}", start)]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def test_rv_ui03_light_theme_text_tokens_pass_aa() -> None:
    """RV-UI-03: semantic text in the light theme reads *-text tokens
    that reach 4.5:1 on the page, the cards and the 6% badge tints."""
    tokens_css = (CSS_DIR / "parts" / "tokens.css").read_text(encoding="utf-8")
    light = _theme_block(tokens_css, '[data-theme="light"]')
    dark = _theme_block(tokens_css, ":root")
    surfaces = ["#ffffff", light["--bg"], light["--bg-card"]]
    for name, soft in (("green", "--green-soft"), ("amber", "--amber-soft"),
                       ("cyan", "--cyan-soft"), ("red", "--red-soft")):
        text = light[f"--{name}-text"]
        for surface in [*surfaces, _blend(light[soft])]:
            assert _contrast(text, surface) >= 4.5, f"{name}-text {text} on {surface}"
        fill = dark[dark[f"--{name}-text"][len("var("):-1]]
        for surface in (dark["--bg"], dark["--bg-card"]):
            assert _contrast(fill, surface) >= 4.5, f"dark {name}-text on {surface}"


def test_rv_ui03_text_colours_never_use_bright_fill_tokens() -> None:
    offenders = []
    pattern = re.compile(r"(?<![-\w])color:\s*var\(--(?:green|amber|amber-400|cyan|red|red-400)\)")
    for path in sorted((CSS_DIR / "parts").glob("*.css")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{line_no}")
    assert not offenders, "use the *-text tokens for text: " + ", ".join(offenders)


def test_rv_sec05_account_deletion_closes_app_without_reprovisioning() -> None:
    """RV-SEC-05: after DELETE /account nothing reaches the API again (the
    backend would re-create the user from the same initData), local data
    and SW caches are cleared, the Mini App closes and a dead-end screen
    replaces the app — no reload."""
    modules = _js("dom_helpers.js") + _ACTIONS_STUBS + _js("api_core.js", "app_actions.js")
    harness = _FAKE_DOM_JS + modules + r"""
const assert = require("assert");
let closed = 0;
let reloaded = 0;
location.reload = () => { reloaded += 1; };
window.Telegram = { WebApp: {
    initData: "query_id=1",
    initDataUnsafe: { user: { id: 5, first_name: "Ivan" } },
    close: () => { closed += 1; },
} };
const deletedCaches = [];
globalThis.caches = {
    keys: async () => ["rafuk-cache-v1-static", "rafuk-cache-v1-runtime", "rafuk-cache-v1-img"],
    delete: async (name) => { deletedCaches.push(name); return true; },
};
const fetches = [];
globalThis.fetch = async (url, options = {}) => {
    fetches.push(`${options.method || "GET"} ${url}`);
    return new Response(null, { status: 204 });
};
localStorage.setItem("recentSearches", "[]");
localStorage.setItem("rafuk:assistant-history", "[]");
localStorage.setItem("theme", "dark");
const appRoot = el("div", { id: "app-root" }, el("main"));
document.body.append(appRoot);
const state = { search: { recentSearches: ["iphone"] }, misc: {}, ui: {}, profile: {} };
const toasts = [];
const actions = createAppActions({
    state,
    elements: { views: {} },
    markDirty: noop,
    renderAll: noop,
    showToast: (...args) => toasts.push(args),
    trapFocus,
});

(async () => {
    const done = actions.deleteAccount();
    await tick(5);
    const input = document.querySelector(".typed-confirm-input");
    input.value = "ivan";
    fire(input, "input");
    document.querySelector(".typed-confirm-modal [data-role='confirm']").click();
    await done;
    await tick(5);

    assert.deepStrictEqual(fetches, ["DELETE /api/v1/account"]);
    assert.strictEqual(localStorage.getItem("recentSearches"), null);
    assert.strictEqual(localStorage.getItem("rafuk:assistant-history"), null);
    assert.strictEqual(localStorage.getItem("theme"), "dark");
    assert.deepStrictEqual(deletedCaches.sort(), ["rafuk-cache-v1-img", "rafuk-cache-v1-runtime"]);
    assert.ok(stubCalls.includes("trackers.stopTrackerRefresh"));
    assert.strictEqual(appRoot.hidden, true);
    const screen = document.querySelector(".account-deleted-screen");
    assert.ok(screen && screen.textContent.includes("Аккаунт удалён"));
    assert.strictEqual(screen.getAttribute("role"), "alert");

    await actions.refreshLeads();
    assert.deepStrictEqual(fetches, ["DELETE /api/v1/account"], "no request after deletion");

    await tick(1300);
    assert.strictEqual(closed, 1, "the Mini App is closed");
    assert.strictEqual(reloaded, 0, "never reloads with the same initData");
})()""" + _NODE_FAIL
    _node_ok(harness)


_AI_HARNESS = r"""
const assert = require("assert");
let now = 1_000_000;
const startedAt = now;
Date.now = () => now;
const realSetTimeout = setTimeout;
// Fake clock: every timer runs on the next turn and advances time by its delay.
globalThis.setTimeout = (fn, ms = 0) => realSetTimeout(() => { now += ms; fn(); }, 0);
globalThis.window = globalThis;
const posts = [];
const results = [];
const errors = [];
let taskResponder = () => ({ status: "processing", stage: "calling_ai" });
let startResponder = () => ({ task_id: `task-${posts.length}` });
window.App = {
    createAiModal: () => ({
        openAIModal() {}, stopLoadingAnimation() {}, showLoaderError() {}, closeAIModal() {},
        setStageLabel() {}, showCompletionThen(cb) { cb(); },
    }),
    createAiRender: () => ({
        renderAIModalResult: (result) => results.push(result),
        renderAiErrorState: (_node, message) => errors.push(message),
    }),
};
"""

_AI_CONTEXT = r"""
const state = {
    search: { activeQuery: "iphone" },
    filters: {},
    detail: { data: { query: "iphone", title: "iPhone" }, ai: null },
};
const api = window.App.createApiAi({
    state,
    elements: { aiModalLoading: {}, aiModalResult: {}, aiModalError: {} },
    postJson: async (url, body) => { posts.push(body.ad_id); return startResponder(body); },
    getJson: async (url) => taskResponder(url),
    logClientError() {},
});
"""


def test_rv_ai07_analysis_waits_for_backend_budget_and_resumes_the_same_task() -> None:
    """RV-AI-07: the client waits longer than the backend's own budget
    (ai_analysis_timeout plus search/photo stages), and "Повторить"
    after a client-side give-up resumes the running task instead of
    starting — and paying for — a second one."""
    config = Path("api/config.py").read_text(encoding="utf-8")
    backend_timeout = int(re.search(r"ai_analysis_timeout:\s*int\s*=\s*(\d+)", config).group(1))
    harness = _AI_HARNESS + _js("api_ai.js") + _AI_CONTEXT + r"""
(async () => {
    await api.loadAIAnalysis(42);
    const waitedSeconds = (now - startedAt) / 1000;
    assert.ok(waitedSeconds >= __BACKEND__ + 60, `gave up after ${waitedSeconds}s`);
    assert.strictEqual(posts.length, 1);
    await new Promise((r) => realSetTimeout(r, 5));
    assert.ok(errors.at(-1).includes("продолжит ждать"), errors.at(-1));

    taskResponder = () => ({ status: "done", result: { verdict: "ok" } });
    await api.loadAIAnalysis(42);
    assert.strictEqual(posts.length, 1, "retry resumed the running task");
    assert.deepStrictEqual(results, [{ verdict: "ok" }]);

    // A finished task is not resumed; another listing starts its own.
    await api.loadAIAnalysis(43);
    assert.strictEqual(posts.length, 2);

    // A resumed task that expired server-side is replaced by a new one.
    taskResponder = () => ({ status: "processing" });
    startResponder = () => ({ task_id: "slow", poll_timeout_seconds: 1 });
    await api.loadAIAnalysis(44);
    assert.strictEqual(posts.length, 3);
    let polls = 0;
    taskResponder = () => {
        polls += 1;
        if (polls === 1) {
            const err = new Error("Задача не найдена");
            err.status = 404;
            throw err;
        }
        return { status: "done", result: { verdict: "again" } };
    };
    startResponder = () => ({ task_id: "fresh" });
    await api.loadAIAnalysis(44);
    assert.strictEqual(posts.length, 4, "an expired task is started afresh");
    assert.deepStrictEqual(results.at(-1), { verdict: "again" });
})()""".replace("__BACKEND__", str(backend_timeout)) + _NODE_FAIL
    _node_ok(harness)


def test_rv_ai07_server_announced_poll_budget_wins() -> None:
    harness = _AI_HARNESS + _js("api_ai.js") + _AI_CONTEXT + r"""
startResponder = () => ({ task_id: "long", poll_timeout_seconds: 400 });
(async () => {
    await api.loadAIAnalysis(7);
    const waitedSeconds = (now - startedAt) / 1000;
    assert.ok(waitedSeconds >= 400 && waitedSeconds < 480, `waited ${waitedSeconds}s`);
})()""" + _NODE_FAIL
    _node_ok(harness)


def test_rv_ai07_listing_assistant_timeout_covers_backend_budget() -> None:
    source = (JS_DIR / "api_listing_assistant.js").read_text(encoding="utf-8")
    raw = re.search(r"const LISTING_ASSISTANT_TIMEOUT_MS = ([\d_]+);", source).group(1)
    timeout_ms = int(raw.replace("_", ""))
    router = Path("api/routers/ai_listing_assistant.py").read_text(encoding="utf-8")
    backend = int(re.search(r'"ai_listing_assistant_timeout", (\d+)\)', router).group(1))
    # Model call plus the strict-then-broad Kufar search (2 × kufar_timeout).
    assert timeout_ms / 1000 > backend + 30
    call = 'postJson("/api/v1/ai/listing-assistant", payload, {\n'
    assert call + "                timeout: LISTING_ASSISTANT_TIMEOUT_MS," in source
