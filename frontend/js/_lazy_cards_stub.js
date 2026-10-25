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

const _LAZY_CARDS_BUILDERS_URL = "js/render_card_builders.js?v=20261025-2832914";
const _LAZY_CARDS_RENDER_URL = "js/render_cards.js?v=20261025-2832914";

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
    // RV-FE-02: shared loader resets after a failed fetch (see
    // _createLazyModule in app_core.js) instead of pinning the rejection.
    const lazy = _createLazyModule(context, {
        load: _lazyLoadCardsSources,
        factoryKey: "_realCreateRenderCards",
        label: "render_cards.js",
    });

    function _containerFor(method, args) {
        const { elements = {}, state } = context;
        if (method === "renderListings") {
            if (!state?.search?.activeQuery) return null;
            if (elements.listingsSection) elements.listingsSection.hidden = false;
            return elements.listingsList;
        }
        if (method === "renderLeads") return elements.leadInboxList;
        if (method === "renderListingsCollection") return args[1];
        return null;
    }

    function _delegate(method, args) {
        return _lazyRenderCall(lazy, context, "cards", method, args, () => {
            _renderLazyLoadError(context, _containerFor(method, args), lazy.retry, undefined, lazy.needsRestart());
        });
    }

    return {
        // Proxy the full surface render_cards.js exports.
        buildListingNode: (...args) => _delegate("buildListingNode", args),
        renderListingsCollection: (...args) => _delegate("renderListingsCollection", args),
        renderListings: (...args) => _delegate("renderListings", args),
        renderLeads: (...args) => _delegate("renderLeads", args),
        _ensureLoaded: lazy.ensure,
    };
}
