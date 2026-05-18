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
        const query = (core.state.search.query || "").trim();
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
                    { forceRefresh: true },
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

        const privacyBtn = document.getElementById("privacy-btn");
        if (privacyBtn) privacyBtn.addEventListener("click", () => actions.openPrivacyModal?.());

        core.state.search.query = core.elements.searchInput.value.trim();
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
        renderBoxPlot: renderChart,
        formatPrice: core.formatPrice,
        syncTelegramChromeTheme: core.syncTelegramChromeTheme,
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const app = analyticsApp();
    app.init();

    if (window.Telegram?.WebApp) {
        Telegram.WebApp.ready();
        Telegram.WebApp.expand();
        app.syncTelegramChromeTheme?.();
    }

    // PR-15: Telegram themeParams come from the WebView host. The
    // values are normally trusted (Telegram controls them) but
    // ``setProperty`` accepts anything string-shaped — including
    // ``"red; --custom: …"`` style payloads that would inject extra
    // CSS variables. Defence-in-depth: only let strict CSS color
    // literals through. Unrecognised values are dropped, leaving
    // the cascade to use the value from frontend/css.
    const _TG_THEME_COLOR_RE = /^#[0-9a-fA-F]{3,8}$/;
    function _isSafeTelegramThemeColor(value) {
        return typeof value === "string"
            && value.length > 0
            && value.length <= 16
            && _TG_THEME_COLOR_RE.test(value.trim());
    }

    function _applyTelegramTheme() {
        const tp = window.Telegram?.WebApp?.themeParams || {};
        const root = document.documentElement;
        const map = [
            ["bg_color", "--tg-theme-bg-color"],
            ["text_color", "--tg-theme-text-color"],
            ["hint_color", "--tg-theme-hint-color"],
            ["link_color", "--tg-theme-link-color"],
            ["button_color", "--tg-theme-button-color"],
            ["button_text_color", "--tg-theme-button-text-color"],
            ["secondary_bg_color", "--tg-theme-secondary-bg-color"],
            ["destructive_text_color", "--tg-theme-destructive-text-color"],
        ];
        for (const [tgKey, cssVar] of map) {
            const value = tp[tgKey];
            if (_isSafeTelegramThemeColor(value)) {
                root.style.setProperty(cssVar, value.trim());
            }
        }
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
                    core.logClientError("Service worker registration failed", err, "warn");
                });
        });
    }
});
