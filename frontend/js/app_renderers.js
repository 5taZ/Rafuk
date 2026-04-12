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

    // Share escapeHtml and safeRender across sub-modules via context
    context.escapeHtml = core.escapeHtml;
    context.safeRender = safeRender;

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

    // ── Error boundary pattern ──────────────────────────────────────────
    /**
     * Wraps a render function in a try/catch to prevent a single render
     * error from crashing the entire app. Logs the error and shows a
     * fallback toast notification instead.
     *
     * @param {string} name - Human-readable render function name
     * @param {Function} fn - The render function to execute
     * @returns {*} The return value of fn, or null on error
     */
    function safeRender(name, fn) {
        try {
            return fn();
        } catch (error) {
            console.error("Render error:", error.message);
            if (typeof core.showToast === 'function') {
                core.showToast("Ошибка отображения", 'error');
            }
            return null;
        }
    }

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
        try {
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
        } catch (error) {
            if (typeof core.showToast === 'function') {
                core.showToast('Ошибка отображения', 'error');
            }
        }
        const duration = performance.now() - start;
        if (duration > 50) {
            console.warn(`[perf] renderAll took ${duration.toFixed(1)}ms (>50ms threshold)`);
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
