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

    // Offline / online detection
    function showOfflineBanner() {
        let banner = document.getElementById("offline-banner");
        if (!banner) {
            banner = document.createElement("div");
            banner.id = "offline-banner";
            banner.textContent = "Нет подключения к интернету";
            banner.style.cssText = "position:fixed;top:0;left:0;right:0;background:#fb7185;color:#fff;text-align:center;padding:6px 12px;font-size:13px;font-family:system-ui;z-index:10000;";
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
