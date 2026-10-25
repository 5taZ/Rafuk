function analyticsApp() {
    const core = createAppCore();
    const actionRegistry = {};
    const renderers = createAppRenderers({ ...core, actions: actionRegistry });
    const actions = createAppActions({ ...core, ...renderers });
    Object.assign(actionRegistry, actions);

    // OPUS-13 wave 73: eagerly preload the cards lazy chunk right
    // after composition. Cards drive overview / ads / deals /
    // tracking, so deferring them until the user's first action
    // would cause a stub-returns-undefined flash on every screen.
    // Firing ``_ensureLoaded`` here lets the network request race
    // with the bundle's init path — warm caches resolve in one
    // tick, cold cache sees a single ``scheduleRender`` re-paint
    // a few frames later.
    if (renderers._cardsEnsureLoaded) {
        renderers._cardsEnsureLoaded().catch((err) => {
            core.logClientError("cards preload failed:", err);
        });
    }

    // FE-H8: module-scoped handle for the pull-to-refresh uninstall
    // function returned by setupPullToRefresh(). Declared up-front so
    // init() can both re-arm it (idempotency) and pagehide can tear
    // it down without races.
    let _ptrUninstall = null;
    let _telegramBackButtonCleanup = null;

    /**
     * Pick the right "refresh this view" function based on the active
     * tab. Returns null for views where pull-to-refresh is meaningless
     * (e.g. an empty search overview), so the gesture is a no-op
     * instead of bouncing without doing anything useful.
     */
    function getRefreshForActiveView() {
        const view = core.state.ui.activeView || "overview";
        // RV-FE-03: refresh the results on screen, not the draft typed
        // into the search box since they were loaded.
        const query = (core.state.search.activeQuery || "").trim();
        if (view === "deals") {
            return () => Promise.all([
                actions.loadLeads ? actions.loadLeads() : null,
                actions.loadWatchlist ? actions.loadWatchlist() : null,
            ]);
        }
        if (view === "profile") {
            return () => actions.loadProfile && actions.loadProfile({ retry: false });
        }
        if (view === "admin") {
            return () => Promise.all([
                actions.loadAdminUsers ? actions.loadAdminUsers({ retry: false }) : null,
                actions.loadAdminStatuses ? actions.loadAdminStatuses({ retry: false }) : null,
                actions.loadAdminAudit ? actions.loadAdminAudit({ retry: false }) : null,
                actions.loadProfile ? actions.loadProfile({ silent: true, retry: false }) : null,
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
                ? () => actions.search && actions.search(
                    view === "ads" ? "ads" : "overview",
                    { forceRefresh: true, query },
                )
                : null;
        }
        return null;
    }

    function setupTelegramBackButton() {
        const tg = window.Telegram?.WebApp;
        const backButton = tg?.BackButton;
        if (!backButton || typeof backButton.onClick !== "function") return () => {};

        const isOpen = (el) => Boolean(el && !el.hidden);
        const click = (selector) => {
            const el = document.querySelector(selector);
            if (el && typeof el.click === "function") el.click();
        };
        const topmostClose = () => {
            // TG-07: the long-press menu sits above every sheet; without
            // it here Android's back closed the whole Mini App.
            if (isOpen(document.querySelector(".lp-menu-overlay"))) return () => hideLongPressMenu();
            if (document.querySelector(".typed-confirm-modal")) {
                return () => click(".typed-confirm-modal [data-role='cancel']");
            }
            if (isOpen(document.getElementById("privacy-modal"))) return () => click("#privacy-modal-close");
            if (isOpen(document.getElementById("consent-modal"))) return () => click("#consent-cancel-btn");
            if (isOpen(document.getElementById("la-result-overlay"))) return () => click("#la-result-back");
            if (isOpen(document.getElementById("la-modal"))) return () => click("#la-modal-close");
            if (isOpen(core.elements.aiModal)) return () => actions.closeAIModal?.();
            if (isOpen(core.elements.expensesModal)) return () => renderers.closeExpensesModal?.();
            if (isOpen(core.elements.detailModal)) return () => renderers.closeDetailModal?.();
            if (isOpen(core.elements.editTrackerModal)) return () => actions.closeEditTracker?.();
            // TG-07: Profile / Admin have no tab of their own, so Back
            // walks up to where they are reached from.
            const view = core.state.ui.activeView;
            if (view === "admin") return () => actions.openProfile?.();
            if (view === "profile") return () => actions.openOverview?.();
            return null;
        };
        const sync = () => {
            try {
                if (topmostClose()) backButton.show();
                else backButton.hide();
            } catch (_) {}
        };
        const onBack = () => {
            const close = topmostClose();
            if (!close) {
                sync();
                return;
            }
            close();
            setTimeout(sync, 0);
            setTimeout(sync, 420);
        };

        backButton.onClick(onBack);
        const observer = new MutationObserver(sync);
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["hidden", "class"],
        });
        sync();

        return () => {
            observer.disconnect();
            try { backButton.offClick?.(onBack); } catch (_) {}
            try { backButton.hide(); } catch (_) {}
        };
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

        // FE-NEW-5: re-render charts with fresh CSS tokens on theme change.
        core.onThemeChange(() => {
            renderers.renderChart();
            renderers.renderHistoryChart();
        });

        const privacyBtn = document.getElementById("privacy-btn");
        if (privacyBtn) privacyBtn.addEventListener("click", () => actions.openPrivacyModal?.());

        core.state.search.draft = core.elements.searchInput.value.trim();
        renderers.renderAll();
        void actions.loadProfile({ silent: true });
        void actions.loadTrackers();
        void actions.loadLeads();
        void actions.loadWatchlist();
        void actions.applyLaunchParams();

        // Pull-to-refresh — page-scoped, picks the right loader by view.
        // Skipped under prefers-reduced-motion (the helper short-circuits).
        //
        // FE-H8: setupPullToRefresh attaches four document-level touch
        // listeners. The returned uninstall() detaches them. When the
        // mini-app tab is closed (pagehide) or the bfcache restore
        // happens, we run uninstall so the listeners aren't duplicated
        // on next init() — this previously grew unbounded when init
        // was triggered multiple times in dev (HMR, Telegram WebView
        // re-mounts).
        if (typeof setupPullToRefresh === "function") {
            if (typeof _ptrUninstall === "function") {
                try { _ptrUninstall(); } catch (_) { /* already gone */ }
                _ptrUninstall = null;
            }
            _ptrUninstall = setupPullToRefresh({
                getRefreshHandler: getRefreshForActiveView,
                indicatorEl: document.getElementById("ptr-indicator"),
            });
        }
        if (typeof _telegramBackButtonCleanup === "function") {
            try { _telegramBackButtonCleanup(); } catch (_) { /* already gone */ }
            _telegramBackButtonCleanup = null;
        }
        _telegramBackButtonCleanup = setupTelegramBackButton();
    }

    // Detach listeners when the page is unloaded or put into bfcache;
    // the browser normally GCs them, but iOS Safari keeps touch
    // listeners alive across bfcache restores which leads to double
    // firings when we come back.
    window.addEventListener("pagehide", () => {
        if (typeof _ptrUninstall === "function") {
            try { _ptrUninstall(); } catch (_) { /* noop */ }
            _ptrUninstall = null;
        }
        if (typeof _telegramBackButtonCleanup === "function") {
            try { _telegramBackButtonCleanup(); } catch (_) { /* noop */ }
            _telegramBackButtonCleanup = null;
        }
    });

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
        formatPrice: core.formatPrice,
        syncTelegramChromeTheme: core.syncTelegramChromeTheme,
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const app = analyticsApp();
    try {
        app.init();
    } finally {
        // TG-15: one ready() (it used to run twice), after init() painted
        // the first screen — it drops Telegram's placeholder. ``finally``
        // so a failed init can't leave the user on the loader for good.
        // expand() runs in initTelegramTheme().
        try { window.Telegram?.WebApp?.ready?.(); } catch (_) {}
    }

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
                    // FE-20: ``core`` lives inside analyticsApp(); the
                    // bundle-scope logger is the one in reach here.
                    logClientError("Service worker registration failed", err, "warn");
                });
        });
    }
});
