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
        renderDetailModal,
        closeDetailModal,
        setPanelOpen,
        showToast,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
        renderProfitDashboard,
        renderPipelineStepper,
        renderVelocity,
        renderDetailRisks,
        renderMonitoringHeroStats,
        renderDealsHeroStats,
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
        if (target === "cheap") {
            setActiveView("cheap");
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
        try {
            state.leads = await getJson("/api/v1/leads");
        } catch (_) {
            state.leads = [];
        } finally {
            renderLeads();
        }
    }

    async function clearAllLeads() {
        if (!state.leads.length) {
            showToast("Список уже пуст");
            return;
        }
        try {
            await deleteJson("/api/v1/leads/all");
            showToast(`Удалено ${state.leads.length} сделок`);
            state.leads = [];
            renderLeads();
            renderDealsHeroStats();
        } catch (error) {
            showToast(error.message || "Не удалось очистить");
        }
    }

    async function loadWatchlist() {
        if (!hasTelegramInitData()) {
            state.watchlist = [];
            renderWatchlist();
            return;
        }
        try {
            state.watchlist = await getJson("/api/v1/watchlist");
        } catch (_) {
            state.watchlist = [];
        } finally {
            renderWatchlist();
        }
    }

    async function createTracker() {
        if (!hasTelegramInitData()) {
            showToast("Доступно только в Telegram");
            return;
        }

        const query = state.query.trim();
        if (!query) {
            showToast("Сначала введите запрос");
            return;
        }

        const normalizedQuery = query.toLocaleLowerCase("ru-RU");
        const duplicate = state.trackers.find(
            (t) => t.query.trim().toLocaleLowerCase("ru-RU") === normalizedQuery
        );
        if (duplicate) {
            showToast("Такой трекер уже существует");
            return;
        }

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
            showToast("Трекер добавлен");
            await loadTrackers();
            renderAll();
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

        // Check if already in leads
        const alreadyInLeads = state.leads.some((l) => l.ad_id === item.ad_id);
        if (alreadyInLeads) {
            showToast("Уже в покупках");
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
                thumbnail: item.thumbnail || null,
            });
            showToast("Добавлено в покупки");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось добавить в покупки");
        }
    }

    async function addWatchlistFromListing(item, queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }

        // Check if already in watchlist
        const alreadyInWatchlist = state.watchlist.some((w) => w.ad_id === item.ad_id);
        if (alreadyInWatchlist) {
            showToast("Уже в избранном");
            return;
        }

        try {
            await postJson("/api/v1/watchlist", {
                query: queryOverride || state.query || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
                thumbnail: item.thumbnail || null,
                market_median_byn: state.stats?.median
                    ? (state.currency === "USD" ? Number(state.stats.median) * (state.usdRateByn || 1) : Number(state.stats.median))
                    : null,
            });
            showToast("Добавлено в избранное");
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось добавить в избранное");
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
            showToast(error.message || "Не удалось обновить");
        }
    }

    async function deleteLead(leadId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}`);
            showToast("Сделка удалена");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        }
    }

    async function markLeadAsBought(lead) {
        await updateLeadMeta(lead.id, { status: "bought" });
        showToast("Сделка отмечена как купленная");
    }

    async function cancelLead(leadId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}`);
            showToast("Сделка отменена");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось отменить");
        }
    }

    async function markLeadAsSold(lead, priceNum) {
        if (!priceNum || !Number.isFinite(priceNum) || priceNum <= 0) {
            showToast("Введите корректную цену");
            return;
        }
        await updateLeadMeta(lead.id, {
            status: "sold",
            sold_price_byn: priceNum,
        });
        const profit = priceNum - (lead.price_byn || 0);
        const profitSign = profit >= 0 ? "+" : "";
        showToast(`Сделка продана! Прибыль: ${profitSign}${Math.round(profit)} BYN`);
    }

    async function openLeadDetail(lead) {
        if (!lead?.ad_id) {
            showToast("Не удалось открыть: нет ID объявления");
            return;
        }
        const queryToUse = lead.query || state.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        showToast("Загружаю...");
        state.error = null;
        renderError();
        try {
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.currency}&strict_search=${state.strictSearch}&ad_id=${lead.ad_id}`
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
            showToast(error.message || "Не удалось обновить");
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
                thumbnail: item.thumbnail || null,
                flip_estimates: [],
            },
            "watchlist",
            item.query
        );
        await updateWatchlistMeta(item.id, { workflow_status: "in_progress" });
    }

    async function openWatchlistDetail(item) {
        if (!item?.ad_id) {
            return;
        }
        const queryToUse = item.query || state.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        showToast("Загружаю...");
        state.error = null;
        renderError();
        try {
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.currency}&strict_search=${state.strictSearch}&ad_id=${item.ad_id}`
            );
            state.detail = fullDetail;
            state.detailImageIndex = 0;
            state.detailFromWatchlist = true;
            renderDetailModal();
        } catch (error) {
            state.error = error.message || "Не удалось загрузить детали";
            renderError();
        }
    }

    async function deleteWatchlistItem(watchlistId) {
        try {
            await deleteJson(`/api/v1/watchlist/${watchlistId}`);
            showToast("Удалено из избранного");
            await loadWatchlist();
            renderMonitoringHeroStats();
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        }
    }

    async function deleteAllWatchlist() {
        if (!state.watchlist.length) {
            showToast("Список уже пуст");
            return;
        }
        try {
            await deleteJson("/api/v1/watchlist/all");
            showToast(`Удалено ${state.watchlist.length} лотов`);
            state.watchlist = [];
            renderWatchlist();
            renderMonitoringHeroStats();
        } catch (error) {
            showToast(error.message || "Не удалось очистить");
        }
    }

    async function refreshWatchlist() {
        try {
            const payload = await postJson("/api/v1/watchlist/refresh", {});
            const parts = [`${payload.updated} проверено`, `${payload.price_drops} падений цены`];
            if (payload.missing > 0) {
                parts.push(`${payload.missing} пропало`);
            }
            if (payload.auto_removed > 0) {
                parts.push(`${payload.auto_removed} удалено (устарело)`);
            }
            showToast(`Обновлено: ${parts.join(", ")}`);
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось обновить цены");
        }
    }

    async function refreshLeads() {
        try {
            const payload = await postJson("/api/v1/leads/refresh", {});
            const parts = [`${payload.checked} проверено`, `${payload.active} активно`];
            if (payload.missing > 0) {
                parts.push(`${payload.missing} пропало`);
            }
            showToast(`Покупки: ${parts.join(", ")}`);
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось проверить покупки");
        }
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
            showToast("Трекер удалён");
            await loadTrackers();
            renderAll();
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
            void loadMarketVelocity();
        }
    }

    // ===== Expenses Actions =====
    async function loadExpenses(leadId) {
        try {
            state.expenses = await getJson(`/api/v1/leads/${leadId}/expenses`);
            renderExpensesModal();
        } catch (_) {
            state.expenses = [];
            renderExpensesModal();
        }
    }

    async function createExpense(leadId, payload) {
        try {
            await postJson(`/api/v1/leads/${leadId}/expenses`, payload);
            showToast("Расход добавлен");
            await loadExpenses(leadId);
        } catch (error) {
            showToast(error.message || "Не удалось добавить расход");
        }
    }

    async function deleteExpense(leadId, expenseId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}/expenses/${expenseId}`);
            showToast("Расход удалён");
            await loadExpenses(leadId);
        } catch (error) {
            showToast(error.message || "Не удалось удалить расход");
        }
    }

    // ===== CSV Export =====
    async function exportLeadsCSV() {
        try {
            const initData = window.Telegram?.WebApp?.initData;
            const headers = initData ? { "X-Telegram-Init-Data": initData } : {};
            const response = await fetch("/api/v1/leads/export?format=csv", {
                headers,
            });
            if (!response.ok) {
                throw new Error("Не удалось экспортировать данные");
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "leads_export.csv";
            a.click();
            URL.revokeObjectURL(url);
            showToast("Файл загружен");
        } catch (error) {
            showToast(error.message || "Не удалось экспортировать");
        }
    }

    // ===== Market Velocity =====
    async function loadMarketVelocity() {
        if (!state.query) {
            renderVelocity(null);
            return;
        }
        try {
            renderVelocity(null);
        } catch (_) {
            renderVelocity(null);
        }
    }

    // ===== Detail Risk Assessment =====
    async function loadDetailRisks(item) {
        if (!item || !item.price) {
            renderDetailRisks(null);
            return;
        }
        try {
            const data = await postJson("/api/v1/risk-assessment", {
                price_byn: item.price_byn || item.price || null,
                description: item.description || "",
                photo_count: item.photo_count || 0,
                market_median: state.stats?.median || null,
            });
            renderDetailRisks(data);
        } catch (_) {
            renderDetailRisks(null);
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
                if (view === "tracking") {
                    void loadTrackers();
                }
                if (view === "monitoring") {
                    void loadWatchlist();
                }
                if (view === "deals") {
                    void loadLeads();
                }
                if (view === "cheap") {
                    if (state.query.trim()) {
                        void loadDeals();
                    }
                }
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
                elements.compareInput.value = query;
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
                setActiveView("cheap");
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
            setActiveView("cheap");
            if (state.query.trim()) {
                void loadDeals();
            }
        });

        elements.trackQueryButton?.addEventListener("click", () => {
            void createTracker();
        });

        elements.clearEventsButton?.addEventListener("click", () => {
            void (async () => {
                try {
                    await deleteJson("/api/v1/tracker-events");
                } catch (_) {
                    // ignore — clear locally anyway
                }
                state.trackerEvents = [];
                showToast("События очищены");
                renderTrackerEvents();
            })();
        });

        elements.refreshLeadsButton?.addEventListener("click", () => {
            void refreshLeads();
        });

        let clearLeadsConfirmed = false;
        elements.clearAllLeadsButton?.addEventListener("click", () => {
            void (async () => {
                if (state.leads.length === 0) {
                    showToast("Список уже пуст");
                    return;
                }
                if (!clearLeadsConfirmed) {
                    clearLeadsConfirmed = true;
                    elements.clearAllLeadsButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    setTimeout(() => {
                        clearLeadsConfirmed = false;
                        if (elements.clearAllLeadsButton) {
                            elements.clearAllLeadsButton.textContent = "Очистить";
                        }
                    }, 3000);
                    return;
                }
                clearLeadsConfirmed = false;
                if (elements.clearAllLeadsButton) {
                    elements.clearAllLeadsButton.textContent = "Очистить";
                }
                await clearAllLeads();
            })();
        });

        let clearWatchlistConfirmed = false;
        elements.deleteAllWatchlistButton?.addEventListener("click", () => {
            void (async () => {
                if (state.watchlist.length === 0) {
                    showToast("Список уже пуст");
                    return;
                }
                if (!clearWatchlistConfirmed) {
                    clearWatchlistConfirmed = true;
                    elements.deleteAllWatchlistButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    setTimeout(() => {
                        clearWatchlistConfirmed = false;
                        if (elements.deleteAllWatchlistButton) {
                            elements.deleteAllWatchlistButton.textContent = "Очистить";
                        }
                    }, 3000);
                    return;
                }
                clearWatchlistConfirmed = false;
                if (elements.deleteAllWatchlistButton) {
                    elements.deleteAllWatchlistButton.textContent = "Очистить";
                }
                try {
                    await deleteJson("/api/v1/watchlist/all");
                    showToast(`Удалено ${state.watchlist.length} лотов`);
                    state.watchlist = [];
                    renderWatchlist();
                    renderMonitoringHeroStats();
                } catch (error) {
                    showToast(error.message || "Не удалось очистить");
                }
            })();
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

        // Swipe support for detail modal photos
        let touchStartX = 0;
        let touchEndX = 0;
        let isSwiping = false;
        elements.detailModal?.addEventListener("touchstart", (e) => {
            touchStartX = e.changedTouches[0].screenX;
            isSwiping = false;
        }, { passive: true });
        elements.detailModal?.addEventListener("touchend", (e) => {
            if (isSwiping) return;
            touchEndX = e.changedTouches[0].screenX;
            const swipeDistance = touchStartX - touchEndX;
            if (Math.abs(swipeDistance) > 50 && state.detail?.images?.length > 1) {
                isSwiping = true;
                const mediaEl = elements.detailModal?.querySelector(".detail-media");
                if (mediaEl) {
                    mediaEl.classList.add("swipe-anim");
                    setTimeout(() => {
                        if (swipeDistance > 0) {
                            state.detailImageIndex = Math.min(state.detail.images.length - 1, state.detailImageIndex + 1);
                        } else {
                            state.detailImageIndex = Math.max(0, state.detailImageIndex - 1);
                        }
                        renderDetailModal();
                        requestAnimationFrame(() => {
                            setTimeout(() => {
                                mediaEl.classList.remove("swipe-anim");
                                isSwiping = false;
                            }, 50);
                        });
                    }, 150);
                }
            }
        }, { passive: true });

        // Keyboard arrow navigation for photos
        document.addEventListener("keydown", (event) => {
            if (!state.detail || !(state.detail?.images?.length > 1)) return;
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                const mediaEl = elements.detailModal?.querySelector(".detail-media");
                if (mediaEl) {
                    mediaEl.classList.add("swipe-anim");
                    setTimeout(() => {
                        if (event.key === "ArrowLeft") {
                            state.detailImageIndex = Math.max(0, state.detailImageIndex - 1);
                        } else {
                            state.detailImageIndex = Math.min(state.detail.images.length - 1, state.detailImageIndex + 1);
                        }
                        renderDetailModal();
                        requestAnimationFrame(() => {
                            setTimeout(() => mediaEl.classList.remove("swipe-anim"), 50);
                        });
                    }, 150);
                }
            }
        });

        // Fix for Telegram Mini App mobile: intercept external links and open them properly
        // On mobile, target="_blank" doesn't work correctly in the webview
        document.addEventListener("click", (event) => {
            const link = event.target.closest("a[target='_blank']");
            if (link && link.href && !link.href.startsWith("#") && !link.href.startsWith("javascript:")) {
                event.preventDefault();
                event.stopPropagation();

                // Use Telegram WebApp API for mobile, fallback to window.open for desktop
                if (window.Telegram?.WebApp?.openLink) {
                    window.Telegram.WebApp.openLink(link.href);
                } else {
                    window.open(link.href, "_blank", "noopener,noreferrer");
                }
            }
        });

        // ===== Expenses Modal Events =====
        elements.expensesClose?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.expensesOverlay?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.saveExpenseButton?.addEventListener("click", () => {
            const leadId = state.currentExpenseLeadId;
            if (!leadId) return;
            const type = elements.expenseTypeSelect?.value || "other";
            const rawAmount = elements.expenseAmountInput?.value?.trim();
            const amount = rawAmount ? Number(rawAmount) : null;
            const notes = elements.expenseNotesInput?.value?.trim() || "";
            if (!amount || amount <= 0) {
                showToast("Введите корректную сумму");
                return;
            }
            void actions.createExpense(leadId, { expense_type: type, amount_byn: amount, notes });
            if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
            if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
        });

        elements.cancelExpenseButton?.addEventListener("click", () => {
            closeExpensesModal();
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !elements.expensesModal?.hidden) {
                closeExpensesModal();
            }
        });

        // ===== CSV Export =====
        elements.exportLeadsButton?.addEventListener("click", () => {
            void actions.exportLeadsCSV();
        });

        // ===== Profit Dashboard =====
        elements.reloadProfitButton?.addEventListener("click", () => {
            void loadLeads();
            renderProfitDashboard();
            renderPipelineStepper();
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
        clearAllLeads,
        deleteLead,
        markLeadAsBought,
        cancelLead,
        markLeadAsSold,
        openLeadDetail,
        loadWatchlist,
        createTracker,
        addLeadFromListing,
        addWatchlistFromListing,
        updateLeadStatus,
        updateLeadMeta,
        updateWatchlistStatus,
        updateWatchlistMeta,
        promoteWatchlistToLead,
        deleteWatchlistItem,
        deleteAllWatchlist,
        refreshWatchlist,
        refreshLeads,
        openWatchlistDetail,
        deleteTracker,
        openOpportunityQuery,
        openOpportunityDetail,
        openListingDetail,
        setCurrency,
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeadsCSV,
        loadMarketVelocity,
        loadDetailRisks,
    };
}
