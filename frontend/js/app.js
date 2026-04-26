function analyticsApp() {
    const core = createAppCore();
    const actions = {};
    const renderers = createAppRenderers({ ...core, actions });
    Object.assign(actions, createAppActions({ ...core, ...renderers }));

    /**
     * Pick the right "refresh this view" function based on the active
     * tab. Returns null for views where pull-to-refresh is meaningless
     * (e.g. an empty search overview), so the gesture is a no-op
     * instead of bouncing without doing anything useful.
     */
    function getRefreshForActiveView() {
        const view = core.state.activeView || "overview";
        const query = (core.state.query || "").trim();
        if (view === "deals") {
            return () => Promise.all([
                actions.loadLeads ? actions.loadLeads() : null,
                actions.loadWatchlist ? actions.loadWatchlist() : null,
            ]);
        }
        if (view === "tracking") {
            return () => actions.loadTrackers && actions.loadTrackers();
        }
        if (view === "overview" || view === "ads") {
            // Repeating the current search is the "refresh" everywhere
            // that depends on Kufar — it re-fetches stats, listings,
            // history together. Pre-merger this had a separate "cheap"
            // branch, but the cheap view is gone — sort=cheap inside
            // the ads view re-uses the same loadListings refresh.
            return query
                ? () => actions.search && actions.search(view === "ads" ? "ads" : "overview")
                : null;
        }
        return null;
    }

    function init() {
        core.cacheElements();
        core.populateRegionSelect(core.elements.trackerRegionSelect);
        core.populateRegionSelect(core.elements.editRegionSelect);
        core.populateRegionSelect(core.elements.filterRegion);
        core.initTelegramTheme();
        core.loadRecentSearches();
        actions.bindEvents();

        const themeBtn = document.getElementById("theme-toggle");
        if (themeBtn) themeBtn.addEventListener("click", () => core.toggleTheme());

        core.state.query = core.elements.searchInput.value.trim();
        renderers.renderAll();
        void actions.loadTrackers();
        void actions.loadLeads();
        void actions.loadWatchlist();
        void actions.applyLaunchParams();

        // Pull-to-refresh — page-scoped, picks the right loader by view.
        // Skipped under prefers-reduced-motion (the helper short-circuits).
        if (typeof setupPullToRefresh === "function") {
            setupPullToRefresh({
                getRefreshHandler: getRefreshForActiveView,
                indicatorEl: document.getElementById("ptr-indicator"),
            });
        }
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

    // Register the service worker so the shell + read-only API
    // responses survive flaky networks. Skipped on insecure origins
    // (browsers reject SW registration over plain http) so local
    // `python -m http.server` style dev still works without spam in
    // the console. Telegram Mini Apps are always served over HTTPS,
    // so production will always register.
    if (
        "serviceWorker" in navigator &&
        (location.protocol === "https:" || location.hostname === "localhost")
    ) {
        // Defer to after first paint so registration competes with
        // nothing visible — saves ~30 ms on the perceived TTI.
        window.addEventListener("load", () => {
            navigator.serviceWorker
                .register("/sw.js", { scope: "/" })
                .catch((err) => {
                    console.warn("Service worker registration failed", err);
                });
        });
    }
});
