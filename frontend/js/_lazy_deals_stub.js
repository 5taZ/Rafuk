/**
 * _lazy_deals_stub.js — OPUS-13 lazy-load shim for the
 * "Мои объявления" view trio: api_leads.js + api_watchlist.js +
 * render_modals.js.
 *
 * Same shape as _lazy_trackers_stub.js — see that file's header
 * for the design rationale. The three real modules load in
 * parallel through one shared promise on
 * ``_loadPromises`` Map keyed by "deals" so any of the api stubs can
 * trigger the bundle download without racing each other.
 *
 * Why bundle these three together: the user almost never opens
 * one without the others. Tapping a lead opens a modal
 * (render_modals.js), saving an expense calls api_leads.js,
 * promoting a watchlist row to a lead crosses both api modules.
 * Co-loading keeps perceived latency low — one network round-
 * trip instead of three serial ones.
 */
/* global window */

const _LAZY_DEALS_API_LEADS_URL = "js/api_leads.js?v=20261025-2832914";
const _LAZY_DEALS_API_WATCHLIST_URL = "js/api_watchlist.js?v=20261025-2832914";
const _LAZY_DEALS_RENDER_MODALS_URL = "js/render_modals.js?v=20261025-2832914";

function _lazyLoadDealsSources(context) {
    if (_loadPromises.has("deals")) return _loadPromises.get("deals");
    const p = Promise.all([
        context._loadScript(_LAZY_DEALS_API_LEADS_URL),
        context._loadScript(_LAZY_DEALS_API_WATCHLIST_URL),
        context._loadScript(_LAZY_DEALS_RENDER_MODALS_URL),
    ]).catch((err) => {
        _loadPromises.delete("deals");
        throw err;
    });
    _loadPromises.set("deals", p);
    return p;
}

// RV-FE-02: the list loaders surface a missing chunk the same way as a
// failed request (RV-FE-05 error state with "Повторить" in the list);
// background refreshes stay quiet; explicit user actions get a toast.
function _dealsChunkFailure(context, collection, loadMethod, lazy) {
    return (method) => {
        const state = context.state;
        if (method === loadMethod && state?.[collection]) {
            state[collection].error = lazy.needsRestart() ? LAZY_RESTART_MESSAGE : LAZY_LOAD_FAILED_MESSAGE;
            state[collection]._loading = false;
            if (typeof context.renderLeads === "function") context.renderLeads();
            return undefined;
        }
        if (method !== "loadAnalytics") _showLazyLoadToast(context);
        return undefined;
    };
}

function createApiLeads(context) {
    // RV-FE-02: see _createLazyModule (app_core.js).
    const lazy = _createLazyModule(context, {
        load: _lazyLoadDealsSources,
        factoryKey: "_realCreateApiLeads",
        label: "api_leads.js",
    });
    const onFailure = _dealsChunkFailure(context, "leads", "loadLeads", lazy);

    function _delegate(method, args) {
        return _lazyApiCall(lazy, context, "deals", method, args, onFailure);
    }

    return {
        loadLeads: (...args) => _delegate("loadLeads", args),
        loadAnalytics: (...args) => _delegate("loadAnalytics", args),
        clearAllLeads: (...args) => _delegate("clearAllLeads", args),
        confirmLead: (...args) => _delegate("confirmLead", args),
        cancelLead: (...args) => _delegate("cancelLead", args),
        closeDeal: (...args) => _delegate("closeDeal", args),
        revertLeadStage: (...args) => _delegate("revertLeadStage", args),
        deleteLead: (...args) => _delegate("deleteLead", args),
        updateLeadMeta: (...args) => _delegate("updateLeadMeta", args),
        updateLeadStatus: (...args) => _delegate("updateLeadStatus", args),
        markLeadAsSold: (...args) => _delegate("markLeadAsSold", args),
        openLeadDetail: (...args) => _delegate("openLeadDetail", args),
        _ensureLoaded: lazy.ensure,
    };
}

function createApiWatchlist(context) {
    // RV-FE-02: see _createLazyModule (app_core.js).
    const lazy = _createLazyModule(context, {
        load: _lazyLoadDealsSources,
        factoryKey: "_realCreateApiWatchlist",
        label: "api_watchlist.js",
    });
    const onFailure = _dealsChunkFailure(context, "watchlist", "loadWatchlist", lazy);

    function _delegate(method, args) {
        return _lazyApiCall(lazy, context, "deals", method, args, onFailure);
    }

    return {
        loadWatchlist: (...args) => _delegate("loadWatchlist", args),
        clearAllWatchlist: (...args) => _delegate("clearAllWatchlist", args),
        addWatchlistFromListing: (...args) => _delegate("addWatchlistFromListing", args),
        updateWatchlistMeta: (...args) => _delegate("updateWatchlistMeta", args),
        updateWatchlistStatus: (...args) => _delegate("updateWatchlistStatus", args),
        promoteWatchlistToLead: (...args) => _delegate("promoteWatchlistToLead", args),
        openWatchlistDetail: (...args) => _delegate("openWatchlistDetail", args),
        deleteWatchlistItem: (...args) => _delegate("deleteWatchlistItem", args),
        deleteAllWatchlist: (...args) => _delegate("deleteAllWatchlist", args),
        refreshWatchlist: (...args) => _delegate("refreshWatchlist", args),
        _ensureLoaded: lazy.ensure,
    };
}

function createRenderModals(context) {
    // RV-FE-02: see _createLazyModule (app_core.js). Every entry point
    // here answers a tap, so a failure retries on the next tap (no
    // cooldown) and tells the user instead of silently doing nothing.
    const lazy = _createLazyModule(context, {
        load: _lazyLoadDealsSources,
        factoryKey: "_realCreateRenderModals",
        label: "render_modals.js",
    });
    const logClientError = context.logClientError || (() => {});

    function _delegate(method, args) {
        const loaded = lazy.current();
        if (loaded) return loaded[method].apply(null, args);
        lazy.ensure()
            .then((real) => {
                real[method].apply(null, args);
                if (typeof context.scheduleRender === "function") {
                    context.scheduleRender();
                }
            })
            .catch((err) => {
                logClientError("modals lazy-load failed:", err);
                if (lazy.current()) return;
                // The detail modal never opened: don't leave state
                // claiming it is on screen (Escape / back would chase it).
                if (method === "renderDetailModal" && context.state?.detail) {
                    context.state.detail.data = null;
                }
                _showLazyLoadToast(context);
            });
        return undefined;
    }

    return {
        renderDetailModal: (...args) => _delegate("renderDetailModal", args),
        closeDetailModal: (...args) => _lazyTeardownCall(lazy, "closeDetailModal", args),
        renderExpensesModal: (...args) => _delegate("renderExpensesModal", args),
        openExpensesModal: (...args) => _delegate("openExpensesModal", args),
        closeExpensesModal: (...args) => _lazyTeardownCall(lazy, "closeExpensesModal", args),
        _ensureLoaded: lazy.ensure,
    };
}
