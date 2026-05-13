/* global Chart */

/**
 * app_renderers.js — Composition entry point.
 * Delegates to focused modules: core, cards, views, modals, charts, trackers.
 * Every function that the original file exported is still available on the
 * returned object so app_actions.js continues to work without changes.
 */

function createAppRenderers(baseContext) {
    const context = { ...baseContext };
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        isDirty: isDirtyFn,
        clearDirty: clearDirtyFn,
    } = context;

    // ── Instantiate sub-modules ──────────────────────────────────────────
    const core = createRenderCore(context);

    // Share escapeHtml and safeRender across sub-modules via context
    context.escapeHtml = core.escapeHtml;
    context.safeUrl = core.safeUrl;
    context.optimizedImage = core.optimizedImage;
    context.safeRender = safeRender;
    // Empty state factory is invoked by every collection renderer
    // (watchlist, leads, tracker events) to swap the previous plain
    // ".tracker-empty" paragraph for a richer iconified block.
    context.buildEmptyState = core.buildEmptyState;

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
        } catch (err) {
            console.error('safeRender error:', name, err);
            if (typeof core.showToast === 'function') {
                core.showToast("Ошибка отображения", 'error');
            }
            return null;
        }
    }

    // ── Forwarded functions (all names that app_actions.js destructures) ─
    const {
        escapeHtml,
        safeUrl,
        showToast,
        dismissToast,
        renderError,
        renderLoading,
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
        renderLeads,
        renderWatchlist,
    } = cards;

    const {
        renderTrackingHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderHistoryRangeButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderFilterDropdown,
    } = views;

    const {
        renderDetailModal,
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
        renderTrackerEventFilters,
    } = trackers;

    // Add renderRecentSearches to hooks now that it's declared
    context._hooks.renderRecentSearches = renderRecentSearches;
    context._hooks.renderFilterDropdown = renderFilterDropdown;

    // ── renderAll ────────────────────────────────────────────────────────

    /**
     * Map of render functions keyed by dirty-flag name.
     * If no dirty flags are set, all renderers run (first-call / full-refresh).
     */
    const _renderMap = {
        error: renderError,
        loading: renderLoading,
        strict: renderStrictSearch,
        tabs: renderViewTabs,
        panels: renderPanels,
        summary: renderSummary,
        helper: renderHelper,
        views: renderViews,
        trackingHero: renderTrackingHeroStats,
        dealsHero: renderDealsHeroStats,
        sort: renderSortButtons,
        discount: renderDiscountButtons,
        eventFilters: renderTrackerEventFilters,
        dealInputs: renderDealInputs,
        trackerInputs: renderTrackerInputs,
        stats: renderStats,
        history: renderHistory,
        segments: renderSegments,
        geography: renderGeography,
        recent: renderRecentSearches,
        categories: renderFilterDropdown,
        listings: renderListings,
        trackerStatus: renderTrackerStatus,
        trackers: renderTrackers,
        trackerEvents: renderTrackerEvents,
        leads: renderLeads,
        watchlist: renderWatchlist,
        profit: renderProfitDashboard,
    };

    function renderAll() {
        const hasSelectiveFlags = state.ui.dirtyViews.size > 0 && !state.ui._allDirty;
        try {
            if (hasSelectiveFlags) {
                // Selective render — only flagged views
                for (const [key, fn] of Object.entries(_renderMap)) {
                    if (state.ui.dirtyViews.has(key)) fn();
                }
            } else {
                // Full render — no specific flags or _allDirty is set
                for (const fn of Object.values(_renderMap)) fn();
            }
        } catch (err) {
            console.error('renderAll error:', err);
            if (typeof core.showToast === 'function') {
                core.showToast('Ошибка отображения', 'error');
            }
        }
        state.ui._allDirty = false;
        state.ui.dirtyViews.clear();
    }

    // Batch multiple renderAll calls into a single requestAnimationFrame.
    // This prevents render cascades when parallel API calls each trigger renderAll.
    let _renderScheduled = false;
    function scheduleRender() {
        if (!_renderScheduled) {
            _renderScheduled = true;
            requestAnimationFrame(() => {
                _renderScheduled = false;
                renderAll();
            });
        }
    }

    // ── Public API (every name the original file exported) ───────────────

    return {
        showToast,
        dismissToast,
        escapeHtml,
        safeUrl,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
        renderTrackingHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderTrackerEventFilters,
        renderDealInputs,
        renderTrackerInputs,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderListingsCollection,
        renderListings,
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
        destroyChart,
        destroyHistoryChart,
        renderDetailModal,
        closeDetailModal,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderAll,
        scheduleRender,
    };
}
