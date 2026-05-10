const APP_REGIONS = {
    "Минск": ["Заводской", "Ленинский", "Московский", "Октябрьский", "Партизанский", "Первомайский", "Советский", "Фрунзенский", "Центральный"],
    "Брестская область": ["Брест", "Барановичи", "Береза", "Ганцевичи", "Дрогичин", "Жабинка", "Иваново", "Ивацевичи", "Каменец", "Кобрин", "Лунинец", "Ляховичи", "Малорита", "Пинск", "Пружаны", "Столин"],
    "Витебская область": ["Витебск", "Бешенковичи", "Браслав", "Верхнедвинск", "Глубокое", "Городок", "Докшицы", "Дубровно", "Лепель", "Лиозно", "Миоры", "Новополоцк", "Орша", "Полоцк", "Поставы", "Россоны", "Сенно", "Толочин", "Ушачи", "Чашники", "Шарковщина", "Шумилино"],
    "Гомельская область": ["Гомель", "Брагин", "Буда-Кошелево", "Ветка", "Добруш", "Ельск", "Житковичи", "Жлобин", "Калинковичи", "Корма", "Лельчицы", "Лоев", "Мозырь", "Наровля", "Октябрьский", "Петриков", "Речица", "Рогачев", "Светлогорск", "Хойники", "Чечерск"],
    "Гродненская область": ["Гродно", "Берестовица", "Волковыск", "Вороново", "Дятлово", "Зельва", "Ивье", "Кореличи", "Лида", "Мосты", "Новогрудок", "Островец", "Ошмяны", "Свислочь", "Слоним", "Сморгонь", "Щучин"],
    "Минская область": ["Минский", "Березино", "Борисов", "Вилейка", "Воложин", "Дзержинск", "Жодино", "Клецк", "Копыль", "Крупки", "Логойск", "Любань", "Марьина Горка", "Молодечно", "Мядель", "Несвиж", "Слуцк", "Смолевичи", "Солигорск", "Старые Дороги", "Столбцы", "Узда", "Червень"],
    "Могилевская область": ["Могилев", "Белыничи", "Бобруйск", "Быхов", "Глуск", "Горки", "Дрибин", "Кировск", "Климовичи", "Кличев", "Костюковичи", "Краснополье", "Кричев", "Круглое", "Мстиславль", "Осиповичи", "Славгород", "Хотимск", "Чаусы", "Чериков", "Шклов"],
};

