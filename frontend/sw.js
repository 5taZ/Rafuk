/**
 * Rafuk Mini App — service worker.
 *
 * Three cache buckets: the static shell, a network-first fallback for
 * the read-only API surfaces, and a bounded bucket for proxied listing
 * photos. Mutations (POST/PATCH/DELETE) and AI analysis endpoints are
 * explicitly bypassed — those need the latest server state, no
 * exceptions.
 *
 * Update flow:
 *   - Bumping CACHE_VERSION below invalidates every bucket on the
 *     next activate, so the old shell can't pin users to a stale
 *     bundle.
 *   - skipWaiting() + clients.claim() picks up the new SW on the
 *     next reload without a "wait for all tabs to close" dance.
 *
 * Designed to be a strict performance / offline-resilience win:
 *   - First view: identical to no-SW (cache is empty, network passthrough).
 *   - API list views always go to the network first; the last copy is
 *     served only when the network fails or hangs, and is marked so
 *     the page can say how old it is.
 *   - Offline: shell + last-seen lists render; mutations fail with the
 *     usual error toast (the SW doesn't queue them — the existing
 *     in-flight guards already protect the UI).
 */

// FE-M8 / FE-M9: bumped to v9. v6 added the staleWhileRevalidate
// max-age check; v7 added the offline-fallback page; v8 moves that
// fallback's CSS out of inline <style>; v9 evicts stale frontend
// bundles after the lead-version compatibility fix.
const CACHE_VERSION = "rafuk-cache-20261021-1872adf";
const OFFLINE_FALLBACK_URL = "/offline.html";
const OFFLINE_FALLBACK_ASSETS = [OFFLINE_FALLBACK_URL, "/offline.css"];
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;
// RV-FE-04: proxied listing photos used to land in the static bucket
// (their paths end in .jpg) and pile up without any limit.
const IMAGE_CACHE = `${CACHE_VERSION}-img`;
const IMAGE_CACHE_MAX_ENTRIES = 120;
const IMAGE_PROXY_PREFIX = "/api/v1/img/";

// RV-FE-04: read-only API surfaces are network-first. They used to be
// stale-while-revalidate with a one-hour ceiling, so a search repeated
// 50 minutes later showed the old market and pull-to-refresh changed
// nothing. The cached copy now only answers when the network fails,
// hangs past API_NETWORK_TIMEOUT_MS, or the gateway is down.
const NETWORK_FIRST_API = [
    "/api/v1/listings",
    "/api/v1/price-stats",
    "/api/v1/price-history",
    "/api/v1/segments",
    "/api/v1/geography",
];
const API_NETWORK_TIMEOUT_MS = 10_000;
const GATEWAY_ERROR_STATUSES = new Set([502, 503, 504]);
const OFFLINE_MESSAGE = "Нет соединения с сервером. Проверьте интернет и попробуйте ещё раз.";

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
    // FE-M8: pre-cache *only* the offline fallback page and its tiny
    // stylesheet so the SW can serve a meaningful response on the
    // very first offline navigation, before the user has had a chance
    // to load the shell at least once. Everything else (CSS, JS, API)
    // still fills the runtime cache lazily as the user navigates — we
    // don't want a "warm-up download" tax on slow connections.
    event.waitUntil(
        caches
            .open(STATIC_CACHE)
            .then((cache) => cache.addAll(OFFLINE_FALLBACK_ASSETS))
            .catch(() => {
                // If pre-cache fails (e.g. the file is briefly 404
                // during a deploy) the SW still installs; the
                // navigateOrOffline handler below falls back to
                // ``throw err`` and the browser shows its native
                // offline UI as before.
            }),
    );
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

    // RV-FE-04: an explicit refresh (pull-to-refresh, "Повторить")
    // must never be answered from a cache. The offline 503 that used to
    // sit here — before any cache lookup — is gone too: being offline is
    // exactly when the last copy is worth showing.
    if (isForcedRefresh(url, request)) return;

    // Proxied photos: immutable, so cache-first, in their own bounded
    // bucket. Checked before the static-asset rule below, which would
    // otherwise claim them by their .jpg extension.
    if (url.pathname.startsWith(IMAGE_PROXY_PREFIX)) {
        event.respondWith(cacheFirstBounded(event, request, IMAGE_CACHE, IMAGE_CACHE_MAX_ENTRIES));
        return;
    }

    // Read-only API surfaces — the network answers; the cache only
    // stands in when the network cannot.
    if (NETWORK_FIRST_API.some((p) => url.pathname.startsWith(p))) {
        event.respondWith(networkFirstApi(event, request, RUNTIME_CACHE));
        return;
    }

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

    // Everything else: passthrough.
});

