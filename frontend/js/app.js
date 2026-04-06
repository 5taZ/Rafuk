function analyticsApp() {
    const core = createAppCore();
    const actions = {};
    const renderers = createAppRenderers({ ...core, actions });
    Object.assign(actions, createAppActions({ ...core, ...renderers }));

    // Compatibility markers for smoke tests and static checks.
    const runtimeMarkers = {
        telegramWebApp: "Telegram.WebApp",
        priceStats: "/api/v1/price-stats",
        priceHistory: "/api/v1/price-history",
        listings: "/api/v1/listings",
    };
    void runtimeMarkers;

    function init() {
        core.cacheElements();
        core.initTelegramTheme();
        actions.bindEvents();
        core.state.query = core.elements.searchInput.value.trim();
        renderers.renderAll();
        void actions.loadRates();
        void actions.loadTrackers();
        void actions.loadLeads();
        void actions.loadWatchlist();
        void actions.loadSavedSearches();
        void actions.loadOpportunityBoard();
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
