/**
 * api_listings.js — Search, listings, deals, and detail loading.
 *
 * All functions that fetch and manage listing data and search
 * orchestration belong here.
 */

function createApiListings(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        scheduleRender,
        markDirty,
        renderLoading,
        renderError,
        renderHistory,
        setPanelOpen,
        renderDetailModal,
        closeDetailModal,
        showToast,
        buildCommonQuery,
        getJson,
        deleteJson,
    } = context;

    // ── Stale-request guard ──────────────────────────────────────────────
    function isActiveRequest(requestId) {
        return requestId === state.search.searchRequestId;
    }

    // Cache TTL: skip reload if data was fetched within this window (ms)
    const CACHE_TTL = 2 * 60 * 1000; // 2 minutes

    // Page size — must match the backend's `_DEFAULT_LISTINGS_PAGE`.
    // Hoisted to module scope so `loadSearchDependencies` can use it.
    const PAGE_SIZE = 50;

    let _detailAbortController = null;
    let _historyAbortController = null;

    function _normalizedFilterPrice(value) {
        if (value == null || value === "") {
            return null;
        }
        const numeric = Number(value);
        return Number.isFinite(numeric) ? Math.max(0, numeric) : null;
    }

    // FE-C4: explicit abort hooks so callers in the modal/view
    // lifecycle (closeDetailModal, view-tab change, etc.) can drop
    // in-flight requests instead of letting them resolve into stale
    // state. Each hook is a no-op when nothing is pending.
    function abortDetailRequest() {
        if (_detailAbortController) {
            _detailAbortController.abort();
            _detailAbortController = null;
        }
    }
    function abortHistoryRequest() {
        if (_historyAbortController) {
            _historyAbortController.abort();
            _historyAbortController = null;
        }
    }
    function abortSearchRequests() {
        if (state.search.searchAbortController) {
            state.search.searchAbortController.abort();
            state.search.searchAbortController = null;
        }
    }

    function buildListingsQuery(params = {}) {
        const query = new URLSearchParams(buildCommonQuery(params));
        if (state.filters.category != null) {
            query.set("reference_context", "base_query");
        }
        const minPrice = _normalizedFilterPrice(state.filters.minPrice);
        const maxPrice = _normalizedFilterPrice(state.filters.maxPrice);
        if (minPrice != null) {
            query.set("min_price", String(minPrice));
        }
        if (maxPrice != null) {
            query.set("max_price", String(maxPrice));
        }
        if (state.filters.condition) {
            query.set("condition", state.filters.condition);
        }
        if (state.filters.sellerType) {
            query.set("seller_type", state.filters.sellerType);
        }
        if (state.filters.regionName) {
            query.set("region_name", state.filters.regionName);
        }
        return query.toString();
    }

    // ── Clear search-dependent state ─────────────────────────────────────
    function clearSearchData() {
        state.misc.stats = null;
        state.charts.historyData = [];
        state.misc.segments = null;
        state.misc.geography = [];
        state.listings.items = [];
        state.listings._loadedAt = 0;
        state.listings.total = 0;
        state.listings.fallbackUsed = false;
        state.listings.resultCap = 0;
        state.listings.datasetCount = 0;
        state.listings.servedCap = 0;
        state.listings.isLimited = false;
        state.detail.data = null;
        state.detail.imageIndex = 0;
        state.listings._pending = true;
    }

    function resetCategoryFilter() {
        state.filters.category = null;
        state.filters.categories = [];
        // Reset all filter state when query changes
        state.filters.condition = "";
        state.filters.sellerType = "";
        state.filters.minPrice = null;
        state.filters.maxPrice = null;
        state.filters.regionName = "";
        // Also reset pending values
        state.filters.pendingCategory = null;
        state.filters.pendingCondition = "";
        state.filters.pendingSellerType = "";
        state.filters.pendingMinPrice = null;
        state.filters.pendingMaxPrice = null;
        state.filters.pendingRegionName = "";
    }

    // ── Load price history (standalone) ──────────────────────────────────
    async function loadHistory() {
        if (!state.search.query) {
            state.charts.historyData = [];
            renderHistory();
            return;
        }
        // Abort previous in-flight history request
        if (_historyAbortController) {
            _historyAbortController.abort();
        }
        _historyAbortController = new AbortController();
        const signal = _historyAbortController.signal;

        // Stale-response guard — switching the history range button or
        // changing the query mid-fetch shouldn't let the older response
        // overwrite the freshly-requested points.
        const requestId = (state.charts._historyRequestId =
            (state.charts._historyRequestId + 1) % 1_000_000);
        let nextHistory;
        try {
            const payload = await getJson(
                `/api/v1/price-history?${buildCommonQuery({ days: state.misc.historyDays })}`,
                { signal }
            );
            nextHistory = payload.points || [];
        } catch (err) {
            if (err.name === "AbortError") return;
            nextHistory = [];
        }
        if (requestId !== state.charts._historyRequestId) return;
        state.charts.historyData = nextHistory;
        renderHistory();
    }

    // ── Load parallel search dependencies ────────────────────────────────
    function _applyListingsMeta(payload) {
        state.listings.resultCap = Number(payload.result_cap || 0);
        state.listings.datasetCount = Number(payload.dataset_count || 0);
        state.listings.servedCap = Number(payload.served_cap || 0);
        state.listings.isLimited = Boolean(payload.is_limited);
    }

    function loadSearchDependencies(requestId, options = {}) {
        const forceRefresh = Boolean(options.forceRefresh);
        // Abort previous in-flight search dependencies
        if (state.search.searchAbortController) {
            state.search.searchAbortController.abort();
        }
        state.search.searchAbortController = new AbortController();
        const signal = state.search.searchAbortController.signal;
        const listingRequestId = (state.listings._requestId =
            ((state.listings._requestId || 0) + 1) % 1_000_000);
        const listingFilterKey = _listingsFilterKey();
        const listingSort = state.search.sort;
        const listingDiscountFrom = state.filters.discountFromPercent;
        const listingDiscountTo = state.filters.discountToPercent;

        const dependencies = [
            {
                request: getJson(`/api/v1/price-history?${buildCommonQuery({ days: state.misc.historyDays })}`, { signal }),
                apply(payload) {
                    state.charts.historyData = payload.points || [];
                },
            },
            {
                request: getJson(`/api/v1/segments?${buildCommonQuery({ force_refresh: forceRefresh ? "1" : "" })}`, { signal }),
                apply(payload) {
                    state.misc.segments = payload;
                },
            },
            {
                request: getJson(`/api/v1/geography?${buildCommonQuery({ force_refresh: forceRefresh ? "1" : "" })}`, { signal }),
                apply(payload) {
                    state.misc.geography = payload.regions || [];
                },
            },
            {
                request: getJson(
                    `/api/v1/listings?${buildListingsQuery({
                        sort: state.search.sort,
                        limit: PAGE_SIZE,
                        offset: 0,
                        force_refresh: forceRefresh ? "1" : "",
                    })}`,
                    { signal },
                ),
                apply(payload) {
                    if (
                        listingRequestId !== state.listings._requestId ||
                        listingFilterKey !== _listingsFilterKey() ||
                        listingSort !== state.search.sort ||
                        (
                            listingSort === "cheap" &&
                            (
                                listingDiscountFrom !== state.filters.discountFromPercent ||
                                listingDiscountTo !== state.filters.discountToPercent
                            )
                        )
                    ) {
                        return false;
                    }
                    state.listings.items = payload.listings || [];
                    state.listings.total = payload.total || 0;
                    state.listings.hasMore = Boolean(payload.has_more);
                    state.listings.fallbackUsed = Boolean(payload.fallback_used);
                    _applyListingsMeta(payload);
                    state.listings._loadedAt = Date.now();
                    state.listings._loadedSort = state.search.sort;
                    state.listings._loadedFilterKey = _listingsFilterKey();
                    state._listingsLoadedDiscountFrom = state.filters.discountFromPercent;
                    state._listingsLoadedDiscountTo = state.filters.discountToPercent;
                    state.listings._pending = false;
                    return true;
                },
            },
        ];

        // FE-03: each dependency still applies its slice as it lands
        // (so the user sees progressive paint of history → segments →
        // geography → listings), but we ONLY clear ``listings._pending``
        // — i.e. drop the skeletons — once EVERY dependency has
        // settled. The previous code flipped _pending = false the
        // moment any single dependency rejected, which made the
        // listings skeleton vanish while the other three were still
        // in flight, replacing it with a half-empty page.
        const settled = dependencies.map((dependency) =>
            dependency.request
                .then((payload) => {
                    if (!isActiveRequest(requestId)) return;
                    if (dependency.apply(payload) === false) return;
                    markDirty('stats', 'history', 'segments', 'geography', 'listings');
                    scheduleRender();
                })
                .catch((err) => {
                    if (err.name === "AbortError" || !isActiveRequest(requestId)) {
                        return;
                    }
                    // Don't clear _pending here — wait for the whole
                    // fan-out to settle so other still-loading slots
                    // keep their skeletons.
                    markDirty('stats', 'history', 'segments', 'geography', 'listings');
                    scheduleRender();
                })
        );
        Promise.allSettled(settled).then(() => {
            if (!isActiveRequest(requestId)) return;
            state.listings._pending = false;
            markDirty('listings');
            scheduleRender();
        });
    }

    // ── Load listings (ads view) ─────────────────────────────────────────
    function _listingsQueryParams(extra) {
        // Cheap sort folds in the discount-range filters that used to
        // belong to the standalone "Выгодно" view. Other sorts ignore
        // the discount params; the backend only honours them when
        // sort=cheap.
        const params = { sort: state.search.sort, ...extra };
        if (state.search.sort === "cheap") {
            params.discount_from_percent = state.filters.discountFromPercent;
            params.discount_to_percent = state.filters.discountToPercent;
        }
        return params;
    }

    function _sameDiscountRangeAsLast() {
        return (
            state._listingsLoadedDiscountFrom === state.filters.discountFromPercent &&
            state._listingsLoadedDiscountTo === state.filters.discountToPercent
        );
    }

    function _listingsFilterKey() {
        return JSON.stringify({
            category: state.filters.category,
            minPrice: _normalizedFilterPrice(state.filters.minPrice),
            maxPrice: _normalizedFilterPrice(state.filters.maxPrice),
            condition: state.filters.condition || "",
            sellerType: state.filters.sellerType || "",
            regionName: state.filters.regionName || "",
        });
    }

    async function loadListings(force) {
        if (!state.search.query) {
            state.listings.items = [];
            state.listings.hasMore = false;
            _applyListingsMeta({});
            markDirty('listings');
            renderAll();
            return;
        }
        // Skip reload if data is fresh AND sort + discount range hasn't
        // changed. Discount range only matters when sort=cheap.
        const cacheStillValid =
            state.listings.items.length &&
            state.listings._loadedSort === state.search.sort &&
            state.listings._loadedFilterKey === _listingsFilterKey() &&
            (state.search.sort !== "cheap" || _sameDiscountRangeAsLast()) &&
            Date.now() - state.listings._loadedAt < CACHE_TTL;
        if (!force && cacheStillValid) {
            return;
        }

        // First page — reset pagination cursor.
        state.listings.items = [];
        state.listings.hasMore = false;
        state.listings.loading = true;
        markDirty('listings');
        renderAll();

        const requestId = (state.listings._requestId =
            ((state.listings._requestId || 0) + 1) % 1_000_000);

        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery(_listingsQueryParams({
                    limit: PAGE_SIZE,
                    offset: 0,
                }))}`
            );
            if (requestId !== state.listings._requestId) return;
            state.listings.items = payload.listings || [];
            state.listings.total = payload.total || 0;
            state.listings.hasMore = Boolean(payload.has_more);
            state.listings.fallbackUsed = Boolean(payload.fallback_used);
            _applyListingsMeta(payload);
            state.listings._loadedAt = Date.now();
            state.listings._loadedSort = state.search.sort;
            state.listings._loadedFilterKey = _listingsFilterKey();
            state._listingsLoadedDiscountFrom = state.filters.discountFromPercent;
            state._listingsLoadedDiscountTo = state.filters.discountToPercent;
            state.ui.error = null;
        } catch (error) {
            if (requestId !== state.listings._requestId) return;
            state.listings.items = [];
            state.listings.hasMore = false;
            state.ui.error = error.message || "Не удалось загрузить объявления";
            renderError();
        } finally {
            if (requestId === state.listings._requestId) {
                state.listings.loading = false;
                markDirty('listings', 'error');
                renderAll();
            }
        }
    }

    async function loadMoreListings() {
        // Append the next page onto state.listings — trigger from the
        // IntersectionObserver attached to the bottom sentinel card.
        // Multiple observer fires while a request is in-flight should
        // be coalesced via state.listings.loadingMore.
        if (!state.search.query) return;
        if (!state.listings.hasMore) return;
        if (state.listings.loadingMore) return;
        state.listings.loadingMore = true;
        markDirty('listings');
        scheduleRender();

        const offset = state.listings.items.length;
        const requestId = state.listings._requestId;
        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery(_listingsQueryParams({
                    limit: PAGE_SIZE,
                    offset,
                }))}`
            );
            // Drop the response if the user re-searched in the
            // meantime — the new query ditched the old cursor.
            if (requestId !== state.listings._requestId) return;
            const fresh = payload.listings || [];
            state.listings.items = state.listings.items.concat(fresh);
            state.listings.total = payload.total || state.listings.total;
            state.listings.hasMore = Boolean(payload.has_more);
            _applyListingsMeta(payload);
        } catch (_) {
            // On error, surface the chip-style "load more" button by
            // keeping has_more true. The user can tap to retry.
        } finally {
            state.listings.loadingMore = false;
            markDirty('listings');
            renderAll();
        }
    }

    // ── Open listing detail modal ────────────────────────────────────────
    async function openListingDetail(item) {
        const queryToUse = (item?.query || state.search.query || "").trim();
        if (!item?.ad_id || !queryToUse) {
            return;
        }

        // Abort previous in-flight detail request
        if (_detailAbortController) {
            _detailAbortController.abort();
        }
        _detailAbortController = new AbortController();
        const signal = _detailAbortController.signal;

        // Stale-response guard — if the user taps a different listing
        // before the first detail fetch resolves, the older response
        // would otherwise overwrite state.detail and pop the wrong
        // modal contents on screen.
        const requestId = (state.detail._requestId =
            (state.detail._requestId + 1) % 1_000_000);

        state.ui.error = null;
        renderError();
        try {
            const params = new URLSearchParams({
                query: queryToUse,
                currency: state.misc.currency,
                strict_search: String(state.search.strictSearch),
                ad_id: String(item.ad_id),
            });
            if (state.filters.category != null) {
                params.set("category", String(state.filters.category));
                params.set("reference_context", "base_query");
            }
            const fullDetail = await getJson(
                `/api/v1/listing-detail?${params.toString()}`,
                { signal }
            );
            if (requestId !== state.detail._requestId) return;
            state.detail.data = fullDetail;
            state.detail.imageIndex = 0;
            state.detail.fromWatchlist = false;
            state.detail.ai = {
                adId: fullDetail.ad_id || item.ad_id,
                loading: false,
                result: null,
                error: "",
                source: "",
            };
            renderDetailModal();
        } catch (error) {
            if (error.name === "AbortError") {
                return;
            }
            if (requestId !== state.detail._requestId) return;
            state.ui.error = error.message || "Не удалось загрузить детали";
            renderError();
        }
    }

    // ── Search orchestration ─────────────────────────────────────────────
    /**
     * Run a search and (re)load all dependent panels.
     *
     * @param {string}  target           Active view to focus after search.
     * @param {object}  [options]
     * @param {boolean} [options.keepFilters=false]
     *        If true, do NOT reset the category/condition/price/region
     *        filters. The filter dropdown's "Применить" button passes
     *        true so the user-picked category is honored. Every other
     *        entry point (text input, Enter, search button, recent
     *        searches, programmatic) uses the default false: every new
     *        query starts clean — no leftover category from the last
     *        search bleeding through.
     */
    async function search(target = "overview", { keepFilters = false, forceRefresh = false } = {}) {
        const query = elements.searchInput.value.trim();
        state.search.query = query;
        state.ui.error = null;

        if (!query) {
            clearSearchData();
            resetCategoryFilter();
            state.ui.loading = false;
            context.focusTarget("overview");
            renderAll();
            return;
        }

        if (!keepFilters) {
            resetCategoryFilter();
        }

        context.focusTarget(target);
        clearSearchData();
        state.ui.loading = true;
        state.search.searchRequestId = (state.search.searchRequestId + 1) % 1_000_000;
        const requestId = state.search.searchRequestId;
        markDirty('loading', 'error', 'summary', 'helper', 'stats');
        renderAll();

        // FE-C2: cancel any in-flight /price-stats from a previous
        // search before kicking off a new one. Without this, fast
        // typers race two stats requests against each other and the
        // older one's response can clobber the fresh state — even
        // with isActiveRequest() guarding the .then() branch, the
        // socket and DB query keep running on the server. We reuse
        // the same searchAbortController that loadSearchDependencies
        // wires up, so abort() drops both groups in one call.
        if (state.search.searchAbortController) {
            state.search.searchAbortController.abort();
        }
        state.search.searchAbortController = new AbortController();
        const searchSignal = state.search.searchAbortController.signal;

        try {
            const stats = await getJson(
                `/api/v1/price-stats?${buildCommonQuery({ force_refresh: forceRefresh ? "1" : "" })}`,
                { signal: searchSignal },
            );
            if (!isActiveRequest(requestId)) {
                return;
            }

            state.misc.stats = stats;
            state.ui.loading = false;
            // Categories cache rules:
            //  - Broad search (state.filters.category == null) ⇒ ALWAYS replace
            //    the chips with whatever the new query returned, even
            //    when the new distribution is empty/1-element. This
            //    prevents stale chips ("Легковые авто (11)") from
            //    bleeding into a refined query that no longer has
            //    that category.
            //  - Narrowed search (a category chip is active) ⇒ keep
            //    the previous distribution because /price-stats with
            //    a `category=` filter only returns that one bucket
            //    and we'd lose the other chips.
            if (state.filters.category == null) {
                state.filters.categories = stats.categories || [];
            } else if (stats.categories && stats.categories.length > 1) {
                state.filters.categories = stats.categories;
            }
            markDirty('loading', 'stats', 'summary', 'helper', 'categories');
            renderAll();
            loadSearchDependencies(requestId, { forceRefresh });

            // Save to recent searches
            if (context.addRecentSearch) {
                context.addRecentSearch(query);
            }
            if (context._hooks?.renderRecentSearches) {
                context._hooks.renderRecentSearches();
            }
        } catch (error) {
            if (!isActiveRequest(requestId)) {
                return;
            }
            // FE-C2: AbortError is the expected outcome when a
            // newer search() supersedes us; don't surface it as a
            // user-facing failure. Only "real" errors (network/HTTP)
            // get the toast and the retry button.
            if (error?.name === "AbortError") {
                return;
            }
            clearSearchData();
            state.ui.error = error.message || "Не удалось загрузить аналитику.";
            state.ui.loading = false;
            markDirty('loading', 'error', 'summary', 'helper');
            renderAll();
        }
    }

    return {
        search,
        loadListings,
        loadMoreListings,
        openListingDetail,
        loadSearchDependencies,
        clearSearchData,
        resetCategoryFilter,
        loadHistory,
        // FE-C4: abort hooks exposed for callers (closeDetailModal,
        // view changes) to drop pending requests on unmount.
        abortDetailRequest,
        abortHistoryRequest,
        abortSearchRequests,
    };
}
