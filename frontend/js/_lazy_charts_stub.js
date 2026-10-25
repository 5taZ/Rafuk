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

const _LAZY_CHARTS_RENDER_URL = "js/render_charts.js?v=20261025-2832914";

function _lazyLoadChartsSources(context) {
    if (_loadPromises.has("charts")) return _loadPromises.get("charts");
    const p = context._loadScript(_LAZY_CHARTS_RENDER_URL).catch((err) => {
        _loadPromises.delete("charts");
        throw err;
    });
    _loadPromises.set("charts", p);
    return p;
}

function createRenderCharts(context) {
    // RV-FE-02: see _createLazyModule (app_core.js).
    const lazy = _createLazyModule(context, {
        load: _lazyLoadChartsSources,
        factoryKey: "_realCreateRenderCharts",
        label: "render_charts.js",
    });

    // FR-08: the distribution / history panels hold a <canvas> the real
    // renderer needs later, so their error state goes into a slot in
    // front of it, removed once the chunk has loaded.
    let errorSlotsShown = false;
    function _errorSlot(panelBody) {
        if (!panelBody) return null;
        let slot = Array.from(panelBody.children).find((child) => child.classList.contains("lazy-load-slot"));
        if (!slot) {
            slot = domEl("div", { className: "lazy-load-slot" });
            panelBody.prepend(slot);
        }
        errorSlotsShown = true;
        return slot;
    }

    function _delegate(method, args) {
        if (errorSlotsShown && lazy.current()) {
            errorSlotsShown = false;
            for (const slot of document.querySelectorAll(".lazy-load-slot")) slot.remove();
        }
        return _lazyRenderCall(lazy, context, "charts", method, args, () => {
            const elements = context.elements || {};
            const panels = elements.panelBodies || {};
            let container = null;
            if (method === "renderHistoryDeals") container = elements.historyDealsList;
            if (method === "renderProfitDashboard") container = elements.profitCards;
            if (method === "renderChart") container = _errorSlot(panels.distribution);
            if (method === "renderHistory" || method === "renderHistoryChart") {
                // renderHistory() normally unhides its section.
                if (!context.state?.search?.activeQuery) return;
                if (elements.historySection) elements.historySection.hidden = false;
                container = _errorSlot(panels.history);
            }
            _renderLazyLoadError(context, container, lazy.retry, undefined, lazy.needsRestart());
        });
    }

    return {
        // No chart can exist before the renderer loaded — don't fetch
        // the chunk just to destroy nothing.
        destroyChart: (...args) => _lazyTeardownCall(lazy, "destroyChart", args),
        destroyHistoryChart: (...args) => _lazyTeardownCall(lazy, "destroyHistoryChart", args),
        renderChart: (...args) => _delegate("renderChart", args),
        renderHistory: (...args) => _delegate("renderHistory", args),
        renderHistoryChart: (...args) => _delegate("renderHistoryChart", args),
        renderProfitDashboard: (...args) => _delegate("renderProfitDashboard", args),
        renderHistoryDeals: (...args) => _delegate("renderHistoryDeals", args),
        _ensureLoaded: lazy.ensure,
    };
}
