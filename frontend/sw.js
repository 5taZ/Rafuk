/**
 * Rafuk Mini App — service worker.
 *
 * Two cache buckets, one for the static shell, one for the read-only
 * API surfaces that benefit from stale-while-revalidate. Mutations
 * (POST/PATCH/DELETE) and AI analysis endpoints are explicitly
 * bypassed — those need the latest server state, no exceptions.
 *
 * Update flow:
 *   - Bumping CACHE_VERSION below invalidates BOTH buckets on the
 *     next activate, so the old shell can't pin users to a stale
 *     bundle.
 *   - skipWaiting() + clients.claim() picks up the new SW on the
 *     next reload without a "wait for all tabs to close" dance.
 *
 * Designed to be a strict performance / offline-resilience win:
 *   - First view: identical to no-SW (cache is empty, network passthrough).
 *   - Subsequent views: shell from cache, API list-views show last-known
 *     data instantly while a fresh fetch updates them in the background.
 *   - Offline: shell + last-seen lists render; mutations fail with the
 *     usual error toast (the SW doesn't queue them — the existing
 *     in-flight guards already protect the UI).
 */

// FE-M9: bumped to v6 alongside the staleWhileRevalidate max-age.
// Activation drops the v5 RUNTIME_CACHE so any pre-FE-M9 entries
// without a usable Date header get evicted in one shot instead
// of being kept-but-aged forever by the new check.
const CACHE_VERSION = "rafuk-cache-v6";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

// API paths whose GET responses are safe to serve stale while
// revalidating. Picked because their UI surfaces re-render gracefully
// on background update (the loaders all bump request-id guards).
//
// /leads and /watchlist USED to live here but caused a UX bug:
// after a DELETE/PATCH the next GET would hit the SW cache and
// re-show the just-removed item. Background refresh caught up
// eventually, but the user saw the wrong state and had to reload
// the app to fix it. They're now in BYPASS_PATHS so writes are
// always reflected on the next read.
const STALE_WHILE_REVALIDATE_API = [
    "/api/v1/listings",
    "/api/v1/price-stats",
    "/api/v1/price-history",
    "/api/v1/segments",
    "/api/v1/geography",
];

// Hard-bypass: never cache. Either too dynamic (AI), never returns
// the same body twice (token-style endpoints), or write-heavy
// surfaces where stale-after-mutation would mislead the user.
//
// /trackers and /tracker-events are write-heavy: clicking "Следить"
// POSTs and immediately GETs the list back; with stale-while-revalidate
// the GET returned the cached list (without the new tracker) and the
// fresh response only landed on the *next* page reload. Same shape of
// bug as we hit on /leads and /watchlist before.
const BYPASS_PATHS = [
    "/api/v1/ai/",
    "/api/v1/health",
    "/api/v1/contacts",
    "/api/v1/expenses",
    "/api/v1/export",
    "/api/v1/risks",
    "/api/v1/saved-searches",
    "/api/v1/leads",
    "/api/v1/watchlist",
    "/api/v1/trackers",
    "/api/v1/tracker-events",
    "/api/v1/analytics/",
];

self.addEventListener("install", (event) => {
    // Don't pre-cache anything — users on slow connections shouldn't
    // pay for a "warm-up" download. The runtime cache fills as they
    // navigate.
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        (async () => {
            const keys = await caches.keys();
            await Promise.all(
                keys
                    .filter((k) => !k.startsWith(CACHE_VERSION))
                    .map((k) => caches.delete(k)),
            );
            await self.clients.claim();
        })(),
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") return; // mutations always hit network
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return; // CDN scripts, fonts

    // Hard bypass for sensitive / always-fresh endpoints.
    if (BYPASS_PATHS.some((p) => url.pathname.startsWith(p))) return;

    // The HTML shell — network-first so a deploy is picked up on the
    // next refresh; falls back to cached shell when offline.
    if (request.mode === "navigate" || url.pathname === "/" || url.pathname === "/index.html") {
        event.respondWith(networkFirst(request, STATIC_CACHE));
        return;
    }

    // Static assets (CSS, JS, fonts, images served from /assets/).
    if (
        url.pathname.startsWith("/assets/") ||
        /\.(?:css|js|woff2?|svg|ico|png|jpg|jpeg|gif|webp)$/i.test(url.pathname)
    ) {
        event.respondWith(cacheFirst(request, STATIC_CACHE));
        return;
    }

    // Read-only API surfaces — render last-known data instantly.
    if (STALE_WHILE_REVALIDATE_API.some((p) => url.pathname.startsWith(p))) {
        event.respondWith(staleWhileRevalidate(request, RUNTIME_CACHE));
        return;
    }

    // Everything else: passthrough.
});

async function cacheFirst(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    if (cached) return cached;
    try {
        const response = await fetch(request);
        if (response && response.ok && response.type === "basic") {
            cache.put(request, response.clone());
        }
        return response;
    } catch (err) {
        // Offline + nothing cached — let the browser show its native
        // failure UI rather than fabricating a fake response.
        throw err;
    }
}

async function networkFirst(request, cacheName) {
    const cache = await caches.open(cacheName);
    try {
        const response = await fetch(request);
        if (response && response.ok && response.type === "basic") {
            cache.put(request, response.clone());
        }
        return response;
    } catch (err) {
        const cached = await cache.match(request);
        if (cached) return cached;
        // Last resort for navigation requests: fall back to the
        // cached SPA shell so the user at least sees the app
        // chrome instead of the browser's "no internet" page.
        if (request.mode === "navigate") {
            const fallback = await cache.match("/index.html");
            if (fallback) return fallback;
        }
        throw err;
    }
}

// FE-M9: ceiling on how stale a runtime-cache hit may be. Without it,
// a user who left the app open for hours could still see analytics
// snapshots from before lunch — the background revalidate fires but
// the user already started reading and acting on the stale data.
// One hour is short enough to be barely-noticeable for CPU/network
// (we spend one extra fetch per stale entry per hour) but long
// enough to fully amortise away inside a typical "scroll the deals
// list" session.
const RUNTIME_CACHE_MAX_AGE_SECONDS = 60 * 60;


function _cachedResponseAgeSeconds(response) {
    // CacheStorage doesn't track an entry's insertion timestamp, so
    // we read the response's own ``Date`` header (set by FastAPI on
    // every reply via Starlette). A missing header means we have no
    // way to age-check the entry, and we conservatively treat it as
    // fresh — same behaviour as the pre-FE-M9 unbounded cache, but
    // restricted to the rare case where the header is genuinely
    // absent.
    const dateHeader = response.headers.get("date");
    if (!dateHeader) return 0;
    const sentAt = Date.parse(dateHeader);
    if (Number.isNaN(sentAt)) return 0;
    return Math.max(0, (Date.now() - sentAt) / 1000);
}


async function staleWhileRevalidate(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    const fetchPromise = fetch(request)
        .then((response) => {
            if (response && response.ok && response.type === "basic") {
                cache.put(request, response.clone());
            }
            return response;
        })
        .catch(() => null);

    // FE-M9: expire-then-network. Skip the cached entry if it's
    // older than RUNTIME_CACHE_MAX_AGE_SECONDS so the user gets
    // fresh data on next read; the background fetch above also
    // refreshes the cache for the next call.
    if (cached) {
        const age = _cachedResponseAgeSeconds(cached);
        if (age <= RUNTIME_CACHE_MAX_AGE_SECONDS) {
            return cached;
        }
    }

    return (await fetchPromise) || cached || new Response(
        JSON.stringify({ detail: "offline" }),
        {
            status: 503,
            headers: { "Content-Type": "application/json" },
        },
    );
}
