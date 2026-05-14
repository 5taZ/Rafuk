/**
 * app_actions.js — Composition entry point for all action / API modules.
 *
 * Delegates to focused modules: api_core, api_listings, api_trackers,
 * api_leads, api_watchlist, api_events.
 *
 * Every function that the original monolithic file exported is still available
 * on the returned object so app.js and renderers continue to work without changes.
 */

function createAppActions(baseContext) {
    const context = { ...baseContext };
    // OPUS-13 wave 74: api_trackers / api_leads / api_watchlist ship as
    // lazy <script>s after waves 70-71 and no longer share the bundle
    // IIFE scope. dom_helpers / dom_helpers-like callables that the
    // bundle keeps as free functions (``domEl``, ``openModalAnimated``,
    // ``closeModalAnimated``) must ride through context so lazy api
    // factories can destructure them instead of triggering a
    // ReferenceError when a modal opens.
    context.domEl = domEl;
    context.domClear = domClear;
    context.domFragment = domFragment;
    context.openModalAnimated = openModalAnimated;
    context.closeModalAnimated = closeModalAnimated;
    context.bindRovingTablist = bindRovingTablist;
    context._prefersReducedMotion = _prefersReducedMotion;
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
        if (state.ui.activeView === view) {
            return;
        }
        state.ui.activeView = view;
        markDirty('tabs', 'views');
        renderAll();

        const viewEl = elements.views[view];
        if (viewEl) {
            const prefersReducedMotion = _prefersReducedMotion();
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
        section?.scrollIntoView({
            behavior: _prefersReducedMotion() ? "auto" : "smooth",
            block: "start",
        });
    }

    /**
     * Focuses the appropriate view or panel based on the search target.
     * Routes to overview by default, then scrolls to specific sections if needed.
     *
     * "cheap" used to be its own view tab; it's now folded into "ads"
     * with state.search.sort = "cheap" surfacing the discount-range UI.
     *
     * @param {string} target - The target context ('ads', 'cheap', 'deals', 'history')
     */
    function focusTarget(target) {
        if (target === "ads" || target === "cheap") {
            if (target === "cheap") {
                state.search.sort = "cheap";
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

    const {
        fetchProxyImageObjectUrl,
        clearProxyImageObjectUrls,
    } = createImageProxyLoader(core);

    const listings = createApiListings(context);
    const trackers = createApiTrackers(context);
    const leads = createApiLeads(context);
    // Expose leads.loadLeads on context so watchlist actions can refresh
    // both surfaces atomically when promoting a watching item to a lead.
    context.loadLeads = leads.loadLeads;
    const watchlist = createApiWatchlist(context);
    let _aiModule = null;
    let _listingAssistantModule = null;

    async function ensureAiLoaded() {
        if (_aiModule) return;
        // UX-M8 (Wave 25): api_ai.js was split by responsibility. Load
        // the sub-modules (modal/render) BEFORE the orchestrator
        // (api_ai.js) so their App namespace registrations are ready
        // by the time createApiAi calls them. They're loaded in
        // parallel and share the same cache-busting version stamp.
        await Promise.all([
            context._loadScript("js/api_ai_modal.js?v=20260514-db67f8d"),
            context._loadScript("js/api_ai_render.js?v=20260514-db67f8d"),
            context._loadScript("js/api_ai.js?v=20260514-db67f8d"),
            context._loadScript("js/api_listing_assistant.js?v=20260514-db67f8d"),
        ]);
        const app = window.App || {};
        if (typeof app.createApiAi !== "function") {
            throw new Error("AI module failed to register");
        }
        _aiModule = app.createApiAi(context);
        _listingAssistantModule = (typeof app.createApiListingAssistant === "function")
            ? app.createApiListingAssistant(context)
            : null;
    }

    async function loadAIAnalysis(...args) {
        await ensureAiLoaded();
        return _aiModule.loadAIAnalysis(...args);
    }

    function closeAIModal() {
        if (_aiModule) _aiModule.closeAIModal();
    }

    // ── Cross-module hooks (actions that modules call into each other) ───
    // These are injected into context so every module can reach them.
    Object.assign(context, {
        fetchProxyImageObjectUrl,
        clearProxyImageObjectUrls,

        // From listings
        search: listings.search,
        loadListings: listings.loadListings,
        loadMoreListings: listings.loadMoreListings,
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
        loadAIAnalysis,
        closeAIModal,
        ensureAiLoaded,
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

    function _guardAdMutation(adId) {
        _inflightAdMutations.add(adId);
        // FE-M14: shared INFLIGHT_GUARD_MS from dom_helpers.js — same
        // window as api_watchlist's per-row dedupe so a click in one
        // surface and a click in the other can't race past each other.
        setTimeout(() => _inflightAdMutations.delete(adId), INFLIGHT_GUARD_MS);
    }

    function _validVersion(value) {
        const numeric = Number(value);
        return Number.isInteger(numeric) && numeric >= 1 ? numeric : null;
    }

    function _findWatchingSnapshot(item) {
        return state.watchlist.items.find(
            (w) => w.id === item?.id || w.ad_id === item?.ad_id,
        ) || null;
    }

    async function _resolveWatchingVersion(item) {
        const current = _findWatchingSnapshot(item) || item;
        const version = _validVersion(current?.version);
        if (version) return version;
        await watchlist.loadWatchlist();
        return _validVersion(_findWatchingSnapshot(item)?.version);
    }

    async function _resolveWatchingItem(item) {
        const current = _findWatchingSnapshot(item);
        if (current) return current;
        await watchlist.loadWatchlist();
        return _findWatchingSnapshot(item);
    }

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
        const alreadyInLeads = state.leads.items.some(
            (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
        );
        if (alreadyInLeads) {
            showToast("Уже в покупках", "info", 1600);
            return;
        }

        // If the ad is currently in the watchlist (status='watching'),
        // promotion is a single PATCH on the same lead_items row — no
        // need for a fresh POST. This also avoids race-condition 500s
        // when the user rapid-fires "В избранное" then "В покупки".
        let watchingItem = _findWatchingSnapshot(item);

        _guardAdMutation(item.ad_id);
        try {
            if (!watchingItem && source === "detail_modal" && state.detail.fromWatchlist) {
                watchingItem = await _resolveWatchingItem(item);
            }
            if (watchingItem) {
                const version = await _resolveWatchingVersion(watchingItem);
                if (!version) {
                    showToast("Данные устарели. Обновите список и попробуйте ещё раз.", "error");
                    return;
                }
                await core.requestJson(`/api/v1/leads/${watchingItem.id}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        status: "new",
                        version,
                    }),
                });
                // Optimistic: remove from watchlist immediately.
                state.watchlist.items = state.watchlist.items.filter((w) => w.id !== watchingItem.id);
            } else {
                const marketEstimate = (item.flip_estimates || []).find(
                    (entry) => entry.label === "По рынку",
                );
                await core.postJson("/api/v1/leads", {
                    query: queryOverride || state.search.query || "",
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
            showToast("В покупках", "success", 1600);
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
            state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
            renderLeads();
            showToast("Сделка удалена из истории");
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

    /**
     * Parses URL launch parameters (?query=...&view=...) and initializes the app state.
     * Called on app startup to handle deep linking.
     *
     * @returns {Promise<void>}
     */
    async function applyLaunchParams() {
        const params = new URLSearchParams(window.location.search);
        // Support deep linking via start_param (from bot /start tracking)
        // Can come from either URL param or Telegram initDataUnsafe
        const startParam = params.get("start_param") || window.Telegram?.WebApp?.initDataUnsafe?.start_param;
        if (startParam && !params.has("view")) {
            const viewMap = { tracking: "tracking", trackers: "tracking", deals: "deals", monitoring: "monitoring" };
            const mappedView = viewMap[startParam] || startParam;
            if (elements.views[mappedView]) {
                params.set("view", mappedView);
            }
        }
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
        state.search.query = query;
        await listings.search(view);
    }

    // ── Expenses ──────────────────────────────────────────────────────────
    async function loadExpenses(leadId) {
        state.expenses.loading = true;
        renderExpensesModal();
        try {
            state.expenses.items = await core.getJson(`/api/v1/leads/${leadId}/expenses`);
        } catch (_) {
            state.expenses.items = [];
        } finally {
            state.expenses.loading = false;
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
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120_000);
        try {
            const initData = window.Telegram?.WebApp?.initData;
            const headers = initData ? { "X-Telegram-Init-Data": initData } : {};
            const response = await fetch(`/api/v1/leads/export?format=${fmt}`, {
                headers,
                signal: controller.signal,
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
            // FE-07: defer revoke so the browser actually has time to
            // start the download. On slow Android WebViews / weak
            // CPUs ``a.click()`` queues the navigation but doesn't
            // commit it before the next microtask tick — revoking
            // immediately after caused empty downloads in practice.
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            showToast("Файл загружен");
        } catch (error) {
            if (error.name === "AbortError") {
                showToast("Экспорт занимает слишком долго, попробуйте позже");
            } else {
                showToast(error.message || "Не удалось экспортировать");
            }
        } finally {
            clearTimeout(timeoutId);
        }
    }
    // Backwards-compat name still used by api_events context
    // destructure list and any external bindings.
    const exportLeadsCSV = () => exportLeads("csv");
    const exportLeadsXLSX = () => exportLeads("xlsx");

    // ── Public API (every name the original file exported) ───────────────

    // ── Consent & Privacy ──────────────────────────────────────────────────

    function isLocalDebugContext() {
        const loc = window.location || {};
        const host = String(loc.hostname || "");
        return loc.protocol === "file:" ||
            host === "localhost" ||
            host === "127.0.0.1" ||
            host === "::1" ||
            host.endsWith(".localhost");
    }

    function clearLocalAccountData() {
        if (state.search) state.search.recentSearches = [];
        if (state.misc) state.misc.listingAssistantResult = null;
        clearProxyImageObjectUrls();
        try {
            const keys = ["recentSearches"];
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith("rafuk:")) keys.push(key);
            }
            for (const key of new Set(keys)) localStorage.removeItem(key);
        } catch (_) {}
    }

    /**
     * Check if user has granted processing/AI consent. Returns true if consent exists.
     * If not, shows the consent modal and returns a Promise that resolves
     * when the user grants consent (or rejects on cancel).
     */
    async function checkAiConsent() {
        try {
            const statuses = await Promise.all([
                core.getJson("/api/v1/account/consent/ai_analysis"),
                core.getJson("/api/v1/account/consent/cross_border"),
                core.getJson("/api/v1/account/consent/pd_processing"),
            ]);
            if (statuses.every((status) => status.granted)) return true;
        } catch (_) {
            if (isLocalDebugContext()) return true;
            showToast("Не удалось проверить согласие на AI. Проверьте соединение и попробуйте ещё раз.", "error", 3600);
            throw new Error("consent_check_failed");
        }
        // Show consent modal
        return new Promise((resolve, reject) => {
            _showConsentModal(resolve, reject);
        });
    }

    function _showConsentModal(resolve, reject) {
        const modal = document.getElementById("consent-modal");
        const aiCb = document.getElementById("consent-ai-checkbox");
        const crossCb = document.getElementById("consent-cross-border-checkbox");
        const pdCb = document.getElementById("consent-pd-checkbox");
        const acceptBtn = document.getElementById("consent-accept-btn");
        const cancelBtn = document.getElementById("consent-cancel-btn");
        const privacyLink = document.getElementById("consent-privacy-link");

        if (!modal) { resolve(true); return; }

        // Reset checkboxes
        aiCb.checked = false;
        crossCb.checked = false;
        pdCb.checked = false;
        acceptBtn.disabled = true;
        modal.hidden = false;
        // FE-H4/UX-H1: install a focus trap when the modal opens so a
        // keyboard user can't Tab out of the consent dialog and
        // interact with the app underneath (which has its own shortcuts
        // and scroll). trapFocus returns a cleanup that restores focus
        // to whatever had it before the modal; we call it from
        // cleanup() on every exit path.
        let focusCleanup = null;
        if (typeof trapFocus === "function") focusCleanup = trapFocus(modal);
        // UX-M2: also hide everything else from assistive tech for
        // the duration of the consent gate.
        if (typeof _applyInertToSiblings === "function") _applyInertToSiblings(modal);

        function updateAcceptBtn() {
            acceptBtn.disabled = !(aiCb.checked && crossCb.checked && pdCb.checked);
        }

        // AbortController to clean up checkbox listeners at once
        const ac = new AbortController();
        const opts = { signal: ac.signal };
        aiCb.addEventListener("change", updateAcceptBtn, opts);
        crossCb.addEventListener("change", updateAcceptBtn, opts);
        pdCb.addEventListener("change", updateAcceptBtn, opts);

        async function onAccept() {
            try {
                await core.postJson("/api/v1/account/consent", { consent_type: "ai_analysis", version: "2026.2" });
                await core.postJson("/api/v1/account/consent", { consent_type: "cross_border", version: "2026.2" });
                await core.postJson("/api/v1/account/consent", { consent_type: "pd_processing", version: "2026.2" });
            } catch (err) {
                showToast(err.message || "Не удалось сохранить согласие");
                return;
            }
            cleanup();
            modal.hidden = true;
            resolve(true);
        }

        function onCancel() {
            cleanup();
            modal.hidden = true;
            // FE-08: Cancel used to be a silent close — the user
            // would tap "Отмена" and nothing visible happened, then
            // every AI button quietly no-op'd because the rejection
            // bubbles back as a swallowed `consent_denied`. Emit a
            // toast so the dismissal is acknowledged and the user
            // knows AI features stay locked until they reopen the
            // gate (any AI action will re-show this modal).
            showToast("AI-функции отключены — требуется согласие на обработку данных", "info", 3200);
            reject(new Error("consent_denied"));
        }

        function onPrivacyLink(e) {
            e.preventDefault();
            openPrivacyModal();
        }

        // FE-02: close the consent gate on Escape so keyboard users
        // aren't trapped. The global Escape handler in api_events.js
        // doesn't know about this modal (it pre-dates the consent
        // flow), and the focus trap installed above keeps Tab inside
        // the modal — without an Escape exit there is literally no
        // keyboard-only way out except submitting the form. Closing
        // is equivalent to clicking Cancel (rejects the consent
        // Promise) so the call site treats it as denial. The privacy
        // modal is allowed to absorb Escape first when it's open on
        // top, otherwise we'd close both modals together.
        function onKeydown(e) {
            if (e.key !== "Escape") return;
            // If the privacy modal is open ON TOP, let it handle the
            // Escape first.
            const privacyModal = document.getElementById("privacy-modal");
            if (privacyModal && !privacyModal.hidden) return;
            onCancel();
        }

        function cleanup() {
            ac.abort(); // removes all checkbox change listeners
            acceptBtn.removeEventListener("click", onAccept);
            cancelBtn.removeEventListener("click", onCancel);
            if (privacyLink) privacyLink.removeEventListener("click", onPrivacyLink);
            document.removeEventListener("keydown", onKeydown);
            if (typeof focusCleanup === "function") focusCleanup();
            // UX-M2: restore inert AFTER the focus trap cleanup so the
            // restored focus target isn't itself sitting in an inert
            // subtree.
            if (typeof _restoreInertSiblings === "function") _restoreInertSiblings(modal);
        }

        acceptBtn.addEventListener("click", onAccept);
        cancelBtn.addEventListener("click", onCancel);
        if (privacyLink) privacyLink.addEventListener("click", onPrivacyLink);
        document.addEventListener("keydown", onKeydown);
    }

    function openPrivacyModal() {
        const modal = document.getElementById("privacy-modal");
        const overlay = document.getElementById("privacy-overlay");
        const closeBtn = document.getElementById("privacy-modal-close");
        if (!modal) return;
        modal.hidden = false;
        document.body.classList.add("modal-open");
        let focusCleanup = null;
        if (typeof trapFocus === "function") focusCleanup = trapFocus(modal);
        // UX-M2: hide rest of the app from assistive tech while the
        // privacy text is open. The consent modal is usually open
        // underneath this one — the second-call guard inside
        // _applyInertToSiblings (skip already-inert siblings) makes
        // sure we don't double-stamp and mis-restore.
        if (typeof _applyInertToSiblings === "function") _applyInertToSiblings(modal);

        function close() {
            modal.hidden = true;
            document.body.classList.remove("modal-open");
            if (typeof focusCleanup === "function") focusCleanup();
            if (typeof _restoreInertSiblings === "function") _restoreInertSiblings(modal);
            closeBtn?.removeEventListener("click", close);
            overlay?.removeEventListener("click", close);
            document.removeEventListener("keydown", onKeydown);
        }
        // FE-02: Escape closes the privacy modal too — it's a
        // read-only secondary modal on top of the consent gate.
        function onKeydown(e) {
            if (e.key === "Escape") close();
        }
        closeBtn?.addEventListener("click", close);
        overlay?.addEventListener("click", close);
        document.addEventListener("keydown", onKeydown);
    }

    async function deleteAccount() {
        // BE-M3: irreversible action — require the user to type their
        // Telegram first_name (the same one shown in the bot header) to
        // confirm. window.confirm() is unreliable in Telegram WebView,
        // so we use a custom typed-input dialog. Server validates the
        // confirmation independently — this modal is UX, not the
        // security boundary.
        const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user || {};
        const firstName = (tgUser.first_name || "").trim();
        const fallbackId = String(tgUser.id || "");
        // Display the most recognisable identifier — first_name when
        // present, otherwise the numeric id (server accepts either).
        const expected = firstName || fallbackId;
        if (!expected) {
            showToast("Не удалось определить пользователя");
            return;
        }
        const typed = await _showTypedConfirmDialog(
            "Удалить аккаунт?",
            "Все ваши данные будут безвозвратно удалены. Это действие нельзя отменить.",
            expected,
        );
        if (typed === null) return;
        try {
            await core.deleteJson("/api/v1/account", { confirmation: typed });
            clearLocalAccountData();
            showToast("Аккаунт удалён. Данные стёрты.");
            setTimeout(() => window.location.reload(), 1500);
        } catch (err) {
            showToast(err.message || "Не удалось удалить аккаунт");
        }
    }

    function _showTypedConfirmDialog(title, message, expectedText) {
        // BE-M3: typed-input variant of the confirm dialog. Resolves to
        // the entered text on confirm, or ``null`` on cancel. The input
        // has to *exactly* match ``expectedText`` (case-insensitive,
        // stripped) before the confirm button activates — clients that
        // skip this check still hit the server-side validator.
        return new Promise((resolve) => {
            const overlay = document.createElement("div");
            overlay.className = "detail-modal typed-confirm-modal";
            const sheet = document.createElement("div");
            sheet.className = "typed-confirm-sheet";

            const h3 = document.createElement("h3");
            h3.className = "typed-confirm-title";
            h3.textContent = title;

            const p = document.createElement("p");
            p.className = "typed-confirm-message";
            p.textContent = message;

            const hint = document.createElement("p");
            hint.className = "typed-confirm-hint";
            // Build via DOM (not innerHTML) — keeps user-controlled
            // ``expectedText`` away from the HTML parser entirely.
            hint.append("Введите ");
            const expectedSpan = document.createElement("strong");
            expectedSpan.className = "typed-confirm-expected";
            expectedSpan.textContent = expectedText;
            hint.append(expectedSpan, " для подтверждения:");

            const input = document.createElement("input");
            input.type = "text";
            input.autocomplete = "off";
            input.autocapitalize = "off";
            input.spellcheck = false;
            input.className = "typed-confirm-input";

            const btnRow = document.createElement("div");
            btnRow.className = "typed-confirm-actions";

            const cancelBtn = document.createElement("button");
            cancelBtn.setAttribute("data-role", "cancel");
            cancelBtn.className = "typed-confirm-btn typed-confirm-btn--secondary";
            cancelBtn.textContent = "Отмена";

            const confirmBtn = document.createElement("button");
            confirmBtn.setAttribute("data-role", "confirm");
            confirmBtn.className = "typed-confirm-btn typed-confirm-btn--danger";
            confirmBtn.textContent = "Удалить";
            confirmBtn.disabled = true;

            btnRow.append(cancelBtn, confirmBtn);
            sheet.append(h3, p, hint, input, btnRow);
            overlay.appendChild(sheet);
            document.body.appendChild(overlay);
            document.body.classList.add("modal-open");
            let focusCleanup = null;
            if (typeof trapFocus === "function") focusCleanup = trapFocus(sheet);
            // UX-M2: hide the rest of the page from assistive tech
            // while the typed-delete-confirm dialog is open. The
            // overlay was just appended to body so it's a body
            // child by the time we apply.
            if (typeof _applyInertToSiblings === "function") _applyInertToSiblings(overlay);
            // Focus the input so the user can start typing immediately —
            // a typed-confirm dialog where you have to click into the
            // box first is hostile UX.
            setTimeout(() => input.focus(), 0);

            const expectedNorm = expectedText.trim().toLowerCase();
            function refreshConfirm() {
                const matches = input.value.trim().toLowerCase() === expectedNorm;
                confirmBtn.disabled = !matches;
            }
            input.addEventListener("input", refreshConfirm);
            input.addEventListener("keydown", (e) => {
                if (e.key === "Enter" && !confirmBtn.disabled) close(input.value);
            });

            function close(result) {
                document.body.classList.remove("modal-open");
                if (typeof focusCleanup === "function") focusCleanup();
                // UX-M2: restore inert siblings BEFORE removing the
                // overlay — once it's gone, ``_inertSiblings`` is
                // unreachable and the page would stay frozen.
                if (typeof _restoreInertSiblings === "function") _restoreInertSiblings(overlay);
                overlay.remove();
                resolve(result);
            }

            cancelBtn.addEventListener("click", () => close(null));
            confirmBtn.addEventListener("click", () => {
                if (!confirmBtn.disabled) close(input.value);
            });
            overlay.addEventListener("click", (e) => {
                if (e.target === overlay) close(null);
            });
        });
    }


    async function exportAccountData() {
        try {
            const resp = await fetch("/api/v1/account/export", {
                headers: core.telegramHeaders(),
            });
            if (!resp.ok) throw new Error("Экспорт не удался");
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "rafuks_data_export.json";
            a.click();
            // FE-07: see note above the CSV/XLSX revoke — same fix.
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            showToast("Данные экспортированы");
        } catch (err) {
            showToast(err.message || "Не удалось экспортировать данные");
        }
    }

    // Expose consent function on context so api_ai.js can call it
    context.checkAiConsent = checkAiConsent;
    context.openPrivacyModal = openPrivacyModal;
    context.ensureAiLoaded = ensureAiLoaded;

    return {
        bindEvents: events.bindEvents,
        search: listings.search,
        loadListings: listings.loadListings,
        loadMoreListings: listings.loadMoreListings,
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
        openListingDetail: listings.openListingDetail,
        // FE-C4: expose abort hooks so closeDetailModal and any
        // future cleanup paths (view tab change, route navigation)
        // can drop pending fetches before clearing UI state.
        abortDetailRequest: listings.abortDetailRequest,
        abortHistoryRequest: listings.abortHistoryRequest,
        abortSearchRequests: listings.abortSearchRequests,
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeads,
        exportLeadsCSV,
        exportLeadsXLSX,
        loadAIAnalysis,
        closeAIModal,
        checkAiConsent,
        openPrivacyModal,
        deleteAccount,
        exportAccountData,
        fetchProxyImageObjectUrl,
        clearProxyImageObjectUrls,
    };
}
