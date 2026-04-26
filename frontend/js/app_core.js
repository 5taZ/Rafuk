function createAppCore() {
    const state = {
        query: "",
        strictSearch: false,
        comparisonQuery: "",
        comparisonStats: null,
        comparisonItems: [],
        comparisonLoading: false,
        historyDays: 7,
        currency: "BYN",
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
        searchRequestId: 0,
        sort: "newest",
        discountFromPercent: 10,
        discountToPercent: 30,
        loading: false,
        error: null,
        stats: null,
        listings: [],
        _listingsLoadedAt: 0,
        _listingsLoadedSort: null,
        listingsTotal: 0,
        dealListings: [],
        _dealsLoadedAt: 0,
        dealsTotal: 0,
        segments: null,
        geography: [],
        chart: null,
        history: [],
        historyChart: null,
        leads: [],
        leadFilter: "all",
        // Monotonic request id — incremented on every loadLeads() so the
        // resolver can drop stale responses when the user rapidly toggles
        // tabs / fires mutations. Mirrors searchRequestId for listings.
        _leadsRequestId: 0,
        watchlist: [],
        watchlistFilter: "all",
        // Monotonic request id for loadWatchlist() — same purpose as
        // _leadsRequestId.
        _watchlistRequestId: 0,
        itemsFilter: "purchases",
        trackers: [],
        trackerEvents: [],
        trackerEventFilter: "all",
        trackerEventFilterTrackerId: null,
        trackerStatus: "",
        trackerStatusKind: "info",
        trackerMinDiscountPercent: 10,
        trackerMaxPriceByn: null,
        trackerSellerType: "",
        trackerCondition: "",
        trackerRegionName: "",
        trackerConfigKeyword: "",
        editingTrackerId: null,
        creatingTracker: false,
        modalCleanup: null,
        searchAbortController: null,
        opportunityBoard: { items: [], top_price_drops: [], rare_opportunities: [], market_signals: [] },
        dirtyViews: new Set(),
        _allDirty: true,
        detail: null,
        detailImageIndex: 0,
        detailFromWatchlist: false,
        detailAi: {
            adId: null,
            loading: false,
            result: null,
            error: "",
            source: "",
        },
        aiLoadingTimer: null,
        activeView: "overview",
        expenses: [],
        expensesLoading: false,
        currentExpenseLeadId: null,
        profitData: null,
        profitChart: null,
        pipelineStep: "active",
        recentSearches: [],
        panels: {
            distribution: false,
            history: true,
            comparison: false,
            segments: false,
            geography: false,
            historyDeals: false,
            listingAssistant: false,
        },
        listingAssistantResult: null,
    };

    const elements = {};

    function cacheElements() {
        cacheAppElements(elements);
    }

    function initTelegramTheme() {
        const saved = localStorage.getItem("theme");
        if (saved === "light" || saved === "dark") {
            document.documentElement.setAttribute("data-theme", saved);
        } else if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.expand();
            window.Telegram.WebApp.ready();
            const scheme = window.Telegram.WebApp.colorScheme;
            document.documentElement.setAttribute(
                "data-theme",
                scheme === "light" ? "light" : "dark"
            );
        } else {
            document.documentElement.setAttribute("data-theme", "dark");
        }
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute("data-theme");
        const next = current === "light" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", next);
        localStorage.setItem("theme", next);
    }

    function formatPrice(value) {
        if (value == null || value === 0) return "Договорная";
        const numeric = Number(value);
        if (Number.isNaN(numeric)) return "Договорная";

        if (numeric >= 10000) {
            const formatted = Number(numeric / 1000).toLocaleString("ru-RU", {
                maximumFractionDigits: 1,
                minimumFractionDigits: 0,
            });
            return `${formatted} тыс. р.`;
        }
        if (numeric >= 1000) {
            const formatted = Number(numeric / 1000).toLocaleString("ru-RU", {
                maximumFractionDigits: 2,
                minimumFractionDigits: 0,
            });
            return `${formatted} тыс. р.`;
        }
        if (numeric >= 100) {
            return `${Math.round(numeric)} р.`;
        }
        // Small amounts — show up to 2 decimals (e.g. 6.5 р., 0.99 р.)
        return `${numeric.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} р.`;
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
                state.recentSearches = JSON.parse(stored).slice(0, 10);
            }
        } catch (_) {
            state.recentSearches = [];
        }
    }

    function saveRecentSearches() {
        try {
            localStorage.setItem("recentSearches", JSON.stringify(state.recentSearches));
        } catch (_) {
            // ignore
        }
    }

    function addRecentSearch(query) {
        if (!query || query.trim().length < 2) return;
        const trimmed = query.trim();
        // Remove if already exists
        state.recentSearches = state.recentSearches.filter((q) => q !== trimmed);
        // Add to front
        state.recentSearches.unshift(trimmed);
        // Keep only last 10
        state.recentSearches = state.recentSearches.slice(0, 10);
        saveRecentSearches();
    }

    function clearRecentSearches() {
        state.recentSearches = [];
        saveRecentSearches();
    }

    // Focus trap for modals — prevents Tab from escaping modal boundaries
    function trapFocus(container) {
        const focusableSelectors = [
            'button:not([disabled])',
            'input:not([disabled])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            'a[href]',
            '[tabindex]:not([tabindex="-1"])',
        ].join(", ");
        const focusable = container.querySelectorAll(focusableSelectors);
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        function handleKeydown(e) {
            if (e.key !== "Tab") return;
            if (e.shiftKey) {
                if (document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                }
            } else {
                if (document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
            }
        }

        container.addEventListener("keydown", handleKeydown);
        // Focus the first focusable element
        first.focus();
        return () => container.removeEventListener("keydown", handleKeydown);
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
            if (elapsed > thresholdMs) {
                console.warn(`[perf] ${name} took ${elapsed.toFixed(1)}ms`);
            }
            return elapsed;
        };
    }

    /**
     * Mark one or more views as needing re-render.
     * Views: 'error','loading','currency','strict','tabs','panels','summary',
     * 'helper','views','trackingHero','cheapHero','monitoringHero','dealsHero',
     * 'sort','discount','eventFilters','dealInputs','trackerInputs','stats',
     * 'history','comparison','segments','geography','recent','listings','deals',
     * 'rates','trackerStatus','trackers','trackerEvents','leads','watchlist',
     * 'profit'.
     * Call without args or with 'all' to mark everything dirty.
     */
    function markDirty() {
        const args = Array.prototype.slice.call(arguments);
        if (args.length === 0 || args.includes('all')) {
            // Mark all known views dirty
            state._allDirty = true;
            state.dirtyViews.clear();
        } else {
            state._allDirty = false;
            for (const v of args) {
                state.dirtyViews.add(v);
            }
        }
    }

    function isDirty(view) {
        if (state._allDirty) return true;
        return state.dirtyViews.has(view);
    }

    function clearDirty() {
        state._allDirty = false;
        state.dirtyViews.clear();
    }

    function populateRegionSelect(selectEl, currentValue) {
        populateRegionSelectOptions(selectEl, currentValue);
    }

    return {
        state,
        elements,
        cacheElements,
        REGIONS: APP_REGIONS,
        initTelegramTheme,
        toggleTheme,
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
    };
}
