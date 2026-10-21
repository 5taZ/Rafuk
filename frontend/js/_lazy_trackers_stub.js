/**
 * _lazy_trackers_stub.js — OPUS-13 lazy-load shim for the
 * tracking view.
 *
 * The bundle ships these stub factories instead of the real
 * render_trackers.js / api_trackers.js implementations. Both real
 * modules are loaded in parallel the first time any tracker
 * method is called; subsequent calls go straight to the real
 * factory output. The shared promise lives on
 * ``_loadPromises`` Map keyed by "trackers" so the api stub and the
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
const _LAZY_TRACKERS_RENDER_URL = "js/render_trackers.js?v=20261021-1872adf";
const _LAZY_TRACKERS_API_URL = "js/api_trackers.js?v=20261021-1872adf";

function _lazyLoadTrackerSources(context) {
    if (_loadPromises.has("trackers")) return _loadPromises.get("trackers");
    const p = Promise.all([
        context._loadScript(_LAZY_TRACKERS_RENDER_URL),
        context._loadScript(_LAZY_TRACKERS_API_URL),
    ]).catch((err) => {
        _loadPromises.delete("trackers");
        throw err;
    });
    _loadPromises.set("trackers", p);
    return p;
}

function createRenderTrackers(context) {
    // RV-FE-02: see _createLazyModule (app_core.js).
    const lazy = _createLazyModule(context, {
        load: _lazyLoadTrackerSources,
        factoryKey: "_realCreateRenderTrackers",
        label: "render_trackers.js",
    });

    // The tracker data loaders live in the same failed chunk, so a
    // successful retry has to fetch the lists too — otherwise the view
    // would claim "no trackers yet".
    function _reloadTrackerData() {
        const actions = context.actions || {};
        if (typeof actions.loadTrackers === "function") void actions.loadTrackers();
        if (context.state?.ui?.activeView === "tracking" && typeof actions.startTrackerRefresh === "function") {
            void actions.startTrackerRefresh();
        }
    }

    function _delegate(method, args) {
        return _lazyRenderCall(lazy, context, "trackers", method, args, () => {
            const elements = context.elements || {};
            let container = null;
            if (method === "renderTrackers") container = elements.trackersList;
            if (method === "renderTrackerEvents") container = elements.trackerEventsList;
            _renderLazyLoadError(context, container, lazy.retry, _reloadTrackerData);
        });
    }

    return {
        renderTrackerStatus: (...args) => _delegate("renderTrackerStatus", args),
        renderWatchlistFilters: (...args) => _delegate("renderWatchlistFilters", args),
        renderTrackers: (...args) => _delegate("renderTrackers", args),
        renderTrackerEvents: (...args) => _delegate("renderTrackerEvents", args),
        renderTrackerEventFilters: (...args) => _delegate("renderTrackerEventFilters", args),
        _ensureLoaded: lazy.ensure,
    };
}

// Loads and background refreshes are reported by the render stub's
// error state in the lists; only explicit user actions get a toast.
const _QUIET_TRACKER_METHODS = new Set(["loadTrackers", "refreshTrackerEvents", "startTrackerRefresh"]);

function createApiTrackers(context) {
    // RV-FE-02: see _createLazyModule (app_core.js).
    const lazy = _createLazyModule(context, {
        load: _lazyLoadTrackerSources,
        factoryKey: "_realCreateApiTrackers",
        label: "api_trackers.js",
    });

    function _onFailure(method) {
        if (!_QUIET_TRACKER_METHODS.has(method)) _showLazyLoadToast(context);
        return undefined;
    }

    function _delegate(method, args) {
        return _lazyApiCall(lazy, context, "trackers", method, args, _onFailure);
    }

    return {
        loadTrackers: (...args) => _delegate("loadTrackers", args),
        createTracker: (...args) => _delegate("createTracker", args),
        pauseTracker: (...args) => _delegate("pauseTracker", args),
        resumeTracker: (...args) => _delegate("resumeTracker", args),
        deleteTracker: (...args) => _delegate("deleteTracker", args),
        openEditTracker: (...args) => _delegate("openEditTracker", args),
        closeEditTracker: (...args) => _lazyTeardownCall(lazy, "closeEditTracker", args),
        saveTracker: (...args) => _delegate("saveTracker", args),
        refreshTrackerEvents: (...args) => _delegate("refreshTrackerEvents", args),
        startTrackerRefresh: (...args) => _delegate("startTrackerRefresh", args),
        // A refresh timer cannot be running before the module loaded.
        stopTrackerRefresh: (...args) => _lazyTeardownCall(lazy, "stopTrackerRefresh", args),
        _ensureLoaded: lazy.ensure,
    };
}
