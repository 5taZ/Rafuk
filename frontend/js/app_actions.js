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
        renderMonitoringHeroStats,
        renderDealsHeroStats,
    } = context;

    // ── Internal helpers (originally inside the monolithic app_actions) ───
    // These are not part of any module because they bridge multiple modules
    // and the view/scroll lifecycle.

    /**
     * Switches the active view with a subtle enter animation.
     * Updates tab states, triggers view transition, and re-renders view content.
     * Respects prefers-reduced-motion for accessibility.
     *
     * @param {string} view - The view key to activate (e.g., 'overview', 'ads', 'tracking')
     */
    function setActiveView(view) {
        if (!(view in elements.views)) {
            return;
        }
        // No-op when the view is already active. Otherwise an in-page
        // action that happens to re-call setActiveView (e.g. picking a
        // discount preset inside the Объявления view) would run a full
        // renderAll + replay the slide-in animation, making the page
        // look like it's reloading.
        if (state.activeView === view) {
            return;
        }
        state.activeView = view;
        markDirty('tabs', 'views');
        renderAll();

        const viewEl = elements.views[view];
        if (viewEl) {
            const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (!prefersReducedMotion) {
                viewEl.classList.add("is-entering");
                setTimeout(() => {
                    viewEl.classList.remove("is-entering");
                }, 200);
            }
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
     * "cheap" used to be its own view tab; it's now folded into "ads"
     * with state.sort = "cheap" surfacing the discount-range UI.
     *
     * @param {string} target - The target context ('ads', 'cheap', 'deals', 'history')
     */
    function focusTarget(target) {
        if (target === "ads" || target === "cheap") {
            if (target === "cheap") {
                state.sort = "cheap";
            }
            setActiveView("ads");
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
    // Expose leads.loadLeads on context so watchlist actions can refresh
    // both surfaces atomically when promoting a watching item to a lead.
    context.loadLeads = leads.loadLeads;
    const watchlist = createApiWatchlist(context);
    const ai = createApiAi(context);
    const listingAssistant = (typeof createApiListingAssistant === "function")
        ? createApiListingAssistant(context)
        : null;
    void listingAssistant;

    // ── Cross-module hooks (actions that modules call into each other) ───
    // These are injected into context so every module can reach them.
    Object.assign(context, {
        // From listings
        search: listings.search,
        loadListings: listings.loadListings,
        loadMoreListings: listings.loadMoreListings,
        loadDeals: listings.loadDeals,
        loadMoreDeals: listings.loadMoreDeals,
        openListingDetail: listings.openListingDetail,
        loadSearchDependencies: listings.loadSearchDependencies,
        clearSearchData: listings.clearSearchData,
        loadHistory: listings.loadHistory,

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
        loadAnalytics: leads.loadAnalytics,
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

        // From AI
        loadAIAnalysis: ai.loadAIAnalysis,
        closeAIModal: ai.closeAIModal,
    });
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
    // Tracks ad_ids with an in-flight watchlist↔leads transition so a
    // double-click on "В покупки" / "В избранное" doesn't fire a second
    // request (the first hasn't reloaded state.leads yet so the
    // alreadyInLeads guard sees the old empty list).
    const _inflightAdMutations = new Set();

    async function addLeadFromListing(item, source = "manual", queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }
        if (_inflightAdMutations.has(item.ad_id)) {
            // A previous click for this ad is still mid-flight — ignore.
            return;
        }

        // Closed/skipped leads stay in history but should NOT block a
        // re-add — that's how a returning customer or a re-buy reaches
        // the funnel. Only an actively-tracked lead counts as duplicate.
        const ACTIVE_LEAD_STATUSES = new Set([
            "new",
            "in_progress",
            "researching",
            "bought",
            "sold",
        ]);
        const alreadyInLeads = state.leads.some(
            (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
        );
        if (alreadyInLeads) {
            showToast("Уже в покупках");
            return;
        }

        // If the ad is currently in the watchlist (status='watching'),
        // promotion is a single PATCH on the same lead_items row — no
        // need for a fresh POST. This also avoids race-condition 500s
        // when the user rapid-fires "В избранное" then "В покупки".
        const watchingItem = state.watchlist.find((w) => w.ad_id === item.ad_id);

        _inflightAdMutations.add(item.ad_id);
        try {
            if (watchingItem) {
                await core.requestJson(`/api/v1/leads/${watchingItem.id}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ status: "new" }),
                });
                // Optimistic: remove from watchlist immediately.
                state.watchlist = state.watchlist.filter((w) => w.id !== watchingItem.id);
            } else {
                const marketEstimate = (item.flip_estimates || []).find(
                    (entry) => entry.label === "По рынку",
                );
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
            }
            showToast("Добавлено в покупки", "success");
            // Refresh both surfaces so a once-watched item disappears
            // from "Избранное" and shows up in "Покупки" together.
            await Promise.all([leads.loadLeads(), watchlist.loadWatchlist()]);
        } catch (error) {
            showToast(error.message || "Не удалось добавить в покупки", "error");
        } finally {
            _inflightAdMutations.delete(item.ad_id);
        }
    }

    // Make addLeadFromListing available on context for cross-module calls
    context.addLeadFromListing = addLeadFromListing;

    // ── Additional actions not in any module ──────────────────────────────

    async function deleteHistoryDeal(leadId) {
        try {
            await core.deleteJson(`/api/v1/leads/${leadId}`);
            state.leads = state.leads.filter((l) => l.id !== leadId);
            renderLeads();
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
        state.expensesLoading = true;
        renderExpensesModal();
        try {
            state.expenses = await core.getJson(`/api/v1/leads/${leadId}/expenses`);
        } catch (_) {
            state.expenses = [];
        } finally {
            state.expensesLoading = false;
        }
        renderExpensesModal();
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

    // ── Leads export ─────────────────────────────────────────────────────
    /**
     * Download the user's leads as a file. `format` is "csv" (default)
     * or "xlsx" — the backend renders the same row schema in either
     * shape, with proper Excel number formatting on .xlsx.
     */
    async function exportLeads(format = "csv") {
        const fmt = format === "xlsx" ? "xlsx" : "csv";
        try {
            const initData = window.Telegram?.WebApp?.initData;
            const headers = initData ? { "X-Telegram-Init-Data": initData } : {};
            const response = await fetch(`/api/v1/leads/export?format=${fmt}`, {
                headers,
            });
            if (!response.ok) {
                throw new Error("Не удалось экспортировать данные");
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = fmt === "xlsx" ? "leads_export.xlsx" : "leads_export.csv";
            a.click();
            URL.revokeObjectURL(url);
            showToast("Файл загружен");
        } catch (error) {
            showToast(error.message || "Не удалось экспортировать");
        }
    }
    // Backwards-compat name still used by api_events context
    // destructure list and any external bindings.
    const exportLeadsCSV = () => exportLeads("csv");
    const exportLeadsXLSX = () => exportLeads("xlsx");

    // ── Public API (every name the original file exported) ───────────────

    return {
        bindEvents: events.bindEvents,
        search: listings.search,
        loadListings: listings.loadListings,
        loadMoreListings: listings.loadMoreListings,
        loadDeals: listings.loadDeals,
        loadMoreDeals: listings.loadMoreDeals,
        loadHistory: listings.loadHistory,
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
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeads,
        exportLeadsCSV,
        exportLeadsXLSX,
        loadAIAnalysis: ai.loadAIAnalysis,
        closeAIModal: ai.closeAIModal,
    };
}
