function createAppActions(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderViews,
        renderSortButtons,
        renderDiscountButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderComparison,
        renderHistory,
        renderTrackerStatus,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
        renderLeads,
        renderWatchlist,
        renderSavedSearches,
        renderOpportunityBoard,
        renderDetailModal,
        closeDetailModal,
        setPanelOpen,
    } = context;

    function telegramHeaders() {
        const initData = window.Telegram?.WebApp?.initData;
        return initData ? { "X-Telegram-Init-Data": initData } : {};
    }

    async function requestJson(url, options = {}) {
        const headers = {
            ...telegramHeaders(),
            ...(options.headers || {}),
        };
        const response = await fetch(url, {
            ...options,
            headers,
        });

        if (!response.ok) {
            let message = "Не удалось выполнить запрос.";
            try {
                const payload = await response.json();
                if (typeof payload.detail === "string" && payload.detail.trim()) {
                    message = payload.detail.trim();
                }
            } catch (_) {
                if (response.status >= 500) {
                    message = "API недоступен. Поднимите uvicorn на 0.0.0.0:8010 и обновите Mini App.";
                }
            }
            throw new Error(message);
        }

        if (response.status === 204) {
            return null;
        }

        return response.json();
    }

    function getJson(url) {
        return requestJson(url);
    }

    function postJson(url, payload) {
        return requestJson(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    function deleteJson(url) {
        return requestJson(url, { method: "DELETE" });
    }

    function buildCommonQuery(params = {}) {
        const query = new URLSearchParams({
            query: state.query,
            currency: state.currency,
            strict_search: String(state.strictSearch),
        });

        for (const [key, value] of Object.entries(params)) {
            if (value == null || value === "") {
                continue;
            }
            query.set(key, String(value));
        }

        return query.toString();
    }

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

    function isActiveRequest(requestId) {
        return requestId === state.searchRequestId;
    }

    async function loadSearchDependencies(requestId) {
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

    function setActiveView(view) {
        if (!(view in elements.views)) {
            return;
        }
        state.activeView = view;
        renderViewTabs();
        renderViews();
    }

    function scrollSectionIntoView(section) {
        section?.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function focusTarget(target) {
        if (target === "ads") {
            setActiveView("ads");
            return;
        }
        if (target === "deals") {
            setActiveView("deals");
            return;
        }

        setActiveView("overview");

        if (target === "history") {
            setPanelOpen("history", true);
            scrollSectionIntoView(elements.historySection);
        } else if (target === "comparison") {
            setPanelOpen("comparison", true);
            scrollSectionIntoView(elements.comparisonSection);
        }
    }

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

    async function loadRates() {
        try {
            const payload = await getJson("/api/v1/currency-rates");
            state.usdRateByn = Number(payload?.rates?.USD || 0) || null;
        } catch (_) {
            state.usdRateByn = null;
        } finally {
            renderAll();
        }
    }

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

    async function openListingDetail(item) {
        if (!item?.ad_id || !state.query) {
            return;
        }

        state.error = null;
        renderError();
        try {
            state.detail = await getJson(
                `/api/v1/listing-detail?${buildCommonQuery({ ad_id: item.ad_id })}`
            );
            state.detailImageIndex = 0;
            renderDetailModal();
        } catch (error) {
            state.error = error.message || "Не удалось открыть карточку";
            renderError();
        }
    }

    async function loadTrackers() {
        if (!hasTelegramInitData()) {
            state.trackers = [];
            state.trackerEvents = [];
            state.trackerStatus = "";
            renderTrackers();
            renderTrackerEvents();
            renderTrackerStatus();
            return;
        }

        elements.trackersList.innerHTML = '<div class="loading-placeholder"><span class="spin"></span> Загрузка...</div>';
        elements.trackerEventsList.innerHTML = '<div class="loading-placeholder"><span class="spin"></span> Загрузка...</div>';

        const [trackersResult, eventsResult] = await Promise.allSettled([
            getJson("/api/v1/trackers"),
            getJson("/api/v1/tracker-events"),
        ]);

        state.trackers = trackersResult.status === "fulfilled" ? trackersResult.value : [];
        state.trackerEvents = eventsResult.status === "fulfilled" ? eventsResult.value : [];

        if (trackersResult.status === "rejected") {
            state.trackerStatus = trackersResult.reason?.message || "Не удалось загрузить трекеры.";
            state.trackerStatusKind = "error";
        } else {
            state.trackerStatus = "";
            state.trackerStatusKind = "info";
        }

        renderTrackers();
        renderTrackerEvents();
        renderTrackerStatus();
    }

    async function loadLeads() {
        if (!hasTelegramInitData()) {
            state.leads = [];
            renderLeads();
            return;
        }

        elements.leadInboxList.innerHTML = '<div class="loading-placeholder"><span class="spin"></span> Загрузка...</div>';

        try {
            state.leads = await getJson("/api/v1/leads");
        } catch (_) {
            state.leads = [];
        } finally {
            renderLeads();
        }
    }

    async function loadWatchlist() {
        if (!hasTelegramInitData()) {
            state.watchlist = [];
            renderWatchlist();
            return;
        }

        elements.watchlistList.innerHTML = '<div class="loading-placeholder"><span class="spin"></span> Загрузка...</div>';

        try {
            state.watchlist = await getJson("/api/v1/watchlist");
        } catch (_) {
            state.watchlist = [];
        } finally {
            renderWatchlist();
        }
    }

    async function loadSavedSearches() {
        if (!hasTelegramInitData()) {
            state.savedSearches = [];
            renderSavedSearches();
            return;
        }

        elements.savedSearchesList.innerHTML = '<div class="loading-placeholder"><span class="spin"></span> Загрузка...</div>';

        try {
            state.savedSearches = await getJson("/api/v1/saved-searches");
        } catch (_) {
            state.savedSearches = [];
        } finally {
            renderSavedSearches();
        }
    }

    async function loadOpportunityBoard() {
        if (!hasTelegramInitData()) {
            state.opportunityBoard = { items: [], top_price_drops: [], rare_opportunities: [], market_signals: [] };
            renderOpportunityBoard();
            return;
        }

        elements.opportunityBoardList.innerHTML = '<div class="loading-placeholder"><span class="spin"></span> Загрузка...</div>';

        try {
            state.opportunityBoard = await getJson(`/api/v1/opportunity-board?currency=${state.currency}`);
        } catch (_) {
            state.opportunityBoard = { items: [], top_price_drops: [], rare_opportunities: [], market_signals: [] };
        } finally {
            renderOpportunityBoard();
        }
    }

    async function createTracker() {
        if (!hasTelegramInitData()) {
            state.trackerStatus = "Эта функция доступна только внутри Telegram Mini App.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
            return;
        }

        const query = state.query.trim();
        if (!query) {
            state.trackerStatus = "Сначала введите запрос.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
            return;
        }

        state.trackerStatus = "Сохраняю трекер...";
        state.trackerStatusKind = "info";
        renderTrackerStatus();

        try {
            await postJson("/api/v1/trackers", {
                query,
                strict_mode: state.strictSearch,
                interval_min: 15,
                min_discount_percent: state.trackerMinDiscountPercent,
                max_price_byn: state.trackerMaxPriceByn,
                seller_type: state.trackerSellerType || null,
                condition: state.trackerCondition || null,
                region_name: state.trackerRegionName || null,
                config_keyword: state.trackerConfigKeyword || null,
                exclude_duplicates: state.trackerExcludeDuplicates,
            });
            state.trackerStatus = "Трекер добавлен.";
            state.trackerStatusKind = "success";
            renderTrackerStatus();
            await loadTrackers();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось создать трекер.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function addLeadFromListing(item, source = "manual", queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }
        try {
            const marketEstimate = (item.flip_estimates || []).find((entry) => entry.label === "По рынку");
            await postJson("/api/v1/leads", {
                query: queryOverride || state.query || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
                target_resale_byn: marketEstimate?.target_price || null,
                status: "new",
                source,
            });
            state.trackerStatus = "Лот добавлен в Inbox.";
            state.trackerStatusKind = "success";
            renderTrackerStatus();
            await loadLeads();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось добавить лот в Inbox.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function addWatchlistFromListing(item, queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }
        try {
            await postJson("/api/v1/watchlist", {
                query: queryOverride || state.query || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
            });
            state.trackerStatus = "Лот добавлен в Watchlist.";
            state.trackerStatusKind = "success";
            renderTrackerStatus();
            await loadWatchlist();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось добавить лот в Watchlist.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function updateLeadStatus(leadId, nextStatus) {
        await updateLeadMeta(leadId, { status: nextStatus });
    }

    async function updateLeadMeta(leadId, payload) {
        try {
            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            await loadLeads();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось обновить статус в Inbox.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function updateWatchlistStatus(watchlistId, workflowStatus) {
        await updateWatchlistMeta(watchlistId, { workflow_status: workflowStatus });
    }

    async function updateWatchlistMeta(watchlistId, payload) {
        try {
            await requestJson(`/api/v1/watchlist/${watchlistId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            await loadWatchlist();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось обновить Watchlist.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function promoteWatchlistToLead(item) {
        if (!item?.ad_id) {
            return;
        }
        await addLeadFromListing(
            {
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.current_price_byn || item.initial_price_byn,
                flip_estimates: [],
            },
            "watchlist",
            item.query
        );
        await updateWatchlistMeta(item.id, { workflow_status: "in_progress" });
    }

    async function deleteWatchlistItem(watchlistId) {
        try {
            await deleteJson(`/api/v1/watchlist/${watchlistId}`);
            await loadWatchlist();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось удалить лот из Watchlist.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function refreshWatchlist() {
        try {
            const payload = await postJson("/api/v1/watchlist/refresh", {});
            state.trackerStatus = `Watchlist обновлён: ${payload.updated} проверено, ${payload.price_drops} падений цены, ${payload.missing} пропавших.`;
            state.trackerStatusKind = "success";
            renderTrackerStatus();
            await loadWatchlist();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось обновить Watchlist.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function saveCurrentSearch() {
        if (!hasTelegramInitData()) {
            return;
        }

        const query = state.query.trim();
        if (!query) {
            state.trackerStatus = "Сначала выполните поиск, затем сохраните его.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
            return;
        }

        try {
            await postJson("/api/v1/saved-searches", {
                query,
                group_name: elements.savedSearchGroupInput?.value?.trim() || state.savedSearchGroupName,
                strict_mode: state.strictSearch,
                target_discount_percent: state.discountFromPercent,
                max_price_byn: state.trackerMaxPriceByn,
                seller_type: state.trackerSellerType || null,
                condition: state.trackerCondition || null,
                region_name: state.trackerRegionName || null,
                config_keyword: state.trackerConfigKeyword || null,
                exclude_duplicates: state.trackerExcludeDuplicates,
            });
            state.trackerStatus = "Поиск сохранён.";
            state.trackerStatusKind = "success";
            renderTrackerStatus();
            await loadSavedSearches();
            await loadOpportunityBoard();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось сохранить поиск.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function deleteSavedSearch(savedSearchId) {
        if (!savedSearchId) {
            return;
        }

        try {
            await deleteJson(`/api/v1/saved-searches/${savedSearchId}`);
            await loadSavedSearches();
            await loadOpportunityBoard();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось удалить сохранённый поиск.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function applySavedSearch(savedSearch, mode = "search") {
        if (!savedSearch) {
            return;
        }

        if (mode === "compare") {
            const existing = parseComparisonQueries(state.comparisonQuery);
            const nextValues = Array.from(new Set([...existing, savedSearch.query])).slice(0, 2);
            state.comparisonQuery = nextValues.join(", ");
            elements.compareInput.value = state.comparisonQuery;
            setPanelOpen("comparison", true);
            setActiveView("overview");
            renderComparison();
            if (state.query.trim()) {
                await loadComparison();
            }
            return;
        }

        state.strictSearch = Boolean(savedSearch.strict_mode);
        state.discountFromPercent = Math.round(savedSearch.target_discount_percent || 10);
        state.trackerMinDiscountPercent = Math.round(savedSearch.target_discount_percent || 10);
        state.trackerMaxPriceByn = savedSearch.max_price_byn ?? null;
        state.trackerExcludeDuplicates = Boolean(savedSearch.exclude_duplicates);
        state.trackerSellerType = savedSearch.seller_type || "";
        state.trackerCondition = savedSearch.condition || "";
        state.trackerRegionName = savedSearch.region_name || "";
        state.trackerConfigKeyword = savedSearch.config_keyword || "";
        elements.searchInput.value = savedSearch.query;
        state.query = savedSearch.query;
        renderStrictSearch();
        renderDealInputs();
        renderTrackerInputs();
        await search("overview");
    }

    async function applySavedSearchGroup(savedSearches) {
        if (!savedSearches?.length) {
            return;
        }
        const [baseSearch, ...rest] = savedSearches;
        state.strictSearch = Boolean(baseSearch.strict_mode);
        state.discountFromPercent = Math.round(baseSearch.target_discount_percent || 10);
        state.trackerMinDiscountPercent = Math.round(baseSearch.target_discount_percent || 10);
        state.trackerMaxPriceByn = baseSearch.max_price_byn ?? null;
        state.savedSearchGroupName = baseSearch.group_name || "Мои модели";
        state.trackerExcludeDuplicates = Boolean(baseSearch.exclude_duplicates);
        state.trackerSellerType = baseSearch.seller_type || "";
        state.trackerCondition = baseSearch.condition || "";
        state.trackerRegionName = baseSearch.region_name || "";
        state.trackerConfigKeyword = baseSearch.config_keyword || "";
        elements.searchInput.value = baseSearch.query;
        state.query = baseSearch.query;
        state.comparisonQuery = rest.slice(0, 2).map((item) => item.query).join(", ");
        elements.compareInput.value = state.comparisonQuery;
        renderStrictSearch();
        renderDealInputs();
        renderTrackerInputs();
        setPanelOpen("comparison", true);
        await search("overview");
    }

    async function openOpportunityQuery(item) {
        if (!item) {
            return;
        }

        state.strictSearch = Boolean(item.strict_mode);
        state.discountFromPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMinDiscountPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMaxPriceByn = item.max_price_byn ?? null;
        state.trackerExcludeDuplicates = Boolean(item.exclude_duplicates);
        state.trackerSellerType = item.seller_type || "";
        state.trackerCondition = item.condition || "";
        state.trackerRegionName = item.region_name || "";
        state.trackerConfigKeyword = item.config_keyword || "";
        elements.searchInput.value = item.query;
        state.query = item.query;
        renderStrictSearch();
        renderDealInputs();
        renderTrackerInputs();
        await search("overview");
    }

    async function openOpportunityDetail(item) {
        if (!item?.listing) {
            return;
        }

        state.strictSearch = Boolean(item.strict_mode);
        state.discountFromPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMinDiscountPercent = Math.round(item.target_discount_percent || 10);
        state.trackerMaxPriceByn = item.max_price_byn ?? null;
        state.trackerExcludeDuplicates = Boolean(item.exclude_duplicates);
        state.trackerSellerType = item.seller_type || "";
        state.trackerCondition = item.condition || "";
        state.trackerRegionName = item.region_name || "";
        state.trackerConfigKeyword = item.config_keyword || "";
        elements.searchInput.value = item.query;
        state.query = item.query;
        renderStrictSearch();
        renderDealInputs();
        renderTrackerInputs();
        await openListingDetail(item.listing);
    }

    async function deleteTracker(trackerId) {
        if (!trackerId) {
            return;
        }

        try {
            await deleteJson(`/api/v1/trackers/${trackerId}`);
            state.trackerStatus = "Трекер удалён.";
            state.trackerStatusKind = "success";
            renderTrackerStatus();
            await loadTrackers();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось удалить трекер.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function setCurrency(currency) {
        if (!currency || state.currency === currency) {
            return;
        }

        state.currency = currency;

        if (state.query.trim()) {
            clearSearchData();
            state.loading = true;
            renderAll();
            await search(state.activeView);
            await loadOpportunityBoard();
            return;
        }

        renderAll();
        await loadRates();
    }

    async function applyLaunchParams() {
        const params = new URLSearchParams(window.location.search);
        const query = params.get("query")?.trim() || "";
        const view = params.get("view") || "overview";
        if (view && elements.views[view]) {
            setActiveView(view);
        }
        if (!query) {
            renderAll();
            return;
        }
        elements.searchInput.value = query;
        state.query = query;
        await search(view);
    }

    async function search(target = "overview") {
        const query = elements.searchInput.value.trim();
        state.query = query;
        state.error = null;
        state.comparisonStats = null;

        if (!query) {
            clearSearchData();
            focusTarget("overview");
            renderAll();
            return;
        }

        focusTarget(target);
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
            void loadSearchDependencies(requestId);
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
            void loadOpportunityBoard();
        }
    }

    function bindEvents() {
        elements.searchInput?.addEventListener("input", () => {
            state.query = elements.searchInput.value.trim();
            renderLoading();
        });

        elements.searchInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                void search("overview");
            }
        });

        elements.searchButton?.addEventListener("click", () => {
            void search("overview");
        });

        elements.strictSearchToggle?.addEventListener("change", () => {
            state.strictSearch = Boolean(elements.strictSearchToggle.checked);
            renderStrictSearch();
            if (state.query.trim()) {
                void search(state.activeView);
            }
        });

        for (const button of Object.values(elements.currencyButtons || {})) {
            button.addEventListener("click", () => {
                void setCurrency(button.id === "btn-usd" ? "USD" : "BYN");
            });
        }

        for (const chip of elements.quickChips || []) {
            chip.addEventListener("click", () => {
                const query = chip.dataset.query || "";
                elements.searchInput.value = query;
                state.query = query;
                renderLoading();
                void search("overview");
            });
        }

        for (const button of elements.viewTabs || []) {
            button.addEventListener("click", () => {
                const view = button.dataset.view;
                if (!view) {
                    return;
                }
                setActiveView(view);
                renderAll();
                if (view === "trackers") {
                    void loadTrackers();
                    void loadLeads();
                    void loadWatchlist();
                    void loadSavedSearches();
                    void loadOpportunityBoard();
                }
            });
        }

        for (const button of elements.summaryActions || []) {
            button.addEventListener("click", () => {
                focusTarget(button.dataset.summaryTarget || "overview");
            });
        }

        for (const button of elements.panelToggles || []) {
            button.addEventListener("click", () => {
                const panelName = button.dataset.panelToggle;
                if (!panelName) {
                    return;
                }
                setPanelOpen(panelName, !state.panels[panelName]);
            });
        }

        for (const button of elements.historyRangeButtons || []) {
            button.addEventListener("click", () => {
                const nextDays = Number(button.dataset.historyDays);
                if (!nextDays || nextDays === state.historyDays) {
                    return;
                }
                state.historyDays = nextDays;
                renderHistory();
                void loadHistory();
            });
        }

        elements.compareInput?.addEventListener("input", () => {
            state.comparisonQuery = elements.compareInput.value;
            renderComparison();
        });

        elements.compareInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                state.comparisonQuery = elements.compareInput.value;
                void loadComparison();
            }
        });

        elements.compareButton?.addEventListener("click", () => {
            state.comparisonQuery = elements.compareInput.value;
            void loadComparison();
        });

        elements.compareSwapButton?.addEventListener("click", () => {
            void swapComparisonQueries();
        });

        for (const chip of elements.compareQuickChips || []) {
            chip.addEventListener("click", () => {
                const query = chip.dataset.compareQuery || "";
                const existing = parseComparisonQueries(state.comparisonQuery);
                const nextValues = Array.from(new Set([...existing, query])).slice(0, 2);
                state.comparisonQuery = nextValues.join(", ");
                elements.compareInput.value = state.comparisonQuery;
                setPanelOpen("comparison", true);
                renderComparison();
                void loadComparison();
            });
        }

        for (const button of elements.sortButtons || []) {
            button.addEventListener("click", () => {
                const sort = button.dataset.sort || "newest";
                if (sort === state.sort) {
                    return;
                }
                state.sort = sort;
                renderSortButtons();
                setActiveView("ads");
                if (state.query.trim()) {
                    void loadListings();
                }
            });
        }

        for (const button of elements.discountButtons || []) {
            button.addEventListener("click", () => {
                const from = Number(button.dataset.discountFrom);
                const to = Number(button.dataset.discountTo);
                if (!Number.isFinite(from) || !Number.isFinite(to)) {
                    return;
                }
                state.discountFromPercent = Math.min(from, to);
                state.discountToPercent = Math.max(from, to);
                renderDiscountButtons();
                renderDealInputs();
                setActiveView("deals");
                if (state.query.trim()) {
                    void loadDeals();
                }
            });
        }

        elements.dealApplyButton?.addEventListener("click", () => {
            const from = Math.abs(Number(elements.dealFromInput?.value || state.discountFromPercent));
            const to = Math.abs(Number(elements.dealToInput?.value || state.discountToPercent));
            state.discountFromPercent = Math.min(from, to);
            state.discountToPercent = Math.max(from, to);
            renderDiscountButtons();
            renderDealInputs();
            setActiveView("deals");
            if (state.query.trim()) {
                void loadDeals();
            }
        });

        elements.trackQueryButton?.addEventListener("click", () => {
            void createTracker();
        });

        elements.reloadTrackersButton?.addEventListener("click", () => {
            void loadTrackers();
        });

        elements.reloadLeadsButton?.addEventListener("click", () => {
            void loadLeads();
        });

        elements.refreshWatchlistButton?.addEventListener("click", () => {
            void refreshWatchlist();
        });

        elements.saveSearchButton?.addEventListener("click", () => {
            void saveCurrentSearch();
        });

        elements.reloadOpportunityBoardButton?.addEventListener("click", () => {
            void loadOpportunityBoard();
        });

        elements.savedSearchGroupInput?.addEventListener("input", () => {
            state.savedSearchGroupName = elements.savedSearchGroupInput.value.trim() || "Мои модели";
        });

        elements.trackerMinDiscountInput?.addEventListener("input", () => {
            const nextValue = Number(elements.trackerMinDiscountInput.value);
            state.trackerMinDiscountPercent = Number.isFinite(nextValue) ? Math.abs(nextValue) : 10;
        });

        elements.trackerMaxPriceInput?.addEventListener("input", () => {
            const rawValue = elements.trackerMaxPriceInput.value.trim();
            if (!rawValue) {
                state.trackerMaxPriceByn = null;
                return;
            }
            const nextValue = Number(rawValue);
            state.trackerMaxPriceByn = Number.isFinite(nextValue) ? Math.abs(nextValue) : null;
        });

        elements.trackerExcludeDuplicatesToggle?.addEventListener("change", () => {
            state.trackerExcludeDuplicates = Boolean(elements.trackerExcludeDuplicatesToggle.checked);
        });

        elements.trackerSellerSelect?.addEventListener("change", () => {
            state.trackerSellerType = elements.trackerSellerSelect.value;
        });

        elements.trackerConditionSelect?.addEventListener("change", () => {
            state.trackerCondition = elements.trackerConditionSelect.value;
        });

        elements.trackerRegionInput?.addEventListener("input", () => {
            state.trackerRegionName = elements.trackerRegionInput.value.trim();
        });

        elements.trackerConfigInput?.addEventListener("input", () => {
            state.trackerConfigKeyword = elements.trackerConfigInput.value.trim();
        });

        for (const button of elements.trackerEventFilterButtons || []) {
            button.addEventListener("click", () => {
                state.trackerEventFilter = button.dataset.eventFilter || "all";
                renderTrackerEventFilters();
                renderTrackerEvents();
            });
        }

        for (const button of elements.leadFilterButtons || []) {
            button.addEventListener("click", () => {
                state.leadFilter = button.dataset.leadFilter || "all";
                renderLeads();
            });
        }

        for (const button of elements.watchlistFilterButtons || []) {
            button.addEventListener("click", () => {
                state.watchlistFilter = button.dataset.watchFilter || "all";
                renderWatchlist();
            });
        }

        elements.detailClose?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailOverlay?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailAddLeadButton?.addEventListener("click", () => {
            if (state.detail) {
                void addLeadFromListing(state.detail, "detail_modal", state.detail.query || state.query);
            }
        });

        elements.detailAddWatchlistButton?.addEventListener("click", () => {
            if (state.detail) {
                void addWatchlistFromListing(state.detail, state.detail.query || state.query);
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && state.detail) {
                closeDetailModal();
            }
        });
    }

    return {
        bindEvents,
        search,
        loadListings,
        loadDeals,
        loadRates,
        loadHistory,
        loadComparison,
        swapComparisonQueries,
        loadTrackers,
        loadLeads,
        loadWatchlist,
        loadSavedSearches,
        loadOpportunityBoard,
        createTracker,
        addLeadFromListing,
        addWatchlistFromListing,
        updateLeadStatus,
        updateLeadMeta,
        updateWatchlistStatus,
        updateWatchlistMeta,
        promoteWatchlistToLead,
        deleteWatchlistItem,
        refreshWatchlist,
        deleteTracker,
        saveCurrentSearch,
        deleteSavedSearch,
        applySavedSearch,
        applySavedSearchGroup,
        openOpportunityQuery,
        openOpportunityDetail,
        openListingDetail,
        setCurrency,
        applyLaunchParams,
    };
}
