/**
 * app_actions.js — Composition entry point for all action / API modules.
 *
 * Delegates to focused modules: api_core, api_listings, api_trackers,
 * api_leads, api_watchlist, api_events.
 *
 * Every function that the original monolithic file exported is still available
 * on the returned object so app.js and renderers continue to work without changes.
 */

function createAppActions(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        trapFocus,
        renderAll,
        markDirty,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderViews,
        renderSortButtons,
        renderDiscountButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderComparison,
        renderHistory,
        renderTrackerStatus,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
        renderLeads,
        renderWatchlist,
        renderDetailModal,
        closeDetailModal,
        setPanelOpen,
        showToast,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
        renderProfitDashboard,
        renderDetailRisks,
        renderMonitoringHeroStats,
        renderDealsHeroStats,
    } = context;

    // ── Internal helpers (originally inside the monolithic app_actions) ───
    // These are not part of any module because they bridge multiple modules
    // and the view/scroll lifecycle.

    /**
     * Switches the active view with a subtle enter animation.
     * Updates tab states, triggers view transition, and re-renders view content.
     *
     * @param {string} view - The view key to activate (e.g., 'overview', 'ads', 'tracking')
     */
    function setActiveView(view) {
        if (!(view in elements.views)) {
            return;
        }
        state.activeView = view;
        markDirty('tabs', 'views');
        renderAll();

        const viewEl = elements.views[view];
        if (viewEl) {
            viewEl.classList.add("is-entering");
            setTimeout(() => {
                viewEl.classList.remove("is-entering");
            }, 200);
        }
    }

    /**
     * Smoothly scrolls a section into view at the top of the viewport.
     *
     * @param {HTMLElement|null} section - The DOM element to scroll to
     */
    function scrollSectionIntoView(section) {
        section?.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    /**
     * Focuses the appropriate view or panel based on the search target.
     * Routes to overview by default, then scrolls to specific sections if needed.
     *
     * @param {string} target - The target context ('ads', 'cheap', 'deals', 'history', 'comparison')
     */
    function focusTarget(target) {
        if (target === "ads") {
            setActiveView("ads");
            return;
        }
        if (target === "cheap") {
            setActiveView("cheap");
            return;
        }
        if (target === "deals") {
            setActiveView("deals");
            return;
        }

        setActiveView("overview");

        if (target === "history") {
            setPanelOpen("history", true);
            scrollSectionIntoView(elements.historySection);
        } else if (target === "comparison") {
            setPanelOpen("comparison", true);
            scrollSectionIntoView(elements.comparisonSection);
        }
    }

    // Inject into context so every module can reach them
    context.setActiveView = setActiveView;
    context.scrollSectionIntoView = scrollSectionIntoView;
    context.focusTarget = focusTarget;
    context.markDirty = markDirty;

    // ── Instantiate sub-modules ──────────────────────────────────────────
    const core = createApiCore(context);
    
    // Add core functions to context BEFORE creating other modules
    // This prevents "X is not a function" errors when modules destructure context
    Object.assign(context, {
        telegramHeaders: core.telegramHeaders,
        requestJson: core.requestJson,
        getJson: core.getJson,
        postJson: core.postJson,
        deleteJson: core.deleteJson,
        buildCommonQuery: core.buildCommonQuery,
    });
    
    const listings = createApiListings(context);
    const trackers = createApiTrackers(context);
    const leads = createApiLeads(context);
    const watchlist = createApiWatchlist(context);

    // ── Cross-module hooks (actions that modules call into each other) ───
    // These are injected into context so every module can reach them.
    Object.assign(context, {
        // From listings
        search: listings.search,
        loadListings: listings.loadListings,
        loadDeals: listings.loadDeals,
        loadComparison: listings.loadComparison,
        swapComparisonQueries: listings.swapComparisonQueries,
        openListingDetail: listings.openListingDetail,
        loadSearchDependencies: listings.loadSearchDependencies,
        clearSearchData: listings.clearSearchData,
        loadHistory: listings.loadHistory,
        loadDetailRisks: listings.loadDetailRisks,

        // From trackers
        loadTrackers: trackers.loadTrackers,
        createTracker: trackers.createTracker,
        pauseTracker: trackers.pauseTracker,
        resumeTracker: trackers.resumeTracker,
        deleteTracker: trackers.deleteTracker,
        openEditTracker: trackers.openEditTracker,
        closeEditTracker: trackers.closeEditTracker,
        saveTracker: trackers.saveTracker,
        refreshTrackerEvents: trackers.refreshTrackerEvents,
        startTrackerRefresh: trackers.startTrackerRefresh,
        stopTrackerRefresh: trackers.stopTrackerRefresh,

        // From leads
        loadLeads: leads.loadLeads,
        clearAllLeads: leads.clearAllLeads,
        confirmLead: leads.confirmLead,
        cancelLead: leads.cancelLead,
        closeDeal: leads.closeDeal,
        revertLeadStage: leads.revertLeadStage,
        deleteLead: leads.deleteLead,
        updateLeadMeta: leads.updateLeadMeta,
        updateLeadStatus: leads.updateLeadStatus,
        markLeadAsSold: leads.markLeadAsSold,
        openLeadDetail: leads.openLeadDetail,

        // From watchlist
        loadWatchlist: watchlist.loadWatchlist,
        clearAllWatchlist: watchlist.clearAllWatchlist,
        addWatchlistFromListing: watchlist.addWatchlistFromListing,
        updateWatchlistMeta: watchlist.updateWatchlistMeta,
        updateWatchlistStatus: watchlist.updateWatchlistStatus,
        promoteWatchlistToLead: watchlist.promoteWatchlistToLead,
        openWatchlistDetail: watchlist.openWatchlistDetail,
        deleteWatchlistItem: watchlist.deleteWatchlistItem,
        deleteAllWatchlist: watchlist.deleteAllWatchlist,
        refreshWatchlist: watchlist.refreshWatchlist,

        // View / focus helpers are injected above as context.setActiveView, context.focusTarget
    });

    // ── Currency switcher (must be defined BEFORE createApiEvents) ───────
    /**
     * Switches the display currency and refreshes all loaded data.
     * Persists the preference to localStorage and re-fetches prices if a query is active.
     *
     * @param {'BYN'|'USD'} currency - The target currency code
     * @returns {Promise<void>}
     */
    async function setCurrency(currency) {
        if (!currency || state.currency === currency) {
            return;
        }

        state.currency = currency;

        // Persist currency preference
        if (context.saveCurrency) {
            context.saveCurrency();
        }

        if (state.query.trim()) {
            listings.clearSearchData();
            state.loading = true;
            markDirty('currency', 'loading');
            renderAll();
            await listings.search(state.activeView);
            return;
        }

        markDirty('currency', 'rates');
        renderAll();
        await core.loadRates();
    }

    // Add to context BEFORE createApiEvents so it can be destructured
    context.setCurrency = setCurrency;

    // ── Wire events module (needs all action functions on context) ────────
    const events = createApiEvents(context);

    // ── Convenience: addLeadFromListing (cross-cutting: listings → leads) ─
    /**
     * Adds a listing as a lead (purchase) with market estimate data.
     * Prevents duplicate entries and shows appropriate feedback.
     *
     * @param {Object} item - The listing data
     * @param {string} [source='manual'] - How the lead was created
     * @param {string|null} [queryOverride=null] - Override the current query
     * @returns {Promise<void>}
     */
    async function addLeadFromListing(item, source = "manual", queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }

        const alreadyInLeads = state.leads.some((l) => l.ad_id === item.ad_id);
        if (alreadyInLeads) {
            showToast("Уже в покупках");
            return;
        }

        try {
            const marketEstimate = (item.flip_estimates || []).find((entry) => entry.label === "По рынку");
            await core.postJson("/api/v1/leads", {
                query: queryOverride || state.query || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
                target_resale_byn: marketEstimate?.target_price || null,
                status: "new",
                source,
                thumbnail: item.thumbnail || null,
            });
            showToast("Добавлено в покупки", "success");
            await leads.loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось добавить в покупки", "error");
        }
    }

    // Make addLeadFromListing available on context for cross-module calls
    context.addLeadFromListing = addLeadFromListing;

    // ── Additional actions not in any module ──────────────────────────────

    async function deleteHistoryDeal(leadId) {
        try {
            await core.deleteJson(`/api/v1/leads/${leadId}`);
            showToast("✓ Сделка удалена из истории");
            await leads.loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось удалить сделку");
        }
    }

    async function refreshLeads() {
        try {
            const payload = await core.postJson("/api/v1/leads/refresh", {});
            const parts = [`${payload.checked} проверено`, `${payload.active} активно`];
            if (payload.missing > 0) {
                parts.push(`${payload.missing} пропало`);
            }
            showToast(`Покупки: ${parts.join(", ")}`);
            await leads.loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось проверить покупки");
        }
    }

    async function openOpportunityQuery(item) {
        if (!item) {
            return;
        }

        state.strictSearch = Boolean(item.strict_mode);
        state.discountFromPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMinDiscountPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMaxPriceByn = item.max_price_byn ?? null;
        state.trackerExcludeDuplicates = Boolean(item.exclude_duplicates);
        state.trackerSellerType = item.seller_type || "";
        state.trackerCondition = item.condition || "";
        state.trackerRegionName = item.region_name || "";
        state.trackerConfigKeyword = item.config_keyword || "";
        elements.searchInput.value = item.query;
        state.query = item.query;
        renderStrictSearch();
        renderDealInputs();
        renderTrackerInputs();
        await listings.search("overview");
    }

    async function openOpportunityDetail(item) {
        if (!item?.listing) {
            return;
        }

        state.strictSearch = Boolean(item.strict_mode);
        state.discountFromPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMinDiscountPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMaxPriceByn = item.max_price_byn ?? null;
        state.trackerExcludeDuplicates = Boolean(item.exclude_duplicates);
        state.trackerSellerType = item.seller_type || "";
        state.trackerCondition = item.condition || "";
        state.trackerRegionName = item.region_name || "";
        state.trackerConfigKeyword = item.config_keyword || "";
        elements.searchInput.value = item.query;
        state.query = item.query;
        renderStrictSearch();
        renderDealInputs();
        renderTrackerInputs();
        await listings.openListingDetail(item.listing);
    }

    /**
     * Parses URL launch parameters (?query=...&view=...) and initializes the app state.
     * Called on app startup to handle deep linking.
     *
     * @returns {Promise<void>}
     */
    async function applyLaunchParams() {
        const params = new URLSearchParams(window.location.search);
        const query = params.get("query")?.trim() || "";
        const view = params.get("view") || "overview";
        if (view && elements.views[view]) {
            context.setActiveView(view);
        }
        if (!query) {
            renderAll();
            return;
        }
        elements.searchInput.value = query;
        state.query = query;
        await listings.search(view);
    }

    // ── Expenses ──────────────────────────────────────────────────────────
    async function loadExpenses(leadId) {
        try {
            state.expenses = await core.getJson(`/api/v1/leads/${leadId}/expenses`);
            renderExpensesModal();
        } catch (_) {
            state.expenses = [];
            renderExpensesModal();
        }
    }

    async function createExpense(leadId, payload) {
        try {
            await core.postJson(`/api/v1/leads/${leadId}/expenses`, payload);
            showToast("Расход добавлен");
            await loadExpenses(leadId);
        } catch (error) {
            showToast(error.message || "Не удалось добавить расход");
        }
    }

    async function deleteExpense(leadId, expenseId) {
        try {
            await core.deleteJson(`/api/v1/leads/${leadId}/expenses/${expenseId}`);
            showToast("Расход удалён");
            await loadExpenses(leadId);
        } catch (error) {
            showToast(error.message || "Не удалось удалить расход");
        }
    }

    // ── CSV Export ────────────────────────────────────────────────────────
    async function exportLeadsCSV() {
        try {
            const initData = window.Telegram?.WebApp?.initData;
            const headers = initData ? { "X-Telegram-Init-Data": initData } : {};
            const response = await fetch("/api/v1/leads/export?format=csv", {
                headers,
            });
            if (!response.ok) {
                throw new Error("Не удалось экспортировать данные");
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "leads_export.csv";
            a.click();
            URL.revokeObjectURL(url);
            showToast("Файл загружен");
        } catch (error) {
            showToast(error.message || "Не удалось экспортировать");
        }
    }

    // ── Public API (every name the original file exported) ───────────────

    return {
        bindEvents: events.bindEvents,
        search: listings.search,
        loadListings: listings.loadListings,
        loadDeals: listings.loadDeals,
        loadRates: core.loadRates,
        loadHistory: listings.loadHistory,
        loadComparison: listings.loadComparison,
        swapComparisonQueries: listings.swapComparisonQueries,
        loadTrackers: trackers.loadTrackers,
        loadLeads: leads.loadLeads,
        clearAllLeads: leads.clearAllLeads,
        deleteLead: leads.deleteLead,
        confirmLead: leads.confirmLead,
        cancelLead: leads.cancelLead,
        closeDeal: leads.closeDeal,
        deleteHistoryDeal,
        revertLeadStage: leads.revertLeadStage,
        markLeadAsSold: leads.markLeadAsSold,
        openLeadDetail: leads.openLeadDetail,
        loadWatchlist: watchlist.loadWatchlist,
        createTracker: trackers.createTracker,
        addLeadFromListing,
        addWatchlistFromListing: watchlist.addWatchlistFromListing,
        updateLeadStatus: leads.updateLeadStatus,
        updateLeadMeta: leads.updateLeadMeta,
        updateWatchlistStatus: watchlist.updateWatchlistStatus,
        updateWatchlistMeta: watchlist.updateWatchlistMeta,
        promoteWatchlistToLead: watchlist.promoteWatchlistToLead,
        deleteWatchlistItem: watchlist.deleteWatchlistItem,
        deleteAllWatchlist: watchlist.deleteAllWatchlist,
        refreshWatchlist: watchlist.refreshWatchlist,
        refreshLeads,
        openWatchlistDetail: watchlist.openWatchlistDetail,
        deleteTracker: trackers.deleteTracker,
        pauseTracker: trackers.pauseTracker,
        resumeTracker: trackers.resumeTracker,
        openEditTracker: trackers.openEditTracker,
        closeEditTracker: trackers.closeEditTracker,
        saveTracker: trackers.saveTracker,
        openOpportunityQuery,
        openOpportunityDetail,
        openListingDetail: listings.openListingDetail,
        setCurrency,
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeadsCSV,
        loadDetailRisks: listings.loadDetailRisks,
    };
}
