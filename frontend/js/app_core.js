const _loadPromises = new Map();

// RV-FE-02: shared plumbing for the lazy-chunk stubs (_lazy_*_stub.js).
// Each stub used to cache its first init promise forever, so one
// network blip, 5xx or SRI mismatch mid-deploy left the section dead
// (skeletons, no detail, no trackers) until the Mini App restarted.
const LAZY_SCRIPT_TIMEOUT_MS = 20_000;
const LAZY_RETRY_COOLDOWN_MS = 8_000;
const LAZY_LOAD_FAILED_MESSAGE = "Не удалось загрузить раздел. Проверьте соединение и попробуйте ещё раз.";

function _createLazyModule(context, { load, factoryKey, label }) {
    let real = null;
    let pending = null;
    let failedAt = 0;

    function ensure() {
        if (real) return Promise.resolve(real);
        if (pending) return pending;
        const attempt = (async () => {
            await load(context);
            const factory = (window.App || {})[factoryKey];
            if (typeof factory !== "function") {
                throw new Error(`${label} did not register factory`);
            }
            real = factory(context);
            failedAt = 0;
            return real;
        })();
        pending = attempt;
        // Registered before any caller's handler, so by the time they
        // see the rejection the next ensure() already starts afresh.
        attempt.catch(() => {
            if (pending === attempt) pending = null;
            failedAt = Date.now();
        });
        return attempt;
    }

    return {
        ensure,
        current: () => real,
        loading: () => Boolean(pending),
        // Render passes inside the cooldown paint the error state
        // instead of re-requesting the chunk on every repaint; the
        // "Повторить" button (or the first render after it) retries.
        coolingDown: () => !real && !pending && failedAt > 0
            && Date.now() - failedAt < LAZY_RETRY_COOLDOWN_MS,
        retry() {
            failedAt = 0;
            return ensure();
        },
    };
}

// Sync render entry points: return undefined until the chunk lands,
// then replay the call and repaint. ``onUnavailable(method, args)`` paints
// the error state while a recent failure is cooling down.
function _lazyRenderCall(lazy, context, label, method, args, onUnavailable) {
    const real = lazy.current();
    if (real) return real[method].apply(null, args);
    if (lazy.coolingDown()) {
        if (typeof onUnavailable === "function") onUnavailable(method, args);
        return undefined;
    }
    lazy.ensure()
        .then((loaded) => {
            loaded[method].apply(null, args);
            if (typeof context.scheduleRender === "function") context.scheduleRender();
        })
        .catch((err) => {
            (context.logClientError || (() => {}))(`${label} lazy-load failed:`, err);
            // Repaint so the skeletons give way to the error state.
            if (!lazy.current() && typeof context.scheduleRender === "function") {
                context.scheduleRender();
            }
        });
    return undefined;
}

// Async API entry points resolve to ``onFailure(method, err)`` instead of
// rejecting when the chunk cannot be fetched: callers fire them with
// ``void`` and would otherwise surface nothing but an unhandled rejection.
async function _lazyApiCall(lazy, context, label, method, args, onFailure) {
    let real = lazy.current();
    if (!real) {
        try {
            real = await lazy.ensure();
        } catch (err) {
            (context.logClientError || (() => {}))(`${label} lazy-load failed:`, err);
            return typeof onFailure === "function" ? onFailure(method, err) : undefined;
        }
    }
    return real[method].apply(null, args);
}

// Teardown entry points (close / destroy) need no chunk when nothing
// could have been opened yet; if an open is still loading, the close
// is replayed right after it so it cannot pop up afterwards.
function _lazyTeardownCall(lazy, method, args) {
    const real = lazy.current();
    if (real) return real[method].apply(null, args);
    if (lazy.loading()) {
        lazy.ensure().then((loaded) => loaded[method].apply(null, args), () => {});
    }
    return undefined;
}

function _showLazyLoadToast(context) {
    const toast = context.showToast || context._hooks?.showToast;
    if (typeof toast === "function") toast(LAZY_LOAD_FAILED_MESSAGE, "error", 3600);
}

