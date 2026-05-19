function createAppCore() {
    const state = {
        ui: {
            loading: false,
            error: null,
            activeView: "overview",
            dirtyViews: new Set(),
            _allDirty: true,
        },
        search: {
            query: "",
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
            pipelineStep: "active",
            itemsFilter: "purchases",
        },
        watchlist: {
            items: [],
            filter: "all",
            _requestId: 0,
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
            // React to the user toggling dark/light in the Telegram
            // client without a reload — but only swap our binary mode,
            // never override individual palette variables.
            try {
                tg.onEvent?.("themeChanged", () => {
                    if (localStorage.getItem("theme")) {
                        syncTelegramChromeTheme();
                        return;
                    }
                    const s = tg.colorScheme;
                    const next = s === "light" ? "light" : "dark";
                    document.documentElement.setAttribute("data-theme", next);
                    syncTelegramChromeTheme(next);
                });
            } catch (_) {
                // onEvent missing on older WebApp builds — non-fatal
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
    // FE-NEW-1: SRI on lazy-loaded modules. Map is generated by
    // scripts/build_frontend_bundle.sh and embedded in the bundle.
    function _loadScript(src, integrity) {
        if (_loadedScripts.has(src)) return Promise.resolve();
        if (!integrity && typeof __LAZY_INTEGRITY !== "undefined") {
            const basename = src.split("/").pop().split("?")[0];
            integrity = __LAZY_INTEGRITY[basename];
        }
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = src;
            if (integrity) {
                s.integrity = integrity;
                s.crossOrigin = "anonymous";
            }
            s.onload = () => { _loadedScripts.add(src); resolve(); };
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    return {
        state,
        elements,
        cacheElements,
        REGIONS: APP_REGIONS,
        initTelegramTheme,
        toggleTheme,
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
        logClientError,
        _loadScript,
    };
}