function isForcedRefresh(url, request) {
    const flag = (url.searchParams.get("force_refresh") || "").toLowerCase();
    return flag === "1" || flag === "true"
        || request.cache === "no-store" || request.cache === "reload";
}

// Never persist what the server marked private or uncacheable.
function isStorable(response) {
    if (!response || !response.ok || response.type !== "basic") return false;
    const cacheControl = (response.headers.get("cache-control") || "").toLowerCase();
    return !/\b(?:no-store|private)\b/.test(cacheControl);
}

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

async function cacheFirstBounded(event, request, cacheName, maxEntries) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (isStorable(response)) {
        keepAlive(
            event,
            cache.put(request, response.clone())
                .then(() => trimCache(cache, maxEntries))
                .catch(() => {}),
        );
    }
    return response;
}

// Cache writes outlive the response; a waitUntil that comes too late
// must not turn a good network answer into an error.
function keepAlive(event, promise) {
    try {
        event.waitUntil(promise);
    } catch (_) {
        // Lifetime already ended — the write still runs best-effort.
    }
}

// CacheStorage keeps insertion order, so the oldest photos go first.
async function trimCache(cache, maxEntries) {
    const keys = await cache.keys();
    const excess = keys.length - maxEntries;
    for (let i = 0; i < excess; i++) {
        await cache.delete(keys[i]);
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
            // FE-M8: if even the SPA shell isn't cached (first visit
            // happened offline), fall through to the dedicated
            // offline page. This lives in STATIC_CACHE because we
            // pre-fetch it on install.
            const offline = await cache.match(OFFLINE_FALLBACK_URL);
            if (offline) return offline;
            const offlineFromStatic = await caches.match(OFFLINE_FALLBACK_URL);
            if (offlineFromStatic) return offlineFromStatic;
        }
        throw err;
    }
}

// FE-M9: ceiling on how old a fallback copy may be. Even offline, a
// snapshot older than this misleads more than it helps.
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

async function _usableFallback(cache, request) {
    const cached = await cache.match(request);
    if (!cached || _cachedResponseAgeSeconds(cached) > RUNTIME_CACHE_MAX_AGE_SECONDS) {
        return null;
    }
    // The page reads this marker (plus the Date header) to tell the
    // user the data is a saved copy and how old it is.
    const headers = new Headers(cached.headers);
    headers.set("X-SW-Fallback", "1");
    return new Response(cached.body, {
        status: cached.status,
        statusText: cached.statusText,
        headers,
    });
}

function offlineResponse() {
    return new Response(
        JSON.stringify({ detail: { error: "offline", message: OFFLINE_MESSAGE } }),
        {
            status: 503,
            headers: { "Content-Type": "application/json", "X-SW-Offline": "1" },
        },
    );
}

async function networkFirstApi(event, request, cacheName) {
    const cache = await caches.open(cacheName);
    const network = fetch(request).then((response) => {
        if (isStorable(response)) {
            keepAlive(event, cache.put(request, response.clone()).catch(() => {}));
        }
        return response;
    });
    const TIMED_OUT = Symbol("timed-out");
    let timer = null;
    const timeout = new Promise((resolve) => {
        timer = setTimeout(() => resolve(TIMED_OUT), API_NETWORK_TIMEOUT_MS);
    });
    try {
        const first = await Promise.race([network, timeout]);
        if (first !== TIMED_OUT) {
            if (!GATEWAY_ERROR_STATUSES.has(first.status)) return first;
            // Backend unreachable behind the gateway: same as offline.
            return (await _usableFallback(cache, request)) || first;
        }
        // The network hangs: answer with a recent copy if there is one
        // (the live request keeps running and refreshes the cache),
        // otherwise keep waiting for it.
        const fallback = await _usableFallback(cache, request);
        if (fallback) {
            keepAlive(event, network.catch(() => {}));
            return fallback;
        }
        return await network;
    } catch (_) {
        return (await _usableFallback(cache, request)) || offlineResponse();
    } finally {
        clearTimeout(timer);
    }
}