// Paint (once) an error state with "Повторить" into ``container``.
// ``retry`` reloads the chunk; ``afterLoad`` re-fetches data whose loader
// lives in the same failed chunk.
function _renderLazyLoadError(context, container, retry, afterLoad) {
    if (!container || typeof container.replaceChildren !== "function") return;
    const current = container.firstElementChild;
    if (current && container.childElementCount === 1 && current.classList?.contains("lazy-load-error")) {
        return; // keep focus and avoid re-announcing on every repaint
    }
    const button = domEl("button", {
        className: "empty-state-action",
        type: "button",
        text: "Повторить",
    });
    const node = domEl(
        "div",
        { className: "empty-state empty-state--error lazy-load-error", attrs: { role: "status" } },
        domEl("p", { className: "empty-state-title", text: "Раздел не загрузился" }),
        domEl("p", { className: "empty-state-hint", text: LAZY_LOAD_FAILED_MESSAGE }),
        button,
    );
    button.addEventListener("click", () => {
        button.disabled = true;
        button.textContent = "Загружаю…";
        retry().then(
            () => {
                if (typeof afterLoad === "function") afterLoad();
                if (typeof context.scheduleRender === "function") context.scheduleRender();
            },
            () => {
                button.disabled = false;
                button.textContent = "Повторить";
                _showLazyLoadToast(context);
            },
        );
    });
    container.replaceChildren(node);
}

