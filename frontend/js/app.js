function analyticsApp() {
    const core = createAppCore();
    const actions = {};
    const renderers = createAppRenderers({ ...core, actions });
    Object.assign(actions, createAppActions({ ...core, ...renderers }));

    function rerenderCharts() {
        // Charts read theme colors when drawn, so redraw them after a theme switch.
        try {
            renderers.renderChart();
            renderers.renderHistoryChart();
        } catch (_) {
            // A missing Chart.js must not break the theme switch.
        }
    }

    function init() {
        core.cacheElements();
        core.initTelegramTheme(rerenderCharts);
        actions.bindEvents();
        core.state.query = core.elements.searchInput.value.trim();
        renderers.renderAll();
        void actions.loadRates();
        void actions.loadTrackers();
        void actions.loadLeads();
        void actions.loadWatchlist();
        // The opportunity board is loaded lazily when the deals view is shown.
        void actions.applyLaunchParams();
    }

    function search(...args) {
        return actions.search(...args);
    }

    function loadListings(...args) {
        return actions.loadListings(...args);
    }

    function loadDeals(...args) {
        return actions.loadDeals(...args);
    }

    function renderChart(...args) {
        return renderers.renderChart(...args);
    }

    function setCurrency(...args) {
        return actions.setCurrency(...args);
    }

    return {
        init,
        search,
        loadListings,
        loadDeals,
        renderChart,
        renderBoxPlot: renderChart,
        setCurrency,
        formatPrice: core.formatPrice,
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const app = analyticsApp();
    app.init();
});
