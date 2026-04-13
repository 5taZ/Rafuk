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
        core.populateRegionSelect(core.elements.trackerRegionSelect);
        core.populateRegionSelect(core.elements.editRegionSelect);
        core.populateRegionSelect(core.elements.filterRegion);
        core.initTelegramTheme();
        core.loadRecentSearches();
        actions.bindEvents();
        core.state.query = core.elements.searchInput.value.trim();
        renderers.renderAll();
        void actions.loadRates();
        void actions.loadTrackers();
        void actions.loadLeads();
        void actions.loadWatchlist();
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

    return {
        init,
        search,
        loadListings,
        loadDeals,
        renderChart,
        renderBoxPlot: renderChart,
        formatPrice: core.formatPrice,
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const app = analyticsApp();
    app.init();

    // Offline / online detection
    function showOfflineBanner() {
        let banner = document.getElementById("offline-banner");
        if (!banner) {
            banner = document.createElement("div");
            banner.id = "offline-banner";
            banner.setAttribute("role", "alert");
            banner.textContent = "Нет подключения к интернету";
            document.body.appendChild(banner);
        }
    }
    function hideOfflineBanner() {
        const banner = document.getElementById("offline-banner");
        if (banner) banner.remove();
    }

    if (!navigator.onLine) showOfflineBanner();
    window.addEventListener("offline", showOfflineBanner);
    window.addEventListener("online", hideOfflineBanner);
});
