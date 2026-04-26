/**
 * api_listings.js — Search, listings, deals, comparison, and detail loading.
 *
 * All functions that fetch and manage listing data, search orchestration,
 * and price comparison belong here.
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
        renderComparison,
        renderHistory,
        setPanelOpen,
        renderDetailModal,
        closeDetailModal,
        showToast,
        dismissToast,
        buildCommonQuery,
        getJson,
        deleteJson,
    } = context;

    // ── Stale-request guard ──────────────────────────────────────────────
    function isActiveRequest(requestId) {
        return requestId === state.searchRequestId;
    }

    // Cache TTL: skip reload if data was fetched within this window (ms)
    const CACHE_TTL = 2 * 60 * 1000; // 2 minutes

    // Page size — must match the backend's `_DEFAULT_LISTINGS_PAGE`.
    // Hoisted to module scope so `loadSearchDependencies` can use it.
    const PAGE_SIZE = 50;

    function buildListingsQuery(params = {}) {
        const query = new URLSearchParams(buildCommonQuery(params));
        if (state.category != null) {
            query.set("reference_context", "base_query");
        }
        return query.toString();
    }

    // ── Clear search-dependent state ─────────────────────────────────────
    function clearSearchData() {
        state.stats = null;
        state.history = [];
        state.segments = null;
        state.geography = [];
        state.listings = [];
        state._listingsLoadedAt = 0;
        state.listingsTotal = 0;
        state.dealListings = [];
        state._dealsLoadedAt = 0;
        state.dealsTotal = 0;
        state.comparisonStats = null;
        state.comparisonItems = [];
        state.detail = null;
        state.detailImageIndex = 0;
        state._listingsPending = true;
    }

    function resetCategoryFilter() {
        state.category = null;
        state.categories = [];
        // Reset all filter state when query changes
        state.condition = "";
        state.sellerType = "";
        state.minPrice = null;
        state.maxPrice = null;
        state.regionName = "";
        // Also reset pending values
        state.pendingCategory = null;
        state.pendingCondition = "";
        state.pendingSellerType = "";
        state.pendingMinPrice = null;
        state.pendingMaxPrice = null;
        state.pendingRegionName = "";
    }

    // ── Load price history (standalone) ──────────────────────────────────
    async function loadHistory() {
        if (!state.query) {
            state.history = [];
            renderHistory();
            return;
        }
        // Stale-response guard — switching the history range button or
        // changing the query mid-fetch shouldn't let the older response
        // overwrite the freshly-requested points.
        const requestId = (state._historyRequestId =
            (state._historyRequestId + 1) % 1_000_000);
        let nextHistory;
        try {
            const payload = await getJson(
                `/api/v1/price-history?${buildCommonQuery({ days: state.historyDays })}`
            );
            nextHistory = payload.points || [];
        } catch (_) {
            nextHistory = [];
        }
        if (requestId !== state._historyRequestId) return;
        state.history = nextHistory;
        renderHistory();
    }

    // ── Load parallel search dependencies ────────────────────────────────
    function loadSearchDependencies(requestId) {
        // Abort previous in-flight search dependencies
        if (state.searchAbortController) {
            state.searchAbortController.abort();
        }
        state.searchAbortController = new AbortController();
        const signal = state.searchAbortController.signal;

        const dependencies = [
            {
                request: getJson(`/api/v1/price-history?${buildCommonQuery({ days: state.historyDays })}`, { signal }),
                apply(payload) {
                    state.history = payload.points || [];
                },
            },
            {
                request: getJson(`/api/v1/segments?${buildCommonQuery()}`, { signal }),
                apply(payload) {
                    state.segments = payload;
                },
            },
            {
                request: getJson(`/api/v1/geography?${buildCommonQuery()}`, { signal }),
                apply(payload) {
                    state.geography = payload.regions || [];
                },
            },
            {
                request: getJson(
                    `/api/v1/listings?${buildListingsQuery({
                        sort: state.sort,
                        limit: PAGE_SIZE,
                        offset: 0,
                    })}`,
                    { signal },
                ),
                apply(payload) {
                    state.listings = payload.listings || [];
                    state.listingsTotal = payload.total || 0;
                    state.listingsHasMore = Boolean(payload.has_more);
                    state._listingsLoadedAt = Date.now();
                    state._listingsLoadedSort = state.sort;
                    state._listingsPending = false;
                },
            },
            {
                request: getJson(
                    `/api/v1/listings?${buildListingsQuery({
                        sort: "cheap",
                        discount_from_percent: state.discountFromPercent,
                        discount_to_percent: state.discountToPercent,
                        limit: PAGE_SIZE,
                        offset: 0,
                    })}`,
                    { signal }
                ),
                apply(payload) {
                    state.dealListings = payload.listings || [];
                    state.dealsTotal = payload.total || 0;
                    state.dealsHasMore = Boolean(payload.has_more);
                },
            },
        ];

        for (const dependency of dependencies) {
            dependency.request
                .then((payload) => {
                    if (!isActiveRequest(requestId)) {
                        return;
                    }
                    dependency.apply(payload);
                    markDirty('stats', 'history', 'comparison', 'segments', 'geography', 'listings', 'deals');
                    scheduleRender();
                })
                .catch((err) => {
                    if (err.name === "AbortError" || !isActiveRequest(requestId)) {
                        return;
                    }
                    state._listingsPending = false;
                    markDirty('stats', 'history', 'comparison', 'segments', 'geography', 'listings', 'deals');
                    scheduleRender();
                });
        }
    }

    // ── Load listings (ads view) ─────────────────────────────────────────
    async function loadListings(force) {
        if (!state.query) {
            state.listings = [];
            state.listingsHasMore = false;
            markDirty('listings');
            renderAll();
            return;
        }
        // Skip reload if data is fresh AND sort hasn't changed
        if (!force && state.listings.length && state._listingsLoadedSort === state.sort && Date.now() - state._listingsLoadedAt < CACHE_TTL) {
            return;
        }

        // First page — reset pagination cursor.
        state.listings = [];
        state.listingsHasMore = false;
        state.listingsLoading = true;
        markDirty('listings');
        renderAll();

        const requestId = (state._listingsRequestId =
            ((state._listingsRequestId || 0) + 1) % 1_000_000);

        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery({
                    sort: state.sort,
                    limit: PAGE_SIZE,
                    offset: 0,
                })}`
            );
            if (requestId !== state._listingsRequestId) return;
            state.listings = payload.listings || [];
            state.listingsTotal = payload.total || 0;
            state.listingsHasMore = Boolean(payload.has_more);
            state._listingsLoadedAt = Date.now();
            state._listingsLoadedSort = state.sort;
            state.error = null;
        } catch (error) {
            if (requestId !== state._listingsRequestId) return;
            state.listings = [];
            state.listingsHasMore = false;
            state.error = error.message || "Не удалось загрузить объявления";
            renderError();
        } finally {
            if (requestId === state._listingsRequestId) {
                state.listingsLoading = false;
                markDirty('listings', 'error');
                renderAll();
            }
        }
    }

    async function loadMoreListings() {
        // Append the next page onto state.listings — trigger from the
        // IntersectionObserver attached to the bottom sentinel card.
        // Multiple observer fires while a request is in-flight should
        // be coalesced via state.listingsLoadingMore.
        if (!state.query) return;
        if (!state.listingsHasMore) return;
        if (state.listingsLoadingMore) return;
        state.listingsLoadingMore = true;
        markDirty('listings');
        scheduleRender();

        const offset = state.listings.length;
        const requestId = state._listingsRequestId;
        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery({
                    sort: state.sort,
                    limit: PAGE_SIZE,
                    offset,
                })}`
            );
            // Drop the response if the user re-searched in the
            // meantime — the new query ditched the old cursor.
            if (requestId !== state._listingsRequestId) return;
            const fresh = payload.listings || [];
            state.listings = state.listings.concat(fresh);
            state.listingsTotal = payload.total || state.listingsTotal;
            state.listingsHasMore = Boolean(payload.has_more);
        } catch (_) {
            // On error, surface the chip-style "load more" button by
            // keeping has_more true. The user can tap to retry.
        } finally {
            state.listingsLoadingMore = false;
            markDirty('listings');
            renderAll();
        }
    }

    // ── Load deals (cheap view) ──────────────────────────────────────────
    async function loadDeals(force) {
        if (!state.query) {
            state.dealListings = [];
            state.dealsHasMore = false;
            markDirty('deals');
            renderAll();
            return;
        }
        // Skip reload if data is fresh and same discount range
        const sameRange = state._dealsLoadedFrom === state.discountFromPercent && state._dealsLoadedTo === state.discountToPercent;
        if (!force && state.dealListings.length && sameRange && Date.now() - state._dealsLoadedAt < CACHE_TTL) {
            return;
        }

        state.dealListings = [];
        state.dealsHasMore = false;
        state.dealsLoading = true;
        markDirty('deals');
        renderAll();

        const requestId = (state._dealsRequestId =
            ((state._dealsRequestId || 0) + 1) % 1_000_000);

        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery({
                    sort: "cheap",
                    discount_from_percent: state.discountFromPercent,
                    discount_to_percent: state.discountToPercent,
                    limit: PAGE_SIZE,
                    offset: 0,
                })}`
            );
            if (requestId !== state._dealsRequestId) return;
            state.dealListings = payload.listings || [];
            state.dealsTotal = payload.total || 0;
            state.dealsHasMore = Boolean(payload.has_more);
            state._dealsLoadedAt = Date.now();
            state._dealsLoadedFrom = state.discountFromPercent;
            state._dealsLoadedTo = state.discountToPercent;
            state.error = null;
        } catch (error) {
            if (requestId !== state._dealsRequestId) return;
            state.dealListings = [];
            state.dealsHasMore = false;
            state.error = error.message || "Не удалось загрузить дешёвые объявления";
            renderError();
        } finally {
            if (requestId === state._dealsRequestId) {
                state.dealsLoading = false;
                markDirty('deals', 'error');
                renderAll();
            }
        }
    }

    async function loadMoreDeals() {
        if (!state.query) return;
        if (!state.dealsHasMore) return;
        if (state.dealsLoadingMore) return;
        state.dealsLoadingMore = true;
        markDirty('deals');
        scheduleRender();

        const offset = state.dealListings.length;
        const requestId = state._dealsRequestId;
        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery({
                    sort: "cheap",
                    discount_from_percent: state.discountFromPercent,
                    discount_to_percent: state.discountToPercent,
                    limit: PAGE_SIZE,
                    offset,
                })}`
            );
            if (requestId !== state._dealsRequestId) return;
            const fresh = payload.listings || [];
            state.dealListings = state.dealListings.concat(fresh);
            state.dealsTotal = payload.total || state.dealsTotal;
            state.dealsHasMore = Boolean(payload.has_more);
        } catch (_) {
            // Same behaviour as loadMoreListings — keep has_more so
            // the user can retry via the visible "load more" chip.
        } finally {
            state.dealsLoadingMore = false;
            markDirty('deals');
            renderAll();
        }
    }

    // ── Comparison helpers ───────────────────────────────────────────────
    function normalizedQuery(value) {
        return String(value || "").trim().toLocaleLowerCase("ru-RU");
    }

    async function loadComparison() {
        const comparisonQuery = state.comparisonQuery.trim();
        if (!state.query || !comparisonQuery) {
            state.comparisonStats = null;
            state.comparisonItems = [];
            renderComparison();
            return;
        }
        // Guard against a stale comparison response landing after the
        // user has already kicked off a new compare with a different
        // query string. Without this, swapping queries quickly leaves
        // the panel showing the older comparison.
        const requestId = (state._comparisonRequestId =
            (state._comparisonRequestId + 1) % 1_000_000);

        state.comparisonLoading = true;
        state.error = null;
        setPanelOpen("comparison", true);
        renderComparison();
        renderError();
        try {
            const compareQueries = parseComparisonQueries(comparisonQuery)
                .filter((item) => normalizedQuery(item) !== normalizedQuery(state.query));
            if (!compareQueries.length) {
                state.comparisonStats = null;
                state.comparisonItems = [];
                renderComparison();
                return;
            }
            const params = new URLSearchParams({
                base_query: state.query,
                currency: state.currency,
                strict_search: String(state.strictSearch),
            });
            for (const item of compareQueries) {
                params.append("compare_query", item);
            }
            const payload = await getJson(`/api/v1/compare?${params.toString()}`);
            if (requestId !== state._comparisonRequestId) return;
            state.comparisonStats = payload;
            state.comparisonItems = payload.items || [];
        } catch (error) {
            if (requestId !== state._comparisonRequestId) return;
            state.comparisonStats = null;
            state.comparisonItems = [];
            state.error = error.message || "Не удалось загрузить сравнение";
            renderError();
        } finally {
            if (requestId === state._comparisonRequestId) {
                state.comparisonLoading = false;
                renderComparison();
            }
        }
    }

    async function swapComparisonQueries() {
        const comparisonQuery = state.comparisonQuery.trim();
        if (!state.query || !comparisonQuery) {
            return;
        }

        const compareQueries = parseComparisonQueries(comparisonQuery);
        if (!compareQueries.length) {
            return;
        }
        const previousBase = state.query;
        const [nextBase, ...rest] = compareQueries;
        elements.searchInput.value = nextBase;
        state.query = nextBase;
        state.comparisonQuery = [previousBase, ...rest].join(", ");
        elements.compareInput.value = state.comparisonQuery;
        setPanelOpen("comparison", true);
        renderComparison();
        await search("overview");
    }

    // ── Open listing detail modal ────────────────────────────────────────
    async function openListingDetail(item) {
        const queryToUse = (item?.query || state.query || "").trim();
        if (!item?.ad_id || !queryToUse) {
            return;
        }
        // Stale-response guard — if the user taps a different listing
        // before the first detail fetch resolves, the older response
        // would otherwise overwrite state.detail and pop the wrong
        // modal contents on screen.
        const requestId = (state._detailRequestId =
            (state._detailRequestId + 1) % 1_000_000);

        const loadingToast = showToast("Загружаю...", "info", 1400);
        state.error = null;
        renderError();
        try {
            const params = new URLSearchParams({
                query: queryToUse,
                currency: state.currency,
                strict_search: String(state.strictSearch),
                ad_id: String(item.ad_id),
            });
            if (state.category != null) {
                params.set("category", String(state.category));
                params.set("reference_context", "base_query");
            }
            const fullDetail = await getJson(
                `/api/v1/listing-detail?${params.toString()}`
            );
            if (requestId !== state._detailRequestId) return;
            state.detail = fullDetail;
            state.detailImageIndex = 0;
            state.detailFromWatchlist = false;
            state.detailAi = {
                adId: fullDetail.ad_id || item.ad_id,
                loading: false,
                result: null,
                error: "",
                source: "",
            };
            renderDetailModal();
        } catch (error) {
            if (requestId !== state._detailRequestId) return;
            state.error = error.message || "Не удалось загрузить детали";
            renderError();
        } finally {
            if (loadingToast) dismissToast(loadingToast);
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
    async function search(target = "overview", { keepFilters = false } = {}) {
        const query = elements.searchInput.value.trim();
        state.query = query;
        state.error = null;
        state.comparisonStats = null;

        if (!query) {
            clearSearchData();
            resetCategoryFilter();
            state.loading = false;
            context.focusTarget("overview");
            renderAll();
            return;
        }

        if (!keepFilters) {
            resetCategoryFilter();
        }

        context.focusTarget(target);
        clearSearchData();
        state.loading = true;
        state.searchRequestId = (state.searchRequestId + 1) % 1_000_000;
        const requestId = state.searchRequestId;
        markDirty('loading', 'error', 'summary', 'helper', 'stats');
        renderAll();

        try {
            const stats = await getJson(`/api/v1/price-stats?${buildCommonQuery()}`);
            if (!isActiveRequest(requestId)) {
                return;
            }

            state.stats = stats;
            state.loading = false;
            // Categories cache rules:
            //  - Broad search (state.category == null) ⇒ ALWAYS replace
            //    the chips with whatever the new query returned, even
            //    when the new distribution is empty/1-element. This
            //    prevents stale chips ("Легковые авто (11)") from
            //    bleeding into a refined query that no longer has
            //    that category.
            //  - Narrowed search (a category chip is active) ⇒ keep
            //    the previous distribution because /price-stats with
            //    a `category=` filter only returns that one bucket
            //    and we'd lose the other chips.
            if (state.category == null) {
                state.categories = stats.categories || [];
            } else if (stats.categories && stats.categories.length > 1) {
                state.categories = stats.categories;
            }
            markDirty('loading', 'stats', 'summary', 'helper', 'categories');
            renderAll();
            loadSearchDependencies(requestId);

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
            clearSearchData();
            state.error = error.message || "Не удалось загрузить аналитику.";
            state.loading = false;
            markDirty('loading', 'error', 'summary', 'helper');
            renderAll();
        }

        if (!state.error && state.comparisonQuery.trim()) {
            await loadComparison();
        }
    }

    return {
        search,
        loadListings,
        loadMoreListings,
        loadDeals,
        loadMoreDeals,
        loadComparison,
        swapComparisonQueries,
        openListingDetail,
        loadSearchDependencies,
        clearSearchData,
        resetCategoryFilter,
        loadHistory,
    };
}
