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

const CACHE_VERSION = "rafuk-cache-v1";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

// API paths whose GET responses are safe to serve stale while
// revalidating. Picked because their UI surfaces re-render gracefully
// on background update (the loaders all bump request-id guards).
const STALE_WHILE_REVALIDATE_API = [
    "/api/v1/listings",
    "/api/v1/leads",
    "/api/v1/watchlist",
    "/api/v1/trackers",
    "/api/v1/tracker-events",
    "/api/v1/price-stats",
    "/api/v1/price-history",
    "/api/v1/segments",
    "/api/v1/geography",
];

// Hard-bypass: never cache. Either too dynamic (AI) or never returns
// the same body twice (token-style endpoints).
const BYPASS_PATHS = [
    "/api/v1/ai/",
    "/api/v1/health",
    "/api/v1/contacts",
    "/api/v1/expenses",
    "/api/v1/export",
    "/api/v1/risks",
    "/api/v1/saved-searches",
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
    return cached || (await fetchPromise) || new Response(
        JSON.stringify({ detail: "offline" }),
        {
            status: 503,
            headers: { "Content-Type": "application/json" },
        },
    );
}
