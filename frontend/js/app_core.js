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
        searchRequestId: 0,
        sort: "newest",
        discountFromPercent: 10,
        discountToPercent: 30,
        loading: false,
        error: null,
        stats: null,
        listings: [],
        dealListings: [],
        segments: null,
        geography: [],
        chart: null,
        history: [],
        historyChart: null,
        usdRateByn: null,
        leads: [],
        leadFilter: "all",
        watchlist: [],
        watchlistFilter: "all",
        trackers: [],
        trackerEvents: [],
        trackerEventFilter: "all",
        trackerEventFilterTrackerId: null,
        trackerStatus: "",
        trackerStatusKind: "info",
        trackerMinDiscountPercent: 10,
        trackerMaxPriceByn: null,
        trackerExcludeDuplicates: false,
        trackerSellerType: "",
        trackerCondition: "",
        trackerRegionName: "",
        trackerConfigKeyword: "",
        editingTrackerId: null,
        detail: null,
        detailImageIndex: 0,
        detailFromWatchlist: false,
        activeView: "overview",
        expenses: [],
        currentExpenseLeadId: null,
        profitData: null,
        profitChart: null,
        pipelineStep: "active",
        panels: {
            distribution: false,
            history: true,
            comparison: false,
            segments: false,
            geography: false,
            historyDeals: false,
        },
    };

    const elements = {};

    function cacheElements() {
        elements.searchInput = document.getElementById("search-input");
        elements.searchButton = document.getElementById("search-btn");
        elements.searchButtonLabel = document.getElementById("search-btn-label");
        elements.strictSearchToggle = document.getElementById("strict-search-toggle");
        elements.errorBar = document.getElementById("error-bar");
        elements.errorText = document.getElementById("error-text");
        elements.helperPanel = document.getElementById("helper-panel");
        elements.summaryStrip = document.getElementById("summary-strip");
        elements.summaryQuery = document.getElementById("summary-query");
        elements.summarySignal = document.getElementById("summary-signal");
        elements.summaryMedian = document.getElementById("summary-median");
        elements.summaryMarketTotal = document.getElementById("summary-market-total");
        elements.summaryCoverage = document.getElementById("summary-coverage");
        elements.viewTabs = Array.from(document.querySelectorAll("[data-view]"));
        elements.views = {
            overview: document.getElementById("overview-view"),
            ads: document.getElementById("ads-view"),
            tracking: document.getElementById("tracking-view"),
            cheap: document.getElementById("cheap-view"),
            monitoring: document.getElementById("monitoring-view"),
            deals: document.getElementById("deals-view"),
        };
        elements.statsSection = document.getElementById("stats-section");
        elements.chartSection = document.getElementById("chart-section");
        elements.historySection = document.getElementById("history-section");
        elements.historyEmpty = document.getElementById("history-empty");
        elements.historyBadge = document.getElementById("history-badge");
        elements.historySummary = document.getElementById("history-summary");
        elements.historyRangeButtons = Array.from(document.querySelectorAll("[data-history-days]"));
        elements.comparisonSection = document.getElementById("comparison-section");
        elements.compareInput = document.getElementById("compare-input");
        elements.compareButton = document.getElementById("compare-btn");
        elements.compareSwapButton = document.getElementById("compare-swap-btn");
        elements.comparisonNote = document.getElementById("comparison-note");
        elements.comparisonSummary = document.getElementById("comparison-summary");
        elements.comparisonGrid = document.getElementById("comparison-grid");
        elements.compareQuickChips = Array.from(document.querySelectorAll("[data-compare-query]"));
        elements.segmentsSection = document.getElementById("segments-section");
        elements.geographySection = document.getElementById("geography-section");
        elements.geographyGrid = document.getElementById("geography-grid");
        elements.geographyNote = document.getElementById("geography-note");
        elements.listingsSection = document.getElementById("listings-section");
        elements.dealsSection = document.getElementById("deals-section");
        elements.marketTotalBadge = document.getElementById("market-total-badge");
        elements.listingsTotalBadge = document.getElementById("listings-total-badge");
        elements.dealsTotalBadge = document.getElementById("deals-total-badge");
        elements.stats = {
            median: document.getElementById("stat-median"),
            mean: document.getElementById("stat-mean"),
            min: document.getElementById("stat-min"),
            max: document.getElementById("stat-max"),
            coverage: document.getElementById("stat-coverage"),
            fairRange: document.getElementById("stat-fair-range"),
        };
        elements.rateStrip = document.getElementById("rate-strip");
        elements.usdRateValue = document.getElementById("usd-rate-value");
        elements.segmentsGrid = document.getElementById("segments-grid");
        elements.listingsList = document.getElementById("listings-list");
        elements.dealsList = document.getElementById("deals-list");
        elements.sortButtons = Array.from(document.querySelectorAll("[data-sort]"));
        elements.discountButtons = Array.from(document.querySelectorAll("[data-discount-from]"));
        elements.trackerEventFilterButtons = Array.from(
            document.querySelectorAll("[data-event-filter]")
        );
        elements.dealFromInput = document.getElementById("deal-from-input");
        elements.dealToInput = document.getElementById("deal-to-input");
        elements.dealApplyButton = document.getElementById("deal-apply-btn");
        elements.quickChips = Array.from(document.querySelectorAll("[data-query]"));
        elements.trackerPanel = document.getElementById("tracker-panel");
        elements.trackQueryButton = document.getElementById("track-query-btn");
        elements.trackerMinDiscountInput = document.getElementById("tracker-min-discount-input");
        elements.trackerMaxPriceInput = document.getElementById("tracker-max-price-input");
        elements.trackerExcludeDuplicatesToggle = document.getElementById("tracker-exclude-duplicates-toggle");
        elements.trackerSellerSelect = document.getElementById("tracker-seller-select");
        elements.trackerConditionSelect = document.getElementById("tracker-condition-select");
        elements.trackerRegionInput = document.getElementById("tracker-region-input");
        elements.trackerConfigInput = document.getElementById("tracker-config-input");
        elements.trackerStatus = document.getElementById("tracker-status");
        elements.trackersList = document.getElementById("trackers-list");
        elements.trackerEventsList = document.getElementById("tracker-events-list");
        elements.clearEventsButton = document.getElementById("clear-events-btn");
        elements.leadInboxSection = document.getElementById("lead-inbox-section");
        elements.leadFilterButtons = Array.from(document.querySelectorAll("[data-lead-filter]"));
        elements.leadInboxList = document.getElementById("lead-inbox-list");
        elements.clearAllLeadsButton = document.getElementById("clear-all-leads-btn");
        elements.watchlistSection = document.getElementById("watchlist-section");
        elements.deleteAllWatchlistButton = document.getElementById("delete-all-watchlist-btn");
        elements.watchlistFilterButtons = Array.from(document.querySelectorAll("[data-watch-filter]"));
        elements.watchlistList = document.getElementById("watchlist-list");
        elements.trackingHeroStats = document.getElementById("tracking-hero-stats");
        elements.cheapHeroStats = document.getElementById("cheap-hero-stats");
        elements.monitoringHeroStats = document.getElementById("monitoring-hero-stats");
        elements.dealsHeroStats = document.getElementById("deals-hero-stats");
        elements.detailModal = document.getElementById("detail-modal");
        elements.detailOverlay = document.getElementById("detail-overlay");
        elements.detailClose = document.getElementById("detail-close");
        elements.detailMainImage = document.getElementById("detail-main-image");
        elements.detailNoImage = document.getElementById("detail-no-image");
        elements.detailThumbs = document.getElementById("detail-thumbs");
        elements.detailTitle = document.getElementById("detail-title");
        elements.detailPrice = document.getElementById("detail-price");
        elements.detailMeta = document.getElementById("detail-meta");
        elements.detailDescription = document.getElementById("detail-description");
        elements.detailProfitBlock = document.getElementById("detail-profit-block");
        elements.detailProfit = document.getElementById("detail-profit");
        elements.detailLiquidityBlock = document.getElementById("detail-liquidity-block");
        elements.detailLiquidity = document.getElementById("detail-liquidity");
        elements.detailAddLeadButton = document.getElementById("detail-add-lead-btn");
        elements.detailAddWatchlistButton = document.getElementById("detail-add-watchlist-btn");
        elements.detailLink = document.getElementById("detail-link");
        elements.detailParamsBlock = document.getElementById("detail-params-block");
        elements.detailParams = document.getElementById("detail-params");
        elements.detailSellerBlock = document.getElementById("detail-seller-block");
        elements.detailSeller = document.getElementById("detail-seller");
        elements.detailRiskBlock = document.getElementById("detail-risk-block");
        elements.detailRisks = document.getElementById("detail-risks");
        elements.expensesModal = document.getElementById("expenses-modal");
        elements.expensesOverlay = document.getElementById("expenses-overlay");
        elements.expensesClose = document.getElementById("expenses-close");
        elements.expensesTitle = document.getElementById("expenses-title");
        elements.expensesSubtitle = document.getElementById("expenses-subtitle");
        elements.expensesList = document.getElementById("expenses-list");
        elements.expenseFormWrap = document.getElementById("expense-form-wrap");
        elements.expenseTypeSelect = document.getElementById("expense-type-select");
        elements.expenseAmountInput = document.getElementById("expense-amount-input");
        elements.expenseNotesInput = document.getElementById("expense-notes-input");
        elements.saveExpenseButton = document.getElementById("save-expense-btn");
        elements.cancelExpenseButton = document.getElementById("cancel-expense-btn");
        elements.profitDashboardSection = document.getElementById("profit-dashboard-section");
        elements.profitCards = document.getElementById("profit-cards");
        elements.profitChartBox = document.getElementById("profit-chart-box");
        elements.exportLeadsButton = document.getElementById("export-leads-btn");
        elements.historyDealsSection = document.getElementById("history-deals-section");
        elements.historyDealsCount = document.getElementById("history-deals-count");
        elements.historyDealsList = document.getElementById("history-deals-list");
        elements.toastContainer = document.getElementById("toast-container");
        elements.currencyButtons = {
            BYN: document.getElementById("btn-byn"),
            USD: document.getElementById("btn-usd"),
        };
        elements.editTrackerModal = document.getElementById("edit-tracker-modal");
        elements.editTrackerQuery = document.getElementById("edit-tracker-query");
        elements.editStrictModeToggle = document.getElementById("edit-strict-mode-toggle");
        elements.editMinDiscountInput = document.getElementById("edit-min-discount-input");
        elements.editMaxPriceInput = document.getElementById("edit-max-price-input");
        elements.editSellerSelect = document.getElementById("edit-seller-select");
        elements.editConditionSelect = document.getElementById("edit-condition-select");
        elements.editRegionInput = document.getElementById("edit-region-input");
        elements.editConfigInput = document.getElementById("edit-config-input");
        elements.editExcludeDuplicatesToggle = document.getElementById("edit-exclude-duplicates-toggle");
        elements.closeEditModal = document.getElementById("close-edit-modal");
        elements.saveTrackerBtn = document.getElementById("save-tracker-btn");
        elements.cancelEditBtn = document.getElementById("cancel-edit-btn");
        elements.panelToggles = Array.from(document.querySelectorAll("[data-panel-toggle]"));
        elements.panelBodies = {
            distribution: document.getElementById("distribution-body"),
            history: document.getElementById("history-body"),
            comparison: document.getElementById("comparison-body"),
            segments: document.getElementById("segments-body"),
            geography: document.getElementById("geography-body"),
            historyDeals: document.getElementById("history-deals-body"),
        };
    }

    function initTelegramTheme() {
        if (window.Telegram && window.Telegram.WebApp) {
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

    function formatPrice(value) {
        if (value == null || value === 0) return "—";
        const numeric = Number(value);
        if (Number.isNaN(numeric)) return "—";

        if (state.currency === "BYN") {
            if (numeric >= 10000) {
                return `${(numeric / 1000).toFixed(1).replace(/\.0$/, "")} тыс. р.`;
            }
            if (numeric >= 1000) {
                return `${(numeric / 1000).toFixed(2).replace(/0+$/, "").replace(/\.$/, "")} тыс. р.`;
            }
            return `${Math.round(numeric)} р.`;
        }

        return `$${numeric.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
    }

    function formatRate(value) {
        if (!value) return "—";
        return `${Number(value).toFixed(4)} BYN`;
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

    function formatDate(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return new Intl.DateTimeFormat("ru-BY", {
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
        }).format(date);
    }

    function hasTelegramInitData() {
        return Boolean(
            window.Telegram &&
            window.Telegram.WebApp &&
            window.Telegram.WebApp.initData
        );
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

    return {
        state,
        elements,
        cacheElements,
        initTelegramTheme,
        formatPrice,
        formatRate,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
    };
}