function createAppCore() {
    const state = {
        ui: {
            loading: false,
            error: null,
            activeView: "overview",
            dirtyViews: new Set(),
            _allDirty: true,
            // RV-SEC-05: set after account deletion; requestJson refuses
            // every further request.
            accountDeleted: false,
        },
        search: {
            // RV-FE-03: ``draft`` mirrors the search input on every
            // keystroke; ``activeQuery`` is the query of the results on
            // screen. Detail, paging, sort, filters, saving, trackers and
            // price history must read activeQuery — a half-typed draft
            // used to leak into all of them.
            draft: "",
            activeQuery: "",
            strictSearch: true,
            searchRequestId: 0,
            sort: "newest",
            recentSearches: [],
            searchAbortController: null,
        },
        filters: {
            category: null, // selected category id (int or null)
            categories: [], // category distribution from last search [{id, label, count}]
            condition: "", // filter by condition: "", "new", "used"
            sellerType: "", // filter by seller: "", "private", "shop"
            minPrice: null, // filter by min price (number or null)
            maxPrice: null, // filter by max price (number or null)
            regionName: "", // filter by region name
            // Pending filter values (before Apply is clicked)
            pendingCategory: null,
            pendingCondition: "",
            pendingSellerType: "",
            pendingMinPrice: null,
            pendingMaxPrice: null,
            pendingRegionName: "",
            filterDropdownOpen: false,
            discountFromPercent: 10,
            discountToPercent: 30,
        },
        listings: {
            items: [],
            _loadedAt: 0,
            _loadedSort: null,
            total: 0,
            hasMore: false,
            loading: false,
            loadingMore: false,
            _requestId: 0,
            _pending: false,
            fallbackUsed: false,
            resultCap: 0,
            datasetCount: 0,
            servedCap: 0,
            isLimited: false,
        },
        trackers: {
            items: [],
            events: [],
            eventFilter: "all",
            eventFilterTrackerId: null,
            status: "",
            statusKind: "info",
            editingId: null,
            creating: false,
            minDiscountPercent: 10,
            maxPriceByn: null,
            sellerType: "",
            condition: "",
            regionName: "",
            configKeyword: "",
        },
        leads: {
            items: [],
            filter: "all",
            _requestId: 0,
            _loading: false,
            // RV-FE-05: message of the last failed load; the list keeps
            // its previous items and renders an error state instead.
            error: null,
            pipelineStep: "active",
            itemsFilter: "purchases",
        },
        watchlist: {
            items: [],
            filter: "all",
            _requestId: 0,
            _loading: false,
            error: null,
        },
        detail: {
            data: null,
            imageIndex: 0,
            fromWatchlist: false,
            _requestId: 0,
            ai: {
                adId: null,
                loading: false,
                result: null,
                error: "",
                source: "",
            },
            aiLoadingTimer: null,
        },
        expenses: {
            items: [],
            loading: false,
            currentLeadId: null,
        },
        analytics: {
            dashboard: null,
            loading: false,
        },
        panels: {
            distribution: false,
            history: true,
            segments: false,
            geography: false,
            historyDeals: false,
            listingAssistant: false,
        },
        profile: {
            data: null,
            loading: false,
            error: "",
        },
        admin: {
            users: [],
            statuses: [],
            query: "",
            selectedStatus: "",
            loading: false,
            statusesLoading: false,
            error: "",
            statusesError: "",
            editingUserId: null,
            savingUserIds: new Set(),
            savingStatusCodes: new Set(),
            auditEntries: [],
            auditLoading: false,
            auditError: "",
            auditOffset: 0,
            auditHasMore: false,
        },
        charts: {
            distribution: null,
            history: null,
            historyData: [],
            _historyRequestId: 0,
        },
        misc: {
            historyDays: 7,
            currency: "BYN",
            segments: null,
            geography: [],
            listingAssistantResult: null,
            modalCleanup: null,
            stats: null,

        },
    };

    const elements = {};

    function cacheElements() {
        cacheAppElements(elements);
    }

    const THEME_CHROME_COLORS = {
        dark: "#0a0a0b",
        light: "#ffffff",
    };

    function normalizeTheme(value) {
        return value === "light" ? "light" : "dark";
    }

    function setThemeColorMeta(color) {
        let meta = document.querySelector('meta[name="theme-color"]');
        if (!meta) {
            meta = document.createElement("meta");
            meta.name = "theme-color";
            document.head.appendChild(meta);
        }
        meta.setAttribute("content", color);
    }

    function compareTelegramVersions(version, minVersion) {
        const left = String(version || "").trim().split(".");
        const right = String(minVersion || "").trim().split(".");
        const length = Math.max(left.length, right.length);
        for (let i = 0; i < length; i++) {
            const a = parseInt(left[i] || "0", 10);
            const b = parseInt(right[i] || "0", 10);
            if (a > b) return 1;
            if (a < b) return -1;
        }
        return 0;
    }

    function telegramVersionAtLeast(tg, minVersion) {
        try {
            if (typeof tg?.isVersionAtLeast === "function") {
                return tg.isVersionAtLeast(minVersion);
            }
        } catch (_) {}
        return compareTelegramVersions(tg?.version, minVersion) >= 0;
    }

    function callTelegramChromeMethod(tg, method, color) {
        try {
            if (typeof tg?.[method] === "function") tg[method](color);
        } catch (_) {}
    }

    function syncTelegramChromeTheme(theme) {
        const next = normalizeTheme(theme || document.documentElement.getAttribute("data-theme"));
        const bg = THEME_CHROME_COLORS[next];
        document.body.style.backgroundColor = bg;
        setThemeColorMeta(bg);
        const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
        if (!tg) return;
        // Telegram paints native top/bottom chrome outside our DOM; keep it on our palette.
        if (telegramVersionAtLeast(tg, "6.1")) {
            callTelegramChromeMethod(tg, "setBackgroundColor", bg);
            callTelegramChromeMethod(tg, "setHeaderColor", bg);
        }
        if (telegramVersionAtLeast(tg, "7.10")) {
            callTelegramChromeMethod(tg, "setBottomBarColor", bg);
        }
    }

    // FE-NEW-3: single themeChanged listener with stored ref so re-init / pagehide can deregister.
    let _themeChangedRef = null;
    let _themeChangedRegistered = false;
    // FE-NEW-5: callbacks invoked after any theme change (toggle or Telegram event).
    const _themeChangeCallbacks = [];

    const _TG_THEME_COLOR_RE = /^#[0-9a-fA-F]{3,8}$/;
    function _applyTelegramThemeVars() {
        const tp = window.Telegram?.WebApp?.themeParams || {};
        const root = document.documentElement;
        const map = [
            ["bg_color", "--tg-theme-bg-color"],
            ["text_color", "--tg-theme-text-color"],
            ["hint_color", "--tg-theme-hint-color"],
            ["link_color", "--tg-theme-link-color"],
            ["button_color", "--tg-theme-button-color"],
            ["button_text_color", "--tg-theme-button-color-text"],
            ["secondary_bg_color", "--tg-theme-secondary-bg-color"],
            ["destructive_text_color", "--tg-theme-destructive-text-color"],
        ];
        for (const [tgKey, cssVar] of map) {
            const value = tp[tgKey];
            if (typeof value === "string" && value.length > 0 && value.length <= 16 && _TG_THEME_COLOR_RE.test(value.trim())) {
                root.style.setProperty(cssVar, value.trim());
            }
        }
    }

    /**
     * Pick a starting theme. We mirror Telegram's coarse dark/light
     * preference (so a user who has Telegram in light mode opens the
     * Mini App in light mode by default), but we DO NOT inherit
     * Telegram's individual theme colours — the Mini App keeps its
     * own palette so the brand stays consistent across clients.
     *
     * Manual `localStorage.theme` always wins over the heuristic so a
     * user who explicitly toggled the theme keeps their choice.
     */
    function initTelegramTheme() {
        const saved = localStorage.getItem("theme");
        const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
        const initialTheme = saved === "light" || saved === "dark"
            ? saved
            : (tg?.colorScheme === "light" ? "light" : "dark");
        document.documentElement.setAttribute("data-theme", initialTheme);
        if (tg) {
            try {
                tg.expand();
                tg.ready();
            } catch (_) {
                // ready/expand can throw outside a real Telegram client
            }
            syncTelegramChromeTheme(initialTheme);
            // FE-NEW-3: single themeChanged listener with stored ref so re-init / pagehide can deregister.
            if (!_themeChangedRegistered) {
                _themeChangedRef = () => {
                    if (localStorage.getItem("theme")) {
                        syncTelegramChromeTheme();
                        return;
                    }
                    const s = tg.colorScheme;
                    const next = s === "light" ? "light" : "dark";
                    document.documentElement.setAttribute("data-theme", next);
                    syncTelegramChromeTheme(next);
                    // Also sync Telegram CSS custom properties (app.js palette).
                    _applyTelegramThemeVars();
                    // FE-NEW-5: re-paint charts with fresh CSS tokens.
                    for (const cb of _themeChangeCallbacks) cb();
                };
                try {
                    tg.onEvent?.("themeChanged", _themeChangedRef);
                    _themeChangedRegistered = true;
                } catch (_) {
                    // onEvent missing on older WebApp builds — non-fatal
                }
            }
            return;
        }
        syncTelegramChromeTheme(initialTheme);
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute("data-theme");
        const next = current === "light" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", next);
        localStorage.setItem("theme", next);
        syncTelegramChromeTheme(next);
        for (const cb of _themeChangeCallbacks) cb();
    }

    function formatPrice(value, priceType) {
        if (priceType === "negotiable" || (value == null && priceType !== "free")) return "Договорная";
        if (priceType === "free" || value === 0) return "Бесплатно";
        const numeric = Number(value);
        if (Number.isNaN(numeric)) return "Договорная";
        return `${Math.round(numeric)} BYN`;
    }

    function formatCondition(condition) {
        const map = {
            "Новый": "Новый",
            "Б/у": "Б/у",
            new: "Новый",
            used: "Б/у",
            "1": "Б/у",
            "2": "Новый",
        };
        return map[condition] || "";
    }

    function formatSeller(seller) {
        const map = {
            "Частное лицо": "Частное",
            "Магазин": "Магазин",
            private: "Частное",
            shop: "Магазин",
        };
        return map[seller] || "";
    }

    function formatDelta(delta) {
        if (delta == null || Math.abs(delta) < 0.5) return "";
        const sign = delta > 0 ? "+" : "";
        return `${sign}${Math.round(delta)}%`;
    }

    function deltaClass(delta) {
        if (!delta || Math.abs(delta) < 0.5) return "";
        return delta > 0 ? "over" : "under";
    }

    const _dateFormatter = new Intl.DateTimeFormat("ru-BY", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
    });

    function formatDate(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return _dateFormatter.format(date);
    }

    function hasTelegramInitData() {
        return Boolean(
            window.Telegram &&
            window.Telegram.WebApp &&
            window.Telegram.WebApp.initData
        );
    }

    function loadRecentSearches() {
        try {
            const stored = localStorage.getItem("recentSearches");
            if (stored) {
                state.search.recentSearches = JSON.parse(stored).slice(0, 10);
            }
        } catch (_) {
            state.search.recentSearches = [];
        }
    }

    function saveRecentSearches() {
        try {
            localStorage.setItem("recentSearches", JSON.stringify(state.search.recentSearches));
        } catch (e) {
            if (e.name === "QuotaExceededError" && state.search.recentSearches.length > 1) {
                state.search.recentSearches = state.search.recentSearches.slice(0, Math.ceil(state.search.recentSearches.length / 2));
                try {
                    localStorage.setItem("recentSearches", JSON.stringify(state.search.recentSearches));
                } catch (_) {
                    // Give up after retry
                }
            }
        }
    }

    function addRecentSearch(query) {
        if (!query || query.trim().length < 2) return;
        const trimmed = query.trim();
        // Remove if already exists
        state.search.recentSearches = state.search.recentSearches.filter((q) => q !== trimmed);
        // Add to front
        state.search.recentSearches.unshift(trimmed);
        // Keep only last 10
        state.search.recentSearches = state.search.recentSearches.slice(0, 10);
        saveRecentSearches();
    }

    function clearRecentSearches() {
        state.search.recentSearches = [];
        saveRecentSearches();
    }

    /**
     * Performance monitoring utility — measures render time in development.
     * Returns elapsed ms when the cleanup function is called.
     *
     * @param {string} name - Human-readable operation name
     * @param {number} [thresholdMs=100] - Warn threshold for console.warn
     * @returns {Function} Cleanup function that returns elapsed ms
     */
    function measureRender(name, thresholdMs = 100) {
        const start = performance.now();
        return function () {
            const elapsed = performance.now() - start;
            // Slow-render diagnostic is available via the returned elapsed
            // value; console.warn removed to avoid noise on weak devices.
            return elapsed;
        };
    }

    /**
     * Mark one or more views as needing re-render.
     * Views: 'error','loading','currency','strict','tabs','panels','summary',
     * 'helper','views','trackingHero','dealsHero',
     * 'sort','discount','eventFilters','dealInputs','trackerInputs','stats',
     * 'history','segments','geography','recent','listings','deals',
     * 'rates','trackerStatus','trackers','trackerEvents','leads','watchlist',
     * 'profit','profile','adminUsers','adminStatuses','adminAudit'.
     * Call without args or with 'all' to mark everything dirty.
     */
    function markDirty() {
        const args = Array.prototype.slice.call(arguments);
        if (args.length === 0 || args.includes('all')) {
            // Mark all known views dirty
            state.ui._allDirty = true;
            state.ui.dirtyViews.clear();
        } else {
            state.ui._allDirty = false;
            for (const v of args) {
                state.ui.dirtyViews.add(v);
            }
        }
    }

    function isDirty(view) {
        if (state.ui._allDirty) return true;
        return state.ui.dirtyViews.has(view);
    }

    function clearDirty() {
        state.ui._allDirty = false;
        state.ui.dirtyViews.clear();
    }

    function populateRegionSelect(selectEl, currentValue) {
        populateRegionSelectOptions(selectEl, currentValue);
    }

    const _loadedScripts = new Set();
    const _pendingScripts = new Map();
    // FE-NEW-1: SRI on lazy-loaded modules. Map is generated by
    // scripts/build_frontend_bundle.sh and embedded in the bundle.
    function _loadScript(src, integrity) {
        if (_loadedScripts.has(src)) return Promise.resolve();
        // RV-FE-02: concurrent callers share one <script> instead of
        // racing duplicate tags.
        if (_pendingScripts.has(src)) return _pendingScripts.get(src);
        if (!integrity && typeof __LAZY_INTEGRITY !== "undefined") {
            const basename = src.split("/").pop().split("?")[0];
            integrity = __LAZY_INTEGRITY[basename];
        }
        const promise = new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = src;
            if (integrity) {
                s.integrity = integrity;
                s.crossOrigin = "anonymous";
            }
            let timer = null;
            const settle = (err) => {
                clearTimeout(timer);
                s.onload = null;
                s.onerror = null;
                _pendingScripts.delete(src);
                if (!err) {
                    _loadedScripts.add(src);
                    resolve();
                    return;
                }
                // RV-FE-02: a stalled or failed tag is removed so the
                // retry inserts a fresh one instead of waiting on it.
                s.remove();
                reject(err);
            };
            s.onload = () => settle(null);
            s.onerror = () => settle(new Error(`Failed to load ${src}`));
            timer = setTimeout(() => settle(new Error(`Timed out loading ${src}`)), LAZY_SCRIPT_TIMEOUT_MS);
            document.head.appendChild(s);
        });
        _pendingScripts.set(src, promise);
        return promise;
    }

    return {
        state,
        elements,
        cacheElements,
        REGIONS: APP_REGIONS,
        initTelegramTheme,
        toggleTheme,
        onThemeChange: (cb) => _themeChangeCallbacks.push(cb),
        syncTelegramChromeTheme,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        loadRecentSearches,
        saveRecentSearches,
        addRecentSearch,
        clearRecentSearches,
        measureRender,
        markDirty,
        isDirty,
        clearDirty,
        populateRegionSelect,
        // M10: shared filter helper — single source of truth for lazy modules
        hasActiveListingFilters: function () {
            return (
                state.filters.category != null
                || Boolean(state.filters.condition)
                || Boolean(state.filters.sellerType)
                || state.filters.minPrice != null
                || state.filters.maxPrice != null
                || Boolean(state.filters.regionName)
            );
        },
        logClientError,
        _loadScript,
    };
}