function cacheAppElements(elements) {
    elements.searchInput = document.getElementById("search-input");
    elements.searchButton = document.getElementById("search-btn");
    elements.searchButtonLabel = document.getElementById("search-btn-label");
    elements.strictSearchToggle = document.getElementById("strict-search-toggle");
    elements.errorBar = document.getElementById("error-bar");
    elements.errorText = document.getElementById("error-text");
    elements.errorRetry = document.getElementById("error-retry");
    elements.helperPanel = document.getElementById("helper-panel");
    elements.summaryStrip = document.getElementById("summary-strip");
    elements.summaryQuery = document.getElementById("summary-query");
    elements.summarySignal = document.getElementById("summary-signal");
    elements.summaryMedian = document.getElementById("summary-median");
    elements.summaryRange = document.getElementById("summary-range");
    elements.summaryFair = document.getElementById("summary-fair");
    elements.summaryRefinements = document.getElementById("summary-refinements");
    elements.summaryRefinementsChips = document.getElementById("summary-refinements-chips");
    elements.viewTabs = Array.from(document.querySelectorAll("[data-view]"));
    elements.views = {
        overview: document.getElementById("overview-view"),
        ads: document.getElementById("ads-view"),
        tracking: document.getElementById("tracking-view"),
        deals: document.getElementById("deals-view"),
    };
    elements.dealsControls = document.getElementById("deals-controls");
    elements.statsSection = document.getElementById("stats-section");
    elements.chartSection = document.getElementById("chart-section");
    elements.priceChartCanvas = document.getElementById("priceChart");
    elements.historyChartCanvas = document.getElementById("historyChart");
    elements.historySection = document.getElementById("history-section");
    elements.historyEmpty = document.getElementById("history-empty");
    elements.historyBadge = document.getElementById("history-badge");
    elements.historySummary = document.getElementById("history-summary");
    elements.historyRangeButtons = Array.from(document.querySelectorAll("[data-history-days]"));
    elements.segmentsSection = document.getElementById("segments-section");
    elements.geographySection = document.getElementById("geography-section");
    elements.geographyGrid = document.getElementById("geography-grid");
    elements.geographyNote = document.getElementById("geography-note");
    elements.listingsSection = document.getElementById("listings-section");
    elements.marketTotalBadge = document.getElementById("market-total-badge");
    elements.listingsTotalBadge = document.getElementById("listings-total-badge");
    elements.listingsFallbackBadge = document.getElementById("listings-fallback-badge");
    elements.stats = {
        median: document.getElementById("stat-median"),
        mean: document.getElementById("stat-mean"),
        min: document.getElementById("stat-min"),
        max: document.getElementById("stat-max"),
        coverage: document.getElementById("stat-coverage"),
        fairRange: document.getElementById("stat-fair-range"),
    };
    elements.segmentsGrid = document.getElementById("segments-grid");
    elements.listingsList = document.getElementById("listings-list");
    elements.sortButtons = Array.from(document.querySelectorAll("[data-sort]"));
    elements.discountButtons = Array.from(document.querySelectorAll("[data-discount-from]"));
    elements.trackerEventFilterButtons = Array.from(document.querySelectorAll("[data-event-filter]"));
    // Per-filter count badges, keyed by filter name ("all" | "price_drop" |
    // "new_listing"). Updated in renderTrackerEventFilters so the user
    // sees how many alerts each tab represents before tapping it.
    elements.trackerEventFilterCounts = {};
    for (const node of document.querySelectorAll("[data-event-filter-count]")) {
        elements.trackerEventFilterCounts[node.dataset.eventFilterCount] = node;
    }
    elements.dealFromInput = document.getElementById("deal-from-input");
    elements.dealToInput = document.getElementById("deal-to-input");
    elements.dealApplyButton = document.getElementById("deal-apply-btn");
    elements.trackerPanel = document.getElementById("tracker-panel");
    elements.trackQueryButton = document.getElementById("track-query-btn");
    elements.trackerMinDiscountInput = document.getElementById("tracker-min-discount-input");
    elements.trackerMaxPriceInput = document.getElementById("tracker-max-price-input");
    elements.trackerSellerSelect = document.getElementById("tracker-seller-select");
    elements.trackerConditionSelect = document.getElementById("tracker-condition-select");
    elements.trackerRegionSelect = document.getElementById("tracker-region-select");
    elements.trackerConfigInput = document.getElementById("tracker-config-input");
    elements.trackerStatus = document.getElementById("tracker-status");
    elements.trackersList = document.getElementById("trackers-list");
    elements.trackerEventsList = document.getElementById("tracker-events-list");
    elements.clearEventsButton = document.getElementById("clear-events-btn");
    elements.trackerEventTrackerSelect = document.getElementById("tracker-event-tracker-select");
    elements.leadInboxSection = document.getElementById("lead-inbox-section");
    elements.leadFilterButtons = Array.from(document.querySelectorAll("[data-lead-filter]"));
    elements.leadInboxList = document.getElementById("lead-inbox-list");
    elements.clearAllLeadsButton = document.getElementById("clear-all-leads-btn");
    // Watchlist section is gone — its items live inside the unified
    // "Мои объявления" list now. We keep the deleteAllWatchlistButton
    // reference so JS can still bind a handler when the chip "Слежу"
    // is the active filter.
    elements.deleteAllWatchlistButton = document.getElementById("delete-all-watchlist-btn");
    elements.itemsFilterRow = document.getElementById("items-filter-row");
    elements.itemsFilterButtons = Array.from(document.querySelectorAll("[data-items-filter]"));
    elements.itemsCountBadges = {};
    for (const node of document.querySelectorAll("[data-items-count]")) {
        elements.itemsCountBadges[node.dataset.itemsCount] = node;
    }
    elements.trackingHeroStats = document.getElementById("tracking-hero-stats");
    elements.dealsHeroStats = document.getElementById("deals-hero-stats");
    elements.detailModal = document.getElementById("detail-modal");
    elements.detailOverlay = document.getElementById("detail-overlay");
    elements.detailClose = document.getElementById("detail-close");
    elements.detailMainImage = document.getElementById("detail-main-image");
    elements.detailMedia = document.getElementById("detail-media");
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
    elements.detailAiBlock = document.getElementById("detail-ai-block");
    elements.detailAiContent = document.getElementById("detail-ai-content");
    elements.detailAiBtn = document.getElementById("detail-ai-btn");
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
    elements.aiModal = document.getElementById("ai-modal");
    elements.aiOverlay = document.getElementById("ai-overlay");
    elements.aiModalClose = document.getElementById("ai-modal-close");
    elements.aiModalSubtitle = document.getElementById("ai-modal-subtitle");
    elements.aiModalLoading = document.getElementById("ai-modal-loading");
    elements.aiLoaderText = document.getElementById("ai-loader-text");
    elements.aiProgressBar = document.getElementById("ai-progress-bar");
    elements.aiProgressPct = document.getElementById("ai-progress-pct");
    elements.aiModalError = document.getElementById("ai-modal-error");
    elements.aiModalResult = document.getElementById("ai-modal-result");
    elements.profitDashboardSection = document.getElementById("profit-dashboard-section");
    elements.profitCards = document.getElementById("profit-cards");
    elements.profitChartBox = document.getElementById("profit-chart-box");
    elements.analyticsPeriodButtons = Array.from(
        document.querySelectorAll("[data-analytics-period]"),
    );
    elements.historyDealsSection = document.getElementById("history-deals-section");
    elements.historyDealsCount = document.getElementById("history-deals-count");
    elements.historyDealsList = document.getElementById("history-deals-list");
    elements.toastContainer = document.getElementById("toast-container");
    elements.recentSection = document.getElementById("recent-section");
    elements.recentList = document.getElementById("recent-list");
    elements.recentClearBtn = document.getElementById("recent-clear-btn");
    elements.filterBtn = document.getElementById("filter-btn");
    elements.filterDropdown = document.getElementById("filter-dropdown");
    elements.filterCategories = document.getElementById("filter-categories");
    elements.filterConditions = document.getElementById("filter-conditions");
    elements.filterSellers = document.getElementById("filter-sellers");
    elements.filterMinPrice = document.getElementById("filter-min-price");
    elements.filterMaxPrice = document.getElementById("filter-max-price");
    elements.filterRegion = document.getElementById("filter-region");
    elements.filterApplyBtn = document.querySelector(".filter-btn--apply");
    elements.filterCancelBtn = document.querySelector(".filter-btn--cancel");
    elements.editTrackerModal = document.getElementById("edit-tracker-modal");
    elements.editTrackerQuery = document.getElementById("edit-tracker-query");
    elements.editStrictModeToggle = document.getElementById("edit-strict-mode-toggle");
    elements.editMinDiscountInput = document.getElementById("edit-min-discount-input");
    elements.editMaxPriceInput = document.getElementById("edit-max-price-input");
    elements.editSellerSelect = document.getElementById("edit-seller-select");
    elements.editConditionSelect = document.getElementById("edit-condition-select");
    elements.editRegionSelect = document.getElementById("edit-region-select");
    elements.editConfigInput = document.getElementById("edit-config-input");
    elements.closeEditModal = document.getElementById("close-edit-modal");
    elements.saveTrackerBtn = document.getElementById("save-tracker-btn");
    elements.cancelEditBtn = document.getElementById("cancel-edit-btn");
    elements.panelToggles = Array.from(document.querySelectorAll("[data-panel-toggle]"));
    elements.panelBodies = {
        distribution: document.getElementById("distribution-body"),
        history: document.getElementById("history-body"),
        segments: document.getElementById("segments-body"),
        geography: document.getElementById("geography-body"),
        historyDeals: document.getElementById("history-deals-body"),
    };
}

function populateRegionSelectOptions(selectEl, currentValue) {
    if (!selectEl) return;
    // Requires: domEl() from dom_helpers.js (loaded before this file)
    const prev = currentValue || "";
    const fragment = document.createDocumentFragment();
    fragment.appendChild(domEl("option", { value: "", text: "Любой" }));
    for (const [region, cities] of Object.entries(APP_REGIONS)) {
        const group = domEl("optgroup", { attrs: { label: region } });
        group.appendChild(domEl("option", { value: region, text: `${region} (все)` }));
        for (const city of cities) {
            group.appendChild(domEl("option", { value: city, text: city }));
        }
        fragment.appendChild(group);
    }
    selectEl.replaceChildren(fragment);
    selectEl.value = prev;
}
