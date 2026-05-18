/**
 * _lazy_trackers_stub.js — OPUS-13 lazy-load shim for the
 * tracking view.
 *
 * The bundle ships these stub factories instead of the real
 * render_trackers.js / api_trackers.js implementations. Both real
 * modules are loaded in parallel the first time any tracker
 * method is called; subsequent calls go straight to the real
 * factory output. The shared promise lives on
 * ``window.App._trackersLoadPromise`` so the api stub and the
 * render stub don't race each other into duplicate <script> tags.
 *
 * Why a stub-and-delegate pattern instead of replacing the
 * factories at composition time: app_renderers.js destructures
 * the factory return shape (``const { renderTrackers, ... } =
 * trackers;``) and stuffs the resulting functions into
 * ``_renderMap`` + the public renderer API. Replacing the factory
 * result later would leave ``_renderMap`` pointing at stale
 * closures. Stable wrapper functions that internally route
 * through a mutable ``_real`` reference sidestep the rewire.
 */
/* global window */

// OPUS-13: hard-coded ``?v=`` tags so ``scripts/bump_static_version.sh``
// finds and rewrites them in the same pass that updates index.html
// and the AI lazy-load URLs in app_actions.js. A template literal
// over a separate version constant would be invisible to that
// regex.
const _LAZY_TRACKERS_RENDER_URL = "js/render_trackers.js?v=20260518-4fa817a";
const _LAZY_TRACKERS_API_URL = "js/api_trackers.js?v=20260518-4fa817a";

function _lazyLoadTrackerSources(context) {
    window.App = window.App || {};
    if (window.App._trackersLoadPromise) return window.App._trackersLoadPromise;
    window.App._trackersLoadPromise = Promise.all([
        context._loadScript(_LAZY_TRACKERS_RENDER_URL),
        context._loadScript(_LAZY_TRACKERS_API_URL),
    ]).catch((err) => {
        // Reset so the next user attempt can retry from scratch.
        window.App._trackersLoadPromise = null;
        throw err;
    });
    return window.App._trackersLoadPromise;
}

function createRenderTrackers(context) {
    const logClientError = context.logClientError || (() => {});
    let _real = null;
    let _initPromise = null;

    function _ensureLoaded() {
        if (_real) return Promise.resolve(_real);
        if (_initPromise) return _initPromise;
        _initPromise = (async () => {
            await _lazyLoadTrackerSources(context);
            const factory = (window.App || {})._realCreateRenderTrackers;
            if (typeof factory !== "function") {
                throw new Error("render_trackers.js did not register factory");
            }
            _real = factory(context);
            return _real;
        })();
        return _initPromise;
    }

    function _delegate(method, args) {
        if (_real) return _real[method].apply(null, args);
        // Sync render path — kick off lazy load and re-render once
        // it finishes so the user gets populated UI inside one
        // animation frame after opening the tab.
        _ensureLoaded()
            .then((real) => {
                real[method].apply(null, args);
                if (typeof context.scheduleRender === "function") {
                    context.scheduleRender();
                }
            })
            .catch((err) => {
                logClientError("trackers lazy-load failed:", err);
            });
        return undefined;
    }

    return {
        renderTrackerStatus: (...args) => _delegate("renderTrackerStatus", args),
        renderWatchlistFilters: (...args) => _delegate("renderWatchlistFilters", args),
        renderTrackers: (...args) => _delegate("renderTrackers", args),
        renderTrackerEvents: (...args) => _delegate("renderTrackerEvents", args),
        renderTrackerEventFilters: (...args) => _delegate("renderTrackerEventFilters", args),
        _ensureLoaded,
    };
}

function createApiTrackers(context) {
    let _real = null;
    let _initPromise = null;

    function _ensureLoaded() {
        if (_real) return Promise.resolve(_real);
        if (_initPromise) return _initPromise;
        _initPromise = (async () => {
            await _lazyLoadTrackerSources(context);
            const factory = (window.App || {})._realCreateApiTrackers;
            if (typeof factory !== "function") {
                throw new Error("api_trackers.js did not register factory");
            }
            _real = factory(context);
            return _real;
        })();
        return _initPromise;
    }

    async function _delegate(method, args) {
        if (!_real) await _ensureLoaded();
        return _real[method].apply(null, args);
    }

    return {
        loadTrackers: (...args) => _delegate("loadTrackers", args),
        createTracker: (...args) => _delegate("createTracker", args),
        pauseTracker: (...args) => _delegate("pauseTracker", args),
        resumeTracker: (...args) => _delegate("resumeTracker", args),
        deleteTracker: (...args) => _delegate("deleteTracker", args),
        openEditTracker: (...args) => _delegate("openEditTracker", args),
        closeEditTracker: (...args) => _delegate("closeEditTracker", args),
        saveTracker: (...args) => _delegate("saveTracker", args),
        refreshTrackerEvents: (...args) => _delegate("refreshTrackerEvents", args),
        startTrackerRefresh: (...args) => _delegate("startTrackerRefresh", args),
        stopTrackerRefresh: (...args) => _delegate("stopTrackerRefresh", args),
        _ensureLoaded,
    };
}
