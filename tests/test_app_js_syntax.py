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
const state = {
    search: {
        query: "iphone",
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
            query: state.search.query,
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
        query: "iphone",
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
            query: state.search.query,
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

    assert "{ forceRefresh: true }" in app_js
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
    events_js = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
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

    assert "context.fetchProxyImageObjectUrl(proxyUrl)" in events_js
    assert "context.optimizedImage(validated, { width: 800, useProxy: true })" in events_js

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


def test_delete_account_clears_local_account_data_before_reload() -> None:
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
    reload = delete_body.index("window.location.reload()")
    assert server_delete < local_cleanup < reload


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
        "const visibleRefinements = visibleRefinementTokens(refinements, state.search.query);"
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
    events = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
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
    assert "is-zoomed" in events
    assert "e.touches.length > 1" in events or "touches.length > 1" in events

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
