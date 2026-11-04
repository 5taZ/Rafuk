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

    let _historyAbortController = null;

    // RV-FE-04: a forced refresh must bypass the browser HTTP cache as
    // well — /listings and /price-stats are served with max-age=60, and
    // the service worker only skips its own cache for force_refresh=1.
    function _requestOptions(signal, forceRefresh) {
        return forceRefresh ? { signal, cache: "no-store" } : { signal };
    }

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
        state.detail.requestController?.abort();
        state.detail.requestController = null;
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

    function buildMarketQuery(params = {}) {
        return buildCommonQuery({
            condition: state.filters.condition,
            seller_type: state.filters.sellerType,
            region_name: state.filters.regionName,
            ...params,
        });
    }

    function buildListingsQuery(params = {}) {
        const query = new URLSearchParams(buildMarketQuery(params));
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
        return query.toString();
    }

    function _requestListingPage(params, options) {
        const query = buildListingsQuery(params);
        const request = getJson(`/api/v1/listings?${query}&include_market=false`, options);
        return { query, request };
    }

    function _loadPageMarket(query, items, requestId, signal) {
        if (!items.some((item) => Number(item.price_byn) > 0)) return;
        const params = new URLSearchParams(query);
        params.delete("force_refresh");
        void getJson(`/api/v1/listings?${params}`, { signal, retry: false }).then((payload) => {
            if (signal?.aborted || requestId !== state.listings._requestId) return;
            const targets = new Set(items);
            const comparisons = new Map(
                (payload.listings || []).filter((item) => item.market_comparison)
                    .map((item) => [String(item.ad_id), item]),
            );
            let changed = false;
            state.listings.items = state.listings.items.map((item) => {
                const result = comparisons.get(String(item.ad_id));
                if (!targets.has(item) || !result || result.price_byn !== item.price_byn) return item;
                changed = true;
                return { ...item, market_comparison: result.market_comparison };
            });
            if (changed) {
                markDirty('listings');
                scheduleRender();
            }
        }).catch(() => {});
    }

    // ── Clear search-dependent state ─────────────────────────────────────
    function clearSearchData() {
        state.misc.stats = null;
        state.charts.historyData = [];
        state.misc.segments = null;
        state.misc.geography = [];
        state.listings.items = [];
        state.listings.error = null;
        state.listings.loadMoreError = null;
        state.listings.hasMore = false;
        state.listings.loading = false;
        state.listings.loadingMore = false;
        state.listings._loadedAt = 0;
        state.listings.total = 0;
        state.listings.fallbackUsed = false;
        state.listings.resultCap = 0;
        state.listings.datasetCount = 0;
        state.listings.servedCap = 0;
        state.listings.isLimited = false;
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

    // M10: delegates to shared context.hasActiveListingFilters when available
    function _hasActiveListingFilters() {
        if (typeof context.hasActiveListingFilters === "function") {
            return context.hasActiveListingFilters();
        }
        return (
            state.filters.category != null
            || Boolean(state.filters.condition)
            || Boolean(state.filters.sellerType)
            || state.filters.minPrice != null
            || state.filters.maxPrice != null
            || Boolean(state.filters.regionName)
        );
    }

    // SEARCH-9: drop every applied filter and re-run the search so
    // the listings panel exits the empty state. Used by the
    // "Снять фильтры" CTA inside `renderListingsCollection`'s empty
    // branch — it's the same code path filterApply runs minus the
    // dropdown gymnastics, but always re-fetching the broad slice.
    //
    // We pass `keepFilters: true` because we already cleared the
    // filters above; passing false would make `search()` call
    // `resetCategoryFilter()` again, which is harmless but redundant.
    async function clearListingFilters() {
        if (!state.search.activeQuery?.trim()) return;
        if (!_hasActiveListingFilters()) return;
        resetCategoryFilter();
        markDirty('categories', 'listings', 'stats', 'helper', 'summary');
        renderAll();
        await search(state.ui.activeView || "ads", {
            keepFilters: true,
            query: state.search.activeQuery,
        });
    }

    // ── Load price history (standalone) ──────────────────────────────────
    async function loadHistory() {
        if (!state.search.activeQuery) {
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
        let failed = false;
        try {
            const payload = await getJson(
                `/api/v1/price-history?${buildCommonQuery({ days: state.misc.historyDays })}`,
                { signal }
            );
            nextHistory = payload.points || [];
        } catch (err) {
            if (err.name === "AbortError") return;
            nextHistory = [];
            failed = true;
        }
        if (requestId !== state.charts._historyRequestId) return;
        state.charts.historyData = nextHistory;
        state.charts.historyError = failed;
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
        const signal = state.search.searchAbortController?.signal;
        const refreshOptions = _requestOptions(signal, forceRefresh);
        const listingRequestId = (state.listings._requestId =
            ((state.listings._requestId || 0) + 1) % 1_000_000);
        const listingFilterKey = _listingsFilterKey();
        const listingSort = state.search.sort;
        const listingDiscountFrom = state.filters.discountFromPercent;
        const listingDiscountTo = state.filters.discountToPercent;
        const page = _requestListingPage(_listingsQueryParams({
            limit: PAGE_SIZE,
            offset: 0,
            force_refresh: forceRefresh ? "1" : "",
        }), refreshOptions);

        const dependencies = [
            {
                dirty: ['history'],
                request: getJson(`/api/v1/price-history?${buildCommonQuery({ days: state.misc.historyDays })}`, { signal }),
                apply(payload) {
                    state.charts.historyData = payload.points || [];
                },
            },
            {
                dirty: ['segments'],
                request: getJson(`/api/v1/segments?${buildCommonQuery({ force_refresh: forceRefresh ? "1" : "" })}`, refreshOptions),
                apply(payload) {
                    state.misc.segments = payload;
                },
            },
            {
                dirty: ['geography'],
                request: getJson(`/api/v1/geography?${buildCommonQuery({ force_refresh: forceRefresh ? "1" : "" })}`, refreshOptions),
                apply(payload) {
                    state.misc.geography = payload.regions || [];
                },
            },
            {
                dirty: ['listings', 'loading'],
                request: page.request,
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
                    _loadPageMarket(page.query, state.listings.items, listingRequestId, signal);
                    return true;
                },
                fail(error) {
                    if (listingRequestId !== state.listings._requestId) return;
                    state.listings._pending = false;
                    state.listings.error = error.message || "Не удалось загрузить объявления";
                },
            },
        ];

        for (const dependency of dependencies) {
            void dependency.request
                .then((payload) => {
                    if (!isActiveRequest(requestId)) return;
                    if (dependency.apply(payload) === false) return;
                    markDirty(...dependency.dirty);
                    scheduleRender();
                })
                .catch((err) => {
                    if (err.name === "AbortError" || !isActiveRequest(requestId)) {
                        return;
                    }
                    dependency.fail?.(err);
                    markDirty(...dependency.dirty);
                    scheduleRender();
                });
        }
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
        if (!state.search.activeQuery) {
            state.listings.items = [];
            state.listings.hasMore = false;
            state.listings.error = null;
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
        state.listings.loadingMore = false;
        state.listings.loadMoreError = null;
        state.listings.error = null;
        state.listings._pending = false;
        state.listings.loading = true;
        markDirty('listings');
        renderAll();

        const requestId = (state.listings._requestId =
            ((state.listings._requestId || 0) + 1) % 1_000_000);
        const signal = state.search.searchAbortController?.signal;

        try {
            const page = _requestListingPage(_listingsQueryParams({limit: PAGE_SIZE, offset: 0}), { signal });
            const payload = await page.request;
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
            _loadPageMarket(page.query, state.listings.items, requestId, signal);
        } catch (error) {
            if (requestId !== state.listings._requestId) return;
            state.listings.items = [];
            state.listings.hasMore = false;
            state.listings.error = error.message || "Не удалось загрузить объявления";
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
        if (!state.search.activeQuery) return;
        if (!state.listings.hasMore) return;
        if (state.listings.loadingMore) return;
        state.listings.loadingMore = true;
        state.listings.loadMoreError = null;
        markDirty('listings');
        scheduleRender();

        const offset = state.listings.items.length;
        const requestId = state.listings._requestId;
        const signal = state.search.searchAbortController?.signal;
        try {
            const page = _requestListingPage(_listingsQueryParams({limit: PAGE_SIZE, offset}), { signal });
            const payload = await page.request;
            // Drop the response if the user re-searched in the
            // meantime — the new query ditched the old cursor.
            if (requestId !== state.listings._requestId) return;
            const fresh = payload.listings || [];
            state.listings.items = state.listings.items.concat(fresh);
            state.listings.total = payload.total || state.listings.total;
            state.listings.hasMore = Boolean(payload.has_more);
            _applyListingsMeta(payload);
            _loadPageMarket(page.query, fresh, requestId, signal);
        } catch (err) {
            // FR-01: keep has_more so the button stays, but record the
            // failure — the sentinel then stops auto-loading. Re-arming
            // the observer here looped error → re-render → sentinel in
            // view → request at RTT speed (401/429/5xx storm).
            if (requestId === state.listings._requestId) {
                state.listings.loadMoreError = err?.message || "Не удалось загрузить ещё";
            }
        } finally {
            if (requestId === state.listings._requestId) {
                state.listings.loadingMore = false;
                markDirty('listings');
                renderAll();
            }
        }
    }

    // ── Open listing detail modal ────────────────────────────────────────
    async function openListingDetail(item, {
        category = state.filters.category,
        strictSearch = state.search.strictSearch,
        referenceContext = "base_query",
        fromWatchlist = false,
        onLoaded,
    } = {}) {
        const queryToUse = (item?.query || state.search.activeQuery || "").trim();
        if (!item?.ad_id) return;
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        abortDetailRequest();
        const controller = new AbortController();
        state.detail.requestController = controller;
        const { signal } = controller;

        state.detail.data = {
            ...item,
            title: item.title || "Объявление",
            price: item.price_byn ?? item.price ?? null,
            query: queryToUse,
            images: item.thumbnail ? [item.thumbnail] : [],
        };
        state.detail.imageIndex = 0;
        state.detail.fromWatchlist = fromWatchlist;
        state.detail.ai = {
            adId: item.ad_id,
            loading: false,
            result: null,
            error: "",
            source: "",
        };
        renderDetailModal();

        try {
            const params = new URLSearchParams({
                query: queryToUse,
                currency: state.misc.currency,
                strict_search: String(strictSearch),
                ad_id: String(item.ad_id),
            });
            if (category != null) {
                params.set("category", String(category));
                if (referenceContext !== "current") params.set("reference_context", referenceContext);
            }
            const fullDetail = await getJson(
                `/api/v1/listing-detail?${params.toString()}`,
                { signal }
            );
            if (signal.aborted) return;
            state.detail.data = fullDetail;
            renderDetailModal();
            onLoaded?.(fullDetail);
        } catch {
            // The listing preview stays usable if enrichment fails.
        } finally {
            if (state.detail.requestController === controller) state.detail.requestController = null;
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
     * @param {string}  [options.query]
     *        Re-run this query instead of the text in the search input.
     *        Filters, retry and pull-to-refresh pass the active query so
     *        a half-typed draft never replaces the results on screen.
     */
    async function search(target = "overview", { keepFilters = false, forceRefresh = false, query: requestedQuery } = {}) {
        const rerun = typeof requestedQuery === "string";
        const query = rerun ? requestedQuery.trim() : elements.searchInput.value.trim();
        if (!rerun) state.search.draft = query;
        // RV-FE-03: the previous results are dropped right below
        // (clearSearchData), so from here on everything on screen
        // belongs to this query — commit it before any request is built.
        state.search.activeQuery = query;
        state.ui.error = null;

        if (!query) {
            // RV-FE-03: retire the in-flight search too, or its late
            // response would repaint results for a query that is no
            // longer active.
            state.search.searchRequestId = (state.search.searchRequestId + 1) % 1_000_000;
            state.listings._requestId = ((state.listings._requestId || 0) + 1) % 1_000_000;
            abortSearchRequests();
            clearSearchData();
            // Nothing is loading: let renderListings drop the old cards
            // instead of keeping them as "pending" skeleton content.
            state.listings._pending = false;
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

        // Cancel the previous query's stats, listings and market requests together.
        if (state.search.searchAbortController) {
            state.search.searchAbortController.abort();
        }
        state.search.searchAbortController = new AbortController();
        const searchSignal = state.search.searchAbortController.signal;

        try {
            const statsRequest = getJson(
                `/api/v1/price-stats?${buildMarketQuery({ force_refresh: forceRefresh ? "1" : "" })}`,
                _requestOptions(searchSignal, forceRefresh),
            );
            loadSearchDependencies(requestId, { forceRefresh });
            const stats = await statsRequest;
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
            state.ui.error = error.message || "Не удалось загрузить аналитику.";
            state.ui.loading = false;
            markDirty('loading', 'error', 'summary', 'helper', 'listings');
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
        // SEARCH-9: empty-state CTA dispatches this through
        // `actions.clearListingFilters` to escape a filter-narrowed
        // zero-result panel without retyping the query.
        clearListingFilters,
        loadHistory,
        // FE-C4: abort hooks exposed for callers (closeDetailModal,
        // view changes) to drop pending requests on unmount.
        abortDetailRequest,
        abortHistoryRequest,
        abortSearchRequests,
    };
}
