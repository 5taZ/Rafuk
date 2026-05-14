/**
 * _lazy_deals_stub.js — OPUS-13 lazy-load shim for the
 * "Мои объявления" view trio: api_leads.js + api_watchlist.js +
 * render_modals.js.
 *
 * Same shape as _lazy_trackers_stub.js — see that file's header
 * for the design rationale. The three real modules load in
 * parallel through one shared promise on
 * ``window.App._dealsLoadPromise`` so any of the api stubs can
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

const _LAZY_DEALS_API_LEADS_URL = "js/api_leads.js?v=20260514-9075538";
const _LAZY_DEALS_API_WATCHLIST_URL = "js/api_watchlist.js?v=20260514-9075538";
const _LAZY_DEALS_RENDER_MODALS_URL = "js/render_modals.js?v=20260514-9075538";

function _lazyLoadDealsSources(context) {
    window.App = window.App || {};
    if (window.App._dealsLoadPromise) return window.App._dealsLoadPromise;
    window.App._dealsLoadPromise = Promise.all([
        context._loadScript(_LAZY_DEALS_API_LEADS_URL),
        context._loadScript(_LAZY_DEALS_API_WATCHLIST_URL),
        context._loadScript(_LAZY_DEALS_RENDER_MODALS_URL),
    ]).catch((err) => {
        window.App._dealsLoadPromise = null;
        throw err;
    });
    return window.App._dealsLoadPromise;
}

function _ensureRealFactory(globalKey, name) {
    const factory = (window.App || {})[globalKey];
    if (typeof factory !== "function") {
        throw new Error(`${name} did not register factory`);
    }
    return factory;
}

function createApiLeads(context) {
    let _real = null;
    let _initPromise = null;

    function _ensureLoaded() {
        if (_real) return Promise.resolve(_real);
        if (_initPromise) return _initPromise;
        _initPromise = (async () => {
            await _lazyLoadDealsSources(context);
            _real = _ensureRealFactory("_realCreateApiLeads", "api_leads.js")(context);
            return _real;
        })();
        return _initPromise;
    }

    async function _delegate(method, args) {
        if (!_real) await _ensureLoaded();
        return _real[method].apply(null, args);
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
        _ensureLoaded,
    };
}

function createApiWatchlist(context) {
    let _real = null;
    let _initPromise = null;

    function _ensureLoaded() {
        if (_real) return Promise.resolve(_real);
        if (_initPromise) return _initPromise;
        _initPromise = (async () => {
            await _lazyLoadDealsSources(context);
            _real = _ensureRealFactory("_realCreateApiWatchlist", "api_watchlist.js")(context);
            return _real;
        })();
        return _initPromise;
    }

    async function _delegate(method, args) {
        if (!_real) await _ensureLoaded();
        return _real[method].apply(null, args);
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
        _ensureLoaded,
    };
}

function createRenderModals(context) {
    let _real = null;
    let _initPromise = null;

    function _ensureLoaded() {
        if (_real) return Promise.resolve(_real);
        if (_initPromise) return _initPromise;
        _initPromise = (async () => {
            await _lazyLoadDealsSources(context);
            _real = _ensureRealFactory("_realCreateRenderModals", "render_modals.js")(context);
            return _real;
        })();
        return _initPromise;
    }

    function _delegate(method, args) {
        if (_real) return _real[method].apply(null, args);
        _ensureLoaded()
            .then((real) => {
                real[method].apply(null, args);
                if (typeof context.scheduleRender === "function") {
                    context.scheduleRender();
                }
            })
            .catch((err) => {
                console.error("modals lazy-load failed:", err);
            });
        return undefined;
    }

    return {
        renderDetailModal: (...args) => _delegate("renderDetailModal", args),
        closeDetailModal: (...args) => _delegate("closeDetailModal", args),
        renderExpensesModal: (...args) => _delegate("renderExpensesModal", args),
        openExpensesModal: (...args) => _delegate("openExpensesModal", args),
        closeExpensesModal: (...args) => _delegate("closeExpensesModal", args),
        _ensureLoaded,
    };
}
