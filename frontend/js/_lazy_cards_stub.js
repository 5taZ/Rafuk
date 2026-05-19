/**
 * _lazy_cards_stub.js — OPUS-13 wave 73 lazy-load shim for the
 * two biggest card files: render_card_builders.js (36 KB raw) +
 * render_cards.js (28 KB raw).
 *
 * virtual_list.js (10 KB) stays in the bundle because
 * render_trackers (lazy wave 70) and render_cards (lazy wave 73)
 * both call ``createVirtualList``; exposing it through
 * ``context.createVirtualList`` (see app_renderers.js) only works
 * if the symbol actually exists somewhere both bundle and lazy
 * scripts can reach — keeping it in the bundle IIFE and proxying
 * via context is the simplest shape.
 *
 * Eager preload: app.js calls ``renderers._cardsEnsureLoaded()``
 * right after ``init()`` so the real cards resolver lands in
 * parallel with the initial paint, not after it. In warm-cache
 * sessions (SW hits) the promise resolves inside one tick; on
 * cold cache the stub serves undefined for a frame or two and
 * then re-renders via ``scheduleRender``.
 */
/* global window */

const _LAZY_CARDS_BUILDERS_URL = "js/render_card_builders.js?v=20260519-1df931e";
const _LAZY_CARDS_RENDER_URL = "js/render_cards.js?v=20260519-1df931e";

function _lazyLoadCardsSources(context) {
    if (_loadPromises.has("cards")) return _loadPromises.get("cards");
    // render_cards.js references createRenderCardBuilders at
    // factory-creation time; load builders first so its global
    // definition exists by the time render_cards.js parses.
    const p = context
        ._loadScript(_LAZY_CARDS_BUILDERS_URL)
        .then(() => {
            if (typeof window.App._realCreateRenderCardBuilders !== "function") {
                throw new Error("render_card_builders.js did not register factory");
            }
            return context._loadScript(_LAZY_CARDS_RENDER_URL);
        })
        .catch((err) => {
            _loadPromises.delete("cards");
            throw err;
        });
    _loadPromises.set("cards", p);
    return p;
}

function createRenderCards(context) {
    const logClientError = context.logClientError || (() => {});
    let _real = null;
    let _initPromise = null;

    function _ensureLoaded() {
        if (_real) return Promise.resolve(_real);
        if (_initPromise) return _initPromise;
        _initPromise = (async () => {
            await _lazyLoadCardsSources(context);
            const factory = (window.App || {})._realCreateRenderCards;
            if (typeof factory !== "function") {
                throw new Error("render_cards.js did not register factory");
            }
            _real = factory(context);
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
                logClientError("cards lazy-load failed:", err);
            });
        return undefined;
    }

    return {
        // Proxy the full surface render_cards.js exports.
        buildListingNode: (...args) => _delegate("buildListingNode", args),
        renderListingsCollection: (...args) => _delegate("renderListingsCollection", args),
        renderListings: (...args) => _delegate("renderListings", args),
        renderLeads: (...args) => _delegate("renderLeads", args),
        renderWatchlist: (...args) => _delegate("renderWatchlist", args),
        _ensureLoaded,
    };
}
