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
        const view = core.state.ui.activeView || "overview";
        const query = (core.state.search.query || "").trim();
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

        const privacyBtn = document.getElementById("privacy-btn");
        if (privacyBtn) privacyBtn.addEventListener("click", () => actions.openPrivacyModal?.());

        core.state.search.query = core.elements.searchInput.value.trim();
        renderers.renderAll();
        void actions.loadTrackers();
        void actions.loadLeads();
        void actions.loadWatchlist();
        void actions.applyLaunchParams();

        // Check PD processing consent on first launch — non-blocking,
        // just shows the consent modal if user hasn't consented yet.
        if (typeof actions.checkAiConsent === "function") {
            // checkAiConsent checks /account/consent/ai_analysis which
            // implies PD processing consent was also granted (both are
            // required together). If missing, the consent modal appears.
            void actions.checkAiConsent().catch((err) => { console.warn("AI consent check failed", err); });
        }

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

    function renderChart(...args) {
        return renderers.renderChart(...args);
    }

    return {
        init,
        search,
        loadListings,
        renderChart,
        renderBoxPlot: renderChart,
        formatPrice: core.formatPrice,
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const app = analyticsApp();
    app.init();

    if (window.Telegram?.WebApp) {
        Telegram.WebApp.ready();
        Telegram.WebApp.expand();
    }

    function _applyTelegramTheme() {
        const tp = window.Telegram?.WebApp?.themeParams || {};
        const root = document.documentElement;
        if (tp.bg_color) root.style.setProperty('--tg-theme-bg-color', tp.bg_color);
        if (tp.text_color) root.style.setProperty('--tg-theme-text-color', tp.text_color);
        if (tp.hint_color) root.style.setProperty('--tg-theme-hint-color', tp.hint_color);
        if (tp.link_color) root.style.setProperty('--tg-theme-link-color', tp.link_color);
        if (tp.button_color) root.style.setProperty('--tg-theme-button-color', tp.button_color);
        if (tp.button_text_color) root.style.setProperty('--tg-theme-button-text-color', tp.button_text_color);
        if (tp.secondary_bg_color) root.style.setProperty('--tg-theme-secondary-bg-color', tp.secondary_bg_color);
        if (tp.destructive_text_color) root.style.setProperty('--tg-theme-destructive-text-color', tp.destructive_text_color);
    }
    _applyTelegramTheme();
    window.Telegram?.WebApp?.onEvent?.('themeChanged', _applyTelegramTheme);

    // Offline / online detection
    const _offlineBadge = document.getElementById("offline-badge");
    if (_offlineBadge) {
        window.addEventListener("online", () => _offlineBadge.hidden = true);
        window.addEventListener("offline", () => _offlineBadge.hidden = false);
        if (!navigator.onLine) _offlineBadge.hidden = false;
    }

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
