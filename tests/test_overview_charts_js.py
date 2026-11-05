from __future__ import annotations

from tests.test_app_js_syntax import _FAKE_DOM_JS, _NODE_FAIL, _js, _node_ok

_CHART_SETUP = r"""
const assert = require("node:assert/strict");
globalThis.getComputedStyle = () => ({getPropertyValue() { return ""; }});
const created = [];
globalThis.Chart = class {
    constructor(canvas, config) { this.config = config; created.push(this); }
    destroy() { this.destroyed = true; }
};
const {state} = createAppCore();
state.search.activeQuery = "phone";
state.panels.distribution = state.panels.history = true;
const elements = {
    chartSection: el("section"), priceChartCanvas: el("canvas"), distributionNote: el("p"),
    historySection: el("section"), historyChartCanvas: el("canvas"), historyNote: el("p"),
    historyEmpty: el("p", {}, "История ещё копится"), historySummary: el("div"),
    segmentsGrid: el("div"), segmentsSection: el("section"), statsSection: el("section"),
    marketTotalBadge: el("span"),
    stats: Object.fromEntries(["median", "mean", "min", "max", "fairRange", "coverage"]
        .map((key) => [key, el("span")])),
};
el("div", {}, elements.historyChartCanvas);
const context = {
    state, elements, domEl, domClear, domFragment, safeRender: (_, fn) => fn(),
    formatPrice: (price) => `${price} BYN`, formatPriceRange: (a, b) => `${a}–${b} BYN`,
    formatDate: (date) => date,
};
const charts = App._realCreateRenderCharts(context);
context._hooks = {renderChart: charts.renderChart, destroyChart: charts.destroyChart};
const views = createRenderViews(context);
"""


def _chart_harness() -> str:
    return _FAKE_DOM_JS + _js(
        "dom_helpers.js", "app_core_dom.js", "app_core.js", "render_charts.js", "render_views.js",
    ) + _CHART_SETUP


def test_distribution_renders_counts_and_refreshes_an_open_panel() -> None:
    _node_ok(_chart_harness() + r"""
state.misc.stats = {
    count: 4, analyzed_count: 4, total_results: 4, min: 10, max: 30, median: 20,
    histogram: [{from_price: 10, to_price: 20, count: 1},
                {from_price: 20, to_price: 30, count: 3}],
};
views.renderStats();
const original = state.charts.distribution;
assert.deepEqual(original.config.data.datasets[0].data, [1, 3]);
assert.equal(original.config.options.scales.y.beginAtZero, true);
assert.equal(original.config.options.scales.y.ticks.precision, 0);
assert.match(original.config.options.plugins.tooltip.callbacks.label({parsed: {y: 3}}), /75\.0%/);
assert.match(elements.priceChartCanvas.getAttribute("aria-label"), /20 – 30: 3/);
state.misc.stats = {...state.misc.stats, count: 2,
    histogram: [{from_price: 50, to_price: 50, count: 2}]};
views.renderStats();
assert.equal(original.destroyed, true);
assert.deepEqual(state.charts.distribution.config.data.datasets[0].data, [2]);
state.misc.stats = {...state.misc.stats, count: 0, histogram: []};
views.renderStats();
assert.equal(state.charts.distribution, null);
assert.equal(elements.stats.median.textContent, "—");
""")


def test_segment_shares_and_comparisons_do_not_depend_on_filtered_stats() -> None:
    _node_ok(_chart_harness() + r"""
state.misc.stats = {count: 3, median: 200};
state.misc.segments = {
    new_shop: {count: 3, median: 200}, used_private: {count: 6, median: 100},
    reference: {count: 9, median: 100}, sample_count: 9,
};
views.renderSegments();
const shares = () => elements.segmentsGrid.querySelectorAll(".seg-share")
    .map((el) => el.textContent);
assert.deepEqual(shares(), ["33.3% сегментов", "66.7% сегментов"]);
assert.equal(elements.segmentsGrid.querySelector(".seg-delta").textContent, "+100.0% к рынку");
state.misc.stats = null;
views.renderSegments();
assert.deepEqual(shares(), ["33.3% сегментов", "66.7% сегментов"]);
state.misc.segments.new_shop.count = 1;
views.renderSegments();
assert.equal(elements.segmentsGrid.querySelector(".seg-delta").textContent,
    "Мало данных для сравнения");
state.misc.segments = null;
state.misc.segmentsError = true;
views.renderSegments();
assert.equal(elements.segmentsSection.hidden, false);
assert.match(elements.segmentsGrid.textContent, /Не удалось/);
""")


