/**
 * _lazy_charts_stub.js — OPUS-13 lazy-load shim for render_charts.js.
 *
 * Same pattern as the other lazy stubs (see _lazy_trackers_stub.js
 * for the full design rationale). Charts are only meaningful in
 * the overview / price-history flow plus the deals profit
 * dashboard — moving them out of app_bundle.js saves ~21 KB raw /
 * ~10 KB minified for users who never paint a chart.
 *
 * Chart.js itself was already lazy-loaded by render_charts.js
 * before this split (CHART_JS_URL inside the file). Loading the
 * renderer lazily means the Chart.js handshake doesn't block the
 * initial bundle either.
 */
/* global window */

const _LAZY_CHARTS_RENDER_URL = "js/render_charts.js?v=20260516-4a99cea";

function _lazyLoadChartsSources(context) {
    window.App = window.App || {};
    if (window.App._chartsLoadPromise) return window.App._chartsLoadPromise;
    window.App._chartsLoadPromise = context._loadScript(_LAZY_CHARTS_RENDER_URL).catch((err) => {
        window.App._chartsLoadPromise = null;
        throw err;
    });
    return window.App._chartsLoadPromise;
}

function createRenderCharts(context) {
    const logClientError = context.logClientError || (() => {});
    let _real = null;
    let _initPromise = null;

    function _ensureLoaded() {
        if (_real) return Promise.resolve(_real);
        if (_initPromise) return _initPromise;
        _initPromise = (async () => {
            await _lazyLoadChartsSources(context);
            const factory = (window.App || {})._realCreateRenderCharts;
            if (typeof factory !== "function") {
                throw new Error("render_charts.js did not register factory");
            }
            _real = factory(context);
            return _real;
        })();
        return _initPromise;
    }

    function _delegate(method, args) {
        if (_real) return _real[method].apply(null, args);
        _ensureLoaded()
            .then((real) => {
                real[method].apply(null, args);
                if (typeof context.scheduleRender === "function") {
                    context.scheduleRender();
                }
            })
            .catch((err) => {
                logClientError("charts lazy-load failed:", err);
            });
        return undefined;
    }

    return {
        destroyChart: (...args) => _delegate("destroyChart", args),
        destroyHistoryChart: (...args) => _delegate("destroyHistoryChart", args),
        renderChart: (...args) => _delegate("renderChart", args),
        renderHistory: (...args) => _delegate("renderHistory", args),
        renderHistoryChart: (...args) => _delegate("renderHistoryChart", args),
        renderProfitDashboard: (...args) => _delegate("renderProfitDashboard", args),
        renderHistoryDeals: (...args) => _delegate("renderHistoryDeals", args),
        _ensureLoaded,
    };
}
