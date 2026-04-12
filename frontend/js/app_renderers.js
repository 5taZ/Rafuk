/* global Chart */

/**
 * app_renderers.js — Composition entry point.
 * Delegates to focused modules: core, cards, views, modals, charts, trackers.
 * Every function that the original file exported is still available on the
 * returned object so app_actions.js continues to work without changes.
 */

function createAppRenderers(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatRate,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
    } = context;

    // ── Instantiate sub-modules ──────────────────────────────────────────
    const core = createRenderCore(context);
    const cards = createRenderCards(context);
    const views = createRenderViews(context);
    const modals = createRenderModals(context);
    const charts = createRenderCharts(context);
    const trackers = createRenderTrackers(context);

    // ── Cross-module hooks ───────────────────────────────────────────────
    // Modules call these when they need functionality from another module.
    context._hooks = {
        showToast: core.showToast,
        renderChart: charts.renderChart,
        destroyChart: charts.destroyChart,
        renderHistory: charts.renderHistory,
        destroyHistoryChart: charts.destroyHistoryChart,
        renderHistoryRangeButtons: views.renderHistoryRangeButtons,
        renderDealsHeroStats: views.renderDealsHeroStats,
        renderProfitDashboard: charts.renderProfitDashboard,
        renderHistoryDeals: charts.renderHistoryDeals,
        renderMonitoringHeroStats: views.renderMonitoringHeroStats,
        renderWatchlistFilters: trackers.renderWatchlistFilters,
        renderTrackerEvents: trackers.renderTrackerEvents,
        renderStrictSearch: core.renderStrictSearch,
        renderTrackerInputs: views.renderTrackerInputs,
        renderLoading: core.renderLoading,
        // renderRecentSearches will be added after declarations below
    };

    // ── Forwarded functions (all names that app_actions.js destructures) ─
    const {
        escapeHtml,
        showToast,
        dismissToast,
        renderRates,
        renderError,
        renderLoading,
        renderCurrencyButtons,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
    } = core;

    const {
        buildListingNode,
        renderListingsCollection,
        renderListings,
        renderDeals,
        renderOpportunityBoard,
        renderLeads,
        renderWatchlist,
    } = cards;

    const {
        renderTrackingHeroStats,
        renderCheapHeroStats,
        renderMonitoringHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderTrackerEventFilters,
        renderHistoryRangeButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderComparison,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
    } = views;

    const {
        renderDetailModal,
        renderDetailRisks,
        closeDetailModal,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
    } = modals;

    const {
        destroyChart,
        destroyHistoryChart,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderProfitDashboard,
        renderHistoryDeals,
        renderVelocity,
    } = charts;

    const {
        renderTrackerStatus,
        renderWatchlistFilters,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters: renderTrackerEventFiltersFromTrackers,
    } = trackers;

    // Add renderRecentSearches to hooks now that it's declared
    context._hooks.renderRecentSearches = renderRecentSearches;

    // ── renderAll ────────────────────────────────────────────────────────

    function renderAll() {
        const start = performance.now();
        renderError();
        renderLoading();
        renderCurrencyButtons();
        renderStrictSearch();
        renderViewTabs();
        renderPanels();
        renderSummary();
        renderHelper();
        renderViews();
        renderTrackingHeroStats();
        renderCheapHeroStats();
        renderMonitoringHeroStats();
        renderDealsHeroStats();
        renderSortButtons();
        renderDiscountButtons();
        renderTrackerEventFilters();
        renderDealInputs();
        renderTrackerInputs();
        renderStats();
        renderHistory();
        renderComparison();
        renderSegments();
        renderGeography();
        renderRecentSearches();
        renderListings();
        renderDeals();
        renderRates();
        renderTrackerStatus();
        renderTrackers();
        renderTrackerEvents();
        renderLeads();
        renderWatchlist();
        renderProfitDashboard();
        const duration = performance.now() - start;
        if (duration > 16) {
            console.warn(`[perf] renderAll took ${duration.toFixed(1)}ms (>16ms threshold)`);
        }
    }

    // ── Public API (every name the original file exported) ───────────────

    return {
        showToast,
        dismissToast,
        escapeHtml,
        renderRates,
        renderError,
        renderLoading,
        renderCurrencyButtons,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
        renderTrackingHeroStats,
        renderCheapHeroStats,
        renderMonitoringHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderTrackerEventFilters,
        renderDealInputs,
        renderTrackerInputs,
        renderComparison,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderListingsCollection,
        renderListings,
        renderDeals,
        renderOpportunityBoard,
        renderTrackerStatus,
        renderTrackers,
        renderTrackerEvents,
        renderWatchlistFilters,
        renderLeads,
        renderWatchlist,
        renderProfitDashboard,
        renderHistoryDeals,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
        renderDetailRisks,
        renderVelocity,
        destroyChart,
        destroyHistoryChart,
        renderDetailModal,
        closeDetailModal,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderAll,
    };
}
