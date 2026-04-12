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
        renderLoading,
        renderError,
        renderComparison,
        renderHistory,
        setPanelOpen,
        renderDetailModal,
        closeDetailModal,
        showToast,
        buildCommonQuery,
        getJson,
        postJson,
        deleteJson,
    } = context;

    // ── Stale-request guard ──────────────────────────────────────────────
    function isActiveRequest(requestId) {
        return requestId === state.searchRequestId;
    }

    // ── Clear search-dependent state ─────────────────────────────────────
    function clearSearchData() {
        state.stats = null;
        state.history = [];
        state.segments = null;
        state.geography = [];
        state.listings = [];
        state.dealListings = [];
        state.comparisonStats = null;
        state.comparisonItems = [];
        state.detail = null;
        state.detailImageIndex = 0;
    }

    // ── Load price history (standalone) ──────────────────────────────────
    async function loadHistory() {
        if (!state.query) {
            state.history = [];
            renderHistory();
            return;
        }

        try {
            const payload = await getJson(
                `/api/v1/price-history?${buildCommonQuery({ days: state.historyDays })}`
            );
            state.history = payload.points || [];
        } catch (_) {
            state.history = [];
        } finally {
            renderHistory();
        }
    }

    // ── Load parallel search dependencies ────────────────────────────────
    function loadSearchDependencies(requestId) {
        const dependencies = [
            {
                request: getJson(`/api/v1/price-history?${buildCommonQuery({ days: state.historyDays })}`),
                apply(payload) {
                    state.history = payload.points || [];
                },
            },
            {
                request: getJson(`/api/v1/segments?${buildCommonQuery()}`),
                apply(payload) {
                    state.segments = payload;
                },
            },
            {
                request: getJson(`/api/v1/geography?${buildCommonQuery()}`),
                apply(payload) {
                    state.geography = payload.regions || [];
                },
            },
            {
                request: getJson(`/api/v1/listings?${buildCommonQuery({ sort: state.sort })}`),
                apply(payload) {
                    state.listings = payload.listings || [];
                },
            },
            {
                request: getJson(
                    `/api/v1/listings?${buildCommonQuery({
                        sort: "cheap",
                        discount_from_percent: state.discountFromPercent,
                        discount_to_percent: state.discountToPercent,
                    })}`
                ),
                apply(payload) {
                    state.dealListings = payload.listings || [];
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
                    renderAll();
                })
                .catch(() => {
                    if (!isActiveRequest(requestId)) {
                        return;
                    }
                    renderAll();
                });
        }
    }

    // ── Load listings (ads view) ─────────────────────────────────────────
    async function loadListings() {
        if (!state.query) {
            state.listings = [];
            renderAll();
            return;
        }

        try {
            const payload = await getJson(
                `/api/v1/listings?${buildCommonQuery({ sort: state.sort })}`
            );
            state.listings = payload.listings || [];
            state.error = null;
        } catch (error) {
            state.listings = [];
            state.error = error.message || "Не удалось загрузить объявления";
            renderError();
        } finally {
            renderAll();
        }
    }

    // ── Load deals (cheap view) ──────────────────────────────────────────
    async function loadDeals() {
        if (!state.query) {
            state.dealListings = [];
            renderAll();
            return;
        }

        try {
            const payload = await getJson(
                `/api/v1/listings?${buildCommonQuery({
                    sort: "cheap",
                    discount_from_percent: state.discountFromPercent,
                    discount_to_percent: state.discountToPercent,
                })}`
            );
            state.dealListings = payload.listings || [];
            state.error = null;
        } catch (error) {
            state.dealListings = [];
            state.error = error.message || "Не удалось загрузить дешёвые объявления";
            renderError();
        } finally {
            renderAll();
        }
    }

    // ── Comparison helpers ───────────────────────────────────────────────
    function normalizedQuery(value) {
        return String(value || "").trim().toLocaleLowerCase("ru-RU");
    }

    function parseComparisonQueries(value) {
        const items = String(value || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
        return Array.from(new Set(items.map((item) => item.toLocaleLowerCase("ru-RU"))))
            .map((key) => items.find((item) => item.toLocaleLowerCase("ru-RU") === key))
            .filter(Boolean)
            .slice(0, 2);
    }

    async function loadComparison() {
        const comparisonQuery = state.comparisonQuery.trim();
        if (!state.query || !comparisonQuery) {
            state.comparisonStats = null;
            state.comparisonItems = [];
            renderComparison();
            return;
        }

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
            state.comparisonStats = payload;
            state.comparisonItems = payload.items || [];
        } catch (error) {
            state.comparisonStats = null;
            state.comparisonItems = [];
            state.error = error.message || "Не удалось загрузить сравнение";
            renderError();
        } finally {
            state.comparisonLoading = false;
            renderComparison();
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
        if (!item?.ad_id || !state.query) {
            return;
        }

        showToast("Загружаю...");
        state.error = null;
        renderError();
        try {
            const fullDetail = await getJson(
                `/api/v1/listing-detail?${buildCommonQuery({ ad_id: item.ad_id })}`
            );
            state.detail = fullDetail;
            state.detailImageIndex = 0;
            state.detailFromWatchlist = false;
            renderDetailModal();
        } catch (error) {
            state.error = error.message || "Не удалось загрузить детали";
            renderError();
        }
    }

    // ── Search orchestration ─────────────────────────────────────────────
    async function search(target = "overview") {
        const query = elements.searchInput.value.trim();
        state.query = query;
        state.error = null;
        state.comparisonStats = null;

        if (!query) {
            clearSearchData();
            context.focusTarget("overview");
            renderAll();
            return;
        }

        context.focusTarget(target);
        clearSearchData();
        state.loading = true;
        state.searchRequestId += 1;
        const requestId = state.searchRequestId;
        renderAll();

        try {
            const stats = await getJson(`/api/v1/price-stats?${buildCommonQuery()}`);
            if (!isActiveRequest(requestId)) {
                return;
            }

            state.stats = stats;
            state.loading = false;
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
            renderAll();
        }

        if (!state.error && state.comparisonQuery.trim()) {
            await loadComparison();
        }
        if (!state.error) {
            void loadMarketVelocity();
        }
    }

    // ── Market Velocity ──────────────────────────────────────────────────
    async function loadMarketVelocity() {
        if (!state.query) {
            context.renderVelocity(null);
            return;
        }
        try {
            context.renderVelocity(null);
        } catch (_) {
            context.renderVelocity(null);
        }
    }

    // ── Detail Risk Assessment ───────────────────────────────────────────
    async function loadDetailRisks(item) {
        if (!item || !item.price) {
            context.renderDetailRisks(null);
            return;
        }
        try {
            const data = await postJson("/api/v1/risk-assessment", {
                price_byn: item.price_byn || item.price || null,
                description: item.description || "",
                photo_count: item.photo_count || 0,
                market_median: state.stats?.median || null,
            });
            context.renderDetailRisks(data);
        } catch (_) {
            context.renderDetailRisks(null);
        }
    }

    return {
        search,
        loadListings,
        loadDeals,
        loadComparison,
        swapComparisonQueries,
        openListingDetail,
        loadSearchDependencies,
        clearSearchData,
        loadHistory,
        loadMarketVelocity,
        loadDetailRisks,
    };
}
