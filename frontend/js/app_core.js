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
            strictSearch: false,
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
        },
        deals: {
            items: [],
            _loadedAt: 0,
            total: 0,
            hasMore: false,
            loading: false,
            loadingMore: false,
            _requestId: 0,
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
            periodDays: 90,
            dashboard: null,
            loading: false,
            profitData: null,
            profitChart: null,
        },
        panels: {
            distribution: false,
            history: true,
            segments: false,
            geography: false,
            historyDeals: false,
            listingAssistant: false,
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
        if (saved === "light" || saved === "dark") {
            document.documentElement.setAttribute("data-theme", saved);
        }
        if (tg) {
            try {
                tg.expand();
                tg.ready();
            } catch (_) {
                // ready/expand can throw outside a real Telegram client
            }
            // Override Telegram's injected background color with our own.
            // Telegram WebApp JS sets body.style.backgroundColor to the
            // user's theme bg_color (often blue), which breaks our dark
            // palette. We force our own bg after Telegram has initialised.
            const isDark = (saved || (tg.colorScheme !== "light")) === "dark";
            const ourBg = isDark ? "#0a0a0b" : "#ffffff";
            // setBackgroundColor requires Telegram WebApp version >= 6.1
            if (tg.version && parseFloat(tg.version) >= 6.1) {
                try { tg.setBackgroundColor(ourBg); } catch (_) {}
            }
            document.body.style.backgroundColor = ourBg;

            if (!saved) {
                const scheme = tg.colorScheme;
                document.documentElement.setAttribute(
                    "data-theme",
                    scheme === "light" ? "light" : "dark"
                );
            }
            // React to the user toggling dark/light in the Telegram
            // client without a reload — but only swap our binary mode,
            // never override individual palette variables.
            try {
                tg.onEvent?.("themeChanged", () => {
                    if (localStorage.getItem("theme")) return;
                    const s = tg.colorScheme;
                    document.documentElement.setAttribute(
                        "data-theme",
                        s === "light" ? "light" : "dark"
                    );
                    // Re-assert our background colour after Telegram
                    // re-injects its theme params on themeChanged.
                    const bg = s === "light" ? "#ffffff" : "#0a0a0b";
                    try {
                        if (tg.version && parseFloat(tg.version) >= 6.1) {
                            tg.setBackgroundColor(bg);
                        }
                    } catch (_) {}
                    document.body.style.backgroundColor = bg;
                });
            } catch (_) {
                // onEvent missing on older WebApp builds — non-fatal
            }
            return;
        }
        document.documentElement.setAttribute("data-theme", "dark");
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute("data-theme");
        const next = current === "light" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", next);
        localStorage.setItem("theme", next);
        // Re-assert our background colour so Telegram's injected
        // bg_color doesn't leak through after a theme toggle.
        const bg = next === "light" ? "#ffffff" : "#0a0a0b";
        document.body.style.backgroundColor = bg;
        try {
            const tg = window.Telegram && window.Telegram.WebApp;
            if (tg && tg.version && parseFloat(tg.version) >= 6.1) {
                tg.setBackgroundColor(bg);
            }
        } catch (_) {}
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
     * 'profit'.
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