def test_history_uses_actual_times_and_has_no_trend_for_one_observation() -> None:
    _node_ok(_chart_harness() + r"""
const point = (date, median) => ({snapshot_at: date, median, analyzed_count: 10});
const a = point("2026-11-01T00:00:00Z", 100);
const b = point("2026-11-01T01:00:00Z", 80);
const c = point("2026-11-04T00:00:00Z", 120);
state.charts.historyData = [a];
charts.renderHistory();
assert.equal(state.charts.history.config.options.scales.x.max
    - state.charts.history.config.options.scales.x.min, 3_600_000);
const change = () => elements.historySummary.querySelectorAll(".history-summary-value")[1]
    .textContent;
assert.equal(change(), "—");
assert.match(elements.historySummary.textContent, /Нужны минимум два замера/);
state.charts.historyData = [c, a, b];
charts.renderHistory();
const config = state.charts.history.config;
assert.equal(config.options.scales.x.type, "linear");
assert.deepEqual(config.data.datasets[0].data.map((p) => p.x),
    [Date.parse(a.snapshot_at), Date.parse(b.snapshot_at), Date.parse(c.snapshot_at)]);
assert.equal(config.data.datasets[0].tension, 0);
assert.equal(change(), "+20.0%");
const tooltipTitle = config.options.plugins.tooltip.callbacks.title;
assert.equal(tooltipTitle([{parsed: {x: Date.parse(a.snapshot_at)}}]),
    "2026-11-01T00:00:00.000Z");
state.charts.historyData = [point(a.snapshot_at, 0), c];
charts.renderHistory();
assert.equal(change(), "—");
assert.match(elements.historySummary.textContent, /равна нулю/);
state.panels.history = false;
charts.renderHistoryChart();
assert.equal(state.charts.history, null);
state.panels.history = true;
state.charts.historyData = [];
state.charts.historyLoading = true;
charts.renderHistory();
assert.match(elements.historyEmpty.textContent, /Загружаю/);
assert.equal(elements.historyChartCanvas.parentElement.hidden, true);
""")


def test_history_library_finishing_after_panel_close_cannot_recreate_chart() -> None:
    _node_ok(_chart_harness() + r"""
(async () => {
    const LoadedChart = Chart;
    delete globalThis.Chart;
    state.charts.historyData = [
        {snapshot_at: "2026-11-01T00:00:00Z", median: 100, analyzed_count: 3},
    ];
    charts.renderHistory();
    const script = document.head.children.at(-1);
    state.panels.history = false;
    globalThis.Chart = LoadedChart;
    script.onload();
    await tick();
    assert.equal(state.charts.history, null);
    assert.equal(created.length, 0);
})()""" + _NODE_FAIL)


def test_history_waits_for_stats_and_ignores_old_period_and_query_responses() -> None:
    harness = _FAKE_DOM_JS + _js(
        "dom_helpers.js", "app_core_dom.js", "app_core.js", "api_core.js", "api_listings.js",
    ) + r"""
const assert = require("node:assert/strict");
const {state} = createAppCore();
const pendingStats = [];
const pendingHistory = [];
const api = createApiListings({
    state, elements: {}, buildCommonQuery: createApiCore({state}).buildCommonQuery,
    markDirty() {}, renderAll() {}, scheduleRender() {}, focusTarget() {}, renderHistory() {},
    getJson(url, options) {
        const parsed = new URL(url, "http://rafuk.test");
        if (parsed.pathname.endsWith("/price-stats")) {
            return new Promise((resolve) => pendingStats.push({resolve}));
        }
        if (parsed.pathname.endsWith("/price-history")) {
            assert.equal(parsed.searchParams.get("force_refresh"), "1");
            return new Promise((resolve, reject) => {
                pendingHistory.push({resolve, reject, parsed, options});
            });
        }
        return Promise.resolve({listings: [], regions: []});
    },
});
(async () => {
    const search = api.search("overview", {query: "phone"});
    assert.equal(pendingHistory.length, 0, "history waits until its new snapshot is committed");
    pendingStats[0].resolve({categories: [], strict_search: false});
    await search;
    assert.equal(pendingHistory.length, 1);
    assert.equal(pendingHistory[0].parsed.searchParams.get("strict_search"), "false");
    state.misc.historyDays = 30;
    const range = api.loadHistory();
    pendingHistory[1].resolve({points: ["30 days"]});
    await range;
    pendingHistory[0].resolve({points: ["stale 7 days"]});
    await tick();
    assert.deepEqual(state.charts.historyData, ["30 days"]);

    const old = api.loadHistory();
    const nextSearch = api.search("overview", {query: "tablet"});
    assert.equal(pendingHistory[2].options.signal.aborted, true);
    pendingHistory[2].resolve({points: ["stale phone"]});
    await old;
    assert.deepEqual(state.charts.historyData, []);
    pendingStats[1].resolve({categories: []});
    await nextSearch;
    pendingHistory[3].reject(new Error("offline"));
    await tick();
    assert.equal(state.charts.historyError, true);
    const retry = api.loadHistory();
    pendingHistory[4].resolve({points: ["tablet"]});
    await retry;
    assert.equal(state.charts.historyError, false);
    assert.equal(state.charts.historyLoading, false);
    assert.deepEqual(state.charts.historyData, ["tablet"]);
})()""" + _NODE_FAIL
    _node_ok(harness)
