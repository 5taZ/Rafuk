function createAppActions(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        supportsTelegramVersion,
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
        renderOpportunityBoard,
        renderDetailModal,
        closeDetailModal,
        setPanelOpen,
        showToast,
    } = context;

    // nginx closes the proxied request after 60 s (proxy_read_timeout), so waiting longer
    // only turns into a 504. The client gives up first and shows its own message.
    const DEFAULT_TIMEOUT_MS = 55000;
    // Watchlist refresh and the opportunity board: the API bounds them with its own deadline
    // and returns partial results (skipped / partial), so wait for that answer until just
    // before nginx would cut the request.
    const LONG_TIMEOUT_MS = 58000;
    const BOARD_TTL_MS = 60000;
    const DEFAULT_MIN_DISCOUNT_PERCENT = 10;
    const TELEGRAM_ONLY_MESSAGE = "Доступно только в Telegram";
    const VIEW_ALIASES = { trackers: "tracking" };
    const SEARCH_REQUEST_KINDS = [
        "stats",
        "history",
        "segments",
        "geography",
        "listings",
        "deals",
        "comparison",
        "detail",
    ];
    const FIELD_LABELS = {
        query: "запрос",
        base_query: "запрос",
        compare_query: "запрос для сравнения",
        currency: "валюта",
        days: "период",
        sort: "сортировка",
        ad_id: "объявление",
        title: "название",
        link: "ссылка",
        status: "статус",
        workflow_status: "статус",
        notes: "заметка",
        price_byn: "цена",
        target_resale_byn: "цель перепродажи",
        target_price_byn: "целевая цена",
        min_discount_percent: "мин. скидка",
        max_price_byn: "макс. цена",
        discount_from_percent: "скидка от",
        discount_to_percent: "скидка до",
        interval_min: "интервал",
        region_name: "регион",
        config_keyword: "ключевое слово",
        seller_type: "продавец",
        condition: "состояние",
    };
    const KNOWN_DETAILS = {
        "Listing not found for this query": "Объявление уже снято с продажи.",
        "A tracker with this configuration already exists": "Такой трекер уже существует.",
        "Query must not be empty": "Сначала введите запрос.",
    };

    // Per-kind request bookkeeping: a newer request of the same kind aborts the
    // previous one and only the latest token may write its response into state.
    // Kinds listed in state.pending also flag their section as loading while in flight.
    const requestTokens = {};
    const requestControllers = {};
    let boardInFlight = null;
    let boardLoadedAt = 0;
    let lastComparisonKey = "";

    function telegramHeaders() {
        const initData = window.Telegram?.WebApp?.initData;
        return initData ? { "X-Telegram-Init-Data": initData } : {};
    }

    function hasCyrillic(value) {
        return /[а-яё]/i.test(String(value || ""));
    }

    function describeValidationErrors(detail) {
        const fields = [];
        for (const entry of detail) {
            const loc = Array.isArray(entry?.loc) ? entry.loc : [];
            const field = [...loc].reverse().find((part) => typeof part === "string" && part !== "body");
            if (!field) {
                continue;
            }
            const label = FIELD_LABELS[field] || field;
            if (!fields.includes(label)) {
                fields.push(label);
            }
        }
        if (!fields.length) {
            return "Проверьте введённые данные.";
        }
        return `Проверьте введённые данные: ${fields.join(", ")}.`;
    }

    function statusMessage(status) {
        if (status === 400) return "Некорректный запрос.";
        if (status === 401 || status === 403) {
            return "Сессия Telegram устарела. Перезапустите Mini App.";
        }
        if (status === 404) return "Данные не найдены. Обновите экран.";
        if (status === 409) return "Такая запись уже существует.";
        if (status === 422) return "Проверьте введённые данные.";
        if (status === 429) return "Слишком много запросов. Подождите минуту и попробуйте снова.";
        if (status === 502) return "Kufar временно недоступен. Попробуйте позже.";
        if (status === 504) return "Сервер не успел ответить. Попробуйте позже.";
        if (status >= 500) return "Сервер временно недоступен. Попробуйте позже.";
        return "Не удалось выполнить запрос.";
    }

    function errorMessageFor(status, payload) {
        const detail = payload?.detail;
        if (status === 401 || status === 403) {
            return statusMessage(status);
        }
        if (Array.isArray(detail)) {
            return describeValidationErrors(detail);
        }
        if (typeof detail === "string" && detail.trim()) {
            const text = detail.trim();
            if (KNOWN_DETAILS[text]) {
                return KNOWN_DETAILS[text];
            }
            if (hasCyrillic(text)) {
                return text;
            }
        }
        return statusMessage(status);
    }

    function createRequestError(message, extra = {}) {
        const error = new Error(message);
        Object.assign(error, extra);
        return error;
    }

    async function requestJson(url, options = {}) {
        const { timeoutMs = DEFAULT_TIMEOUT_MS, signal: externalSignal, ...fetchOptions } = options;
        const headers = {
            ...telegramHeaders(),
            ...(fetchOptions.headers || {}),
        };
        const controller = typeof AbortController === "function" ? new AbortController() : null;
        let timedOut = false;
        let timer = null;
        const abortFromCaller = () => controller?.abort();
        if (controller) {
            if (externalSignal?.aborted) {
                controller.abort();
            } else {
                externalSignal?.addEventListener("abort", abortFromCaller);
            }
            if (timeoutMs > 0) {
                timer = setTimeout(() => {
                    timedOut = true;
                    controller.abort();
                }, timeoutMs);
            }
        }

        // Fetch and body errors share one translation: a timeout or an abort can also
        // interrupt reading the body, and anything else there means the body is not JSON.
        const transportError = (error, fallback) => {
            if (timedOut) {
                return createRequestError("Сервер не ответил вовремя. Попробуйте ещё раз.", {
                    timeout: true,
                });
            }
            if (error?.name === "AbortError" || controller?.signal.aborted) {
                return createRequestError("Запрос отменён.", { aborted: true });
            }
            return fallback;
        };

        try {
            let response;
            try {
                response = await fetch(url, {
                    ...fetchOptions,
                    headers,
                    signal: controller?.signal,
                });
            } catch (error) {
                throw transportError(
                    error,
                    createRequestError(
                        "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.",
                        { network: true }
                    )
                );
            }

            if (!response.ok) {
                let payload = null;
                try {
                    payload = await response.json();
                } catch (_) {
                    payload = null;
                }
                throw createRequestError(errorMessageFor(response.status, payload), {
                    status: response.status,
                });
            }

            if (response.status === 204) {
                return null;
            }

            try {
                return await response.json();
            } catch (error) {
                // E.g. a proxy or captive portal answering 200 with an HTML page.
                throw transportError(
                    error,
                    createRequestError("Сервер вернул некорректный ответ. Попробуйте позже.", {
                        invalidJson: true,
                        status: response.status,
                    })
                );
            }
        } finally {
            if (timer) {
                clearTimeout(timer);
            }
            externalSignal?.removeEventListener("abort", abortFromCaller);
        }
    }

    function getJson(url, options = {}) {
        return requestJson(url, options);
    }

    function postJson(url, payload, options = {}) {
        return requestJson(url, {
            ...options,
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    function deleteJson(url) {
        return requestJson(url, { method: "DELETE" });
    }

    function beginRequest(kind) {
        requestControllers[kind]?.abort();
        const controller = typeof AbortController === "function" ? new AbortController() : null;
        requestControllers[kind] = controller;
        requestTokens[kind] = (requestTokens[kind] || 0) + 1;
        setPending(kind, true);
        return { token: requestTokens[kind], signal: controller?.signal };
    }

    function isCurrentRequest(kind, token) {
        return requestTokens[kind] === token;
    }

    function endRequest(kind, token) {
        if (!isCurrentRequest(kind, token)) {
            return;
        }
        requestControllers[kind] = null;
        setPending(kind, false);
    }

    function cancelRequest(kind) {
        requestControllers[kind]?.abort();
        requestControllers[kind] = null;
        requestTokens[kind] = (requestTokens[kind] || 0) + 1;
        setPending(kind, false);
    }

    function isPending(kind) {
        return Boolean(state.pending[kind]);
    }

    function setPending(kind, value) {
        if (kind in state.pending) {
            state.pending[kind] = Boolean(value);
        }
    }

    function buildCommonQuery(params = {}, overrides = {}) {
        const query = new URLSearchParams({
            query: overrides.query ?? state.activeQuery,
            currency: state.currency,
            strict_search: String(overrides.strictSearch ?? state.strictSearch),
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
        state.listingsTruncated = false;
        state.dealsTruncated = false;
        state.comparisonStats = null;
        state.comparisonItems = [];
        // An open card belongs to the previous search: close it properly (visibility,
        // inert background, focus) instead of leaving a modal whose controls see no detail.
        if (state.detail || !elements.detailModal?.hidden) {
            cancelRequest("detail");
            closeDetailModal();
        }
        state.detail = null;
        state.detailImageIndex = 0;
    }

    // Kufar pagination may stop early; contract: responses carry truncated=true then.
    function isTruncated(payload) {
        return Boolean(payload?.truncated);
    }

    function isActiveRequest(requestId) {
        return requestId === state.searchRequestId;
    }

    function listingsUrl() {
        return `/api/v1/listings?${buildCommonQuery({ sort: state.sort })}`;
    }

    function dealsUrl() {
        return `/api/v1/listings?${buildCommonQuery({
            sort: "cheap",
            discount_from_percent: state.discountFromPercent,
            discount_to_percent: state.discountToPercent,
        })}`;
    }

    function applyListings(payload) {
        state.listings = payload?.listings || [];
        state.listingsTruncated = isTruncated(payload);
        state.loadErrors.listings = null;
    }

    function failListings(error) {
        state.listings = [];
        state.listingsTruncated = false;
        state.loadErrors.listings = error.message || "Не удалось загрузить объявления.";
    }

    function applyDeals(payload) {
        state.dealListings = payload?.listings || [];
        state.dealsTruncated = isTruncated(payload);
        state.loadErrors.deals = null;
    }

    function failDeals(error) {
        state.dealListings = [];
        state.dealsTruncated = false;
        state.loadErrors.deals = error.message || "Не удалось загрузить дешёвые объявления.";
    }

    // Runs one request of the given kind; apply/fail run only while it is the latest one.
    // onStart runs right after the request is registered (and marked pending).
    async function runTrackedRequest(kind, url, { apply, fail, timeoutMs, onStart } = {}) {
        const { token, signal } = beginRequest(kind);
        onStart?.();
        let payload;
        try {
            payload = await getJson(url, { signal, timeoutMs });
        } catch (error) {
            if (!isCurrentRequest(kind, token)) {
                return false;
            }
            endRequest(kind, token);
            fail?.(error);
            return true;
        }
        if (!isCurrentRequest(kind, token)) {
            return false;
        }
        endRequest(kind, token);
        apply?.(payload);
        return true;
    }

    function loadSearchDependencies(requestId) {
        const dependencies = [
            {
                kind: "history",
                url: `/api/v1/price-history?${buildCommonQuery({ days: state.historyDays })}`,
                apply(payload) {
                    state.history = payload?.points || [];
                },
                fail() {
                    state.history = [];
                },
            },
            {
                kind: "segments",
                url: `/api/v1/segments?${buildCommonQuery()}`,
                apply(payload) {
                    state.segments = payload;
                },
                fail() {
                    state.segments = null;
                },
            },
            {
                kind: "geography",
                url: `/api/v1/geography?${buildCommonQuery()}`,
                apply(payload) {
                    state.geography = payload?.regions || [];
                },
                fail() {
                    state.geography = [];
                },
            },
            {
                kind: "listings",
                url: listingsUrl(),
                apply: applyListings,
                fail: failListings,
            },
            {
                kind: "deals",
                url: dealsUrl(),
                apply: applyDeals,
                fail: failDeals,
            },
        ];

        for (const dependency of dependencies) {
            void runTrackedRequest(dependency.kind, dependency.url, {
                apply(payload) {
                    if (!isActiveRequest(requestId)) {
                        return;
                    }
                    dependency.apply(payload);
                    renderAll();
                },
                fail(error) {
                    if (!isActiveRequest(requestId)) {
                        return;
                    }
                    dependency.fail(error);
                    renderAll();
                },
            });
        }
    }

    function normalizeView(view) {
        const value = String(view || "").trim().toLowerCase();
        return VIEW_ALIASES[value] || value;
    }

    function isKnownView(view) {
        return Boolean(view) && Object.prototype.hasOwnProperty.call(elements.views, view);
    }

    function setActiveView(view) {
        if (!isKnownView(view)) {
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
        const view = normalizeView(target);
        if (isKnownView(view)) {
            setActiveView(view);
            return;
        }

        setActiveView("overview");

        if (view === "history") {
            setPanelOpen("history", true);
            scrollSectionIntoView(elements.historySection);
        } else if (view === "comparison") {
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

    function comparisonKey(compareQueries) {
        return JSON.stringify([
            normalizedQuery(state.activeQuery),
            compareQueries.map(normalizedQuery),
            state.currency,
            state.strictSearch,
        ]);
    }

    function telegramOnly() {
        if (hasTelegramInitData()) {
            return false;
        }
        showToast(TELEGRAM_ONLY_MESSAGE);
        return true;
    }

    function confirmAction(message) {
        const webApp = window.Telegram?.WebApp;
        if (webApp?.showConfirm && supportsTelegramVersion("6.2")) {
            return new Promise((resolve) => {
                try {
                    webApp.showConfirm(message, (confirmed) => resolve(Boolean(confirmed)));
                } catch (_) {
                    resolve(window.confirm(message));
                }
            });
        }
        return Promise.resolve(window.confirm(message));
    }

    function parseMinDiscount(rawValue) {
        const text = String(rawValue ?? "").trim();
        if (!text) {
            return DEFAULT_MIN_DISCOUNT_PERCENT;
        }
        const value = Number(text);
        if (!Number.isFinite(value)) {
            return DEFAULT_MIN_DISCOUNT_PERCENT;
        }
        return Math.min(100, Math.max(0, Math.abs(value)));
    }

    function emptyOpportunityBoard() {
        return {
            currency: null,
            partial: false,
            items: [],
            top_price_drops: [],
            rare_opportunities: [],
            market_signals: [],
        };
    }

    async function loadRates() {
        try {
            const payload = await getJson("/api/v1/currency-rates");
            state.usdRateByn = Number(payload?.rates?.USD || 0) || null;
            state.rateSource = payload?.source || null;
        } catch (_) {
            state.usdRateByn = null;
            state.rateSource = null;
        } finally {
            renderAll();
        }
    }

    async function loadHistory() {
        if (!state.activeQuery) {
            cancelRequest("history");
            state.history = [];
            renderHistory();
            return;
        }

        await runTrackedRequest(
            "history",
            `/api/v1/price-history?${buildCommonQuery({ days: state.historyDays })}`,
            {
                onStart: renderHistory,
                apply(payload) {
                    state.history = payload?.points || [];
                    renderHistory();
                },
                fail() {
                    state.history = [];
                    renderHistory();
                },
            }
        );
    }

    async function loadListings() {
        if (!state.activeQuery) {
            cancelRequest("listings");
            state.listings = [];
            state.loadErrors.listings = null;
            renderAll();
            return;
        }
        if (state.searchError) {
            // Without stats the list cannot be shown: rerun the whole search instead.
            await retrySearch();
            return;
        }

        const request = runTrackedRequest("listings", listingsUrl(), {
            apply(payload) {
                applyListings(payload);
                renderAll();
            },
            fail(error) {
                failListings(error);
                renderAll();
            },
        });
        state.loadErrors.listings = null;
        renderAll();
        await request;
    }

    async function loadDeals() {
        if (!state.activeQuery) {
            cancelRequest("deals");
            state.dealListings = [];
            state.loadErrors.deals = null;
            renderAll();
            return;
        }
        if (state.searchError) {
            // Without stats the list cannot be shown: rerun the whole search instead.
            await retrySearch();
            return;
        }

        const request = runTrackedRequest("deals", dealsUrl(), {
            apply(payload) {
                applyDeals(payload);
                renderAll();
            },
            fail(error) {
                failDeals(error);
                renderAll();
            },
        });
        state.loadErrors.deals = null;
        renderAll();
        await request;
    }

    async function loadComparison() {
        const comparisonQuery = state.comparisonQuery.trim();
        const compareQueries = parseComparisonQueries(comparisonQuery).filter(
            (item) => normalizedQuery(item) !== normalizedQuery(state.activeQuery)
        );
        if (!state.activeQuery || !compareQueries.length) {
            cancelRequest("comparison");
            lastComparisonKey = "";
            state.comparisonLoading = false;
            state.comparisonStats = null;
            state.comparisonItems = [];
            renderComparison();
            return;
        }

        const params = new URLSearchParams({
            base_query: state.activeQuery,
            currency: state.currency,
            strict_search: String(state.strictSearch),
        });
        for (const item of compareQueries) {
            params.append("compare_query", item);
        }
        // Remember the requested set so a quick chip that changes nothing does not reload it.
        lastComparisonKey = comparisonKey(compareQueries);
        const request = runTrackedRequest("comparison", `/api/v1/compare?${params.toString()}`, {
            apply(payload) {
                state.comparisonStats = payload;
                state.comparisonItems = payload?.items || [];
                state.comparisonLoading = false;
                renderComparison();
            },
            fail(error) {
                state.comparisonStats = null;
                state.comparisonItems = [];
                state.comparisonLoading = false;
                lastComparisonKey = "";
                state.error = error.message || "Не удалось загрузить сравнение.";
                renderComparison();
                renderError();
            },
        });

        state.comparisonLoading = true;
        state.error = null;
        setPanelOpen("comparison", true);
        renderComparison();
        renderError();
        await request;
    }

    async function swapComparisonQueries() {
        const comparisonQuery = state.comparisonQuery.trim();
        if (!state.activeQuery || !comparisonQuery) {
            return;
        }

        const compareQueries = parseComparisonQueries(comparisonQuery);
        if (!compareQueries.length) {
            return;
        }
        const previousBase = state.activeQuery;
        const [nextBase, ...rest] = compareQueries;
        elements.searchInput.value = nextBase;
        state.query = nextBase;
        state.comparisonQuery = [previousBase, ...rest].join(", ");
        elements.compareInput.value = state.comparisonQuery;
        setPanelOpen("comparison", true);
        renderComparison();
        await search("overview");
    }

    async function openListingDetail(item, { query, strictSearch } = {}) {
        const detailQuery = String(query || state.activeQuery || "").trim();
        if (!item?.ad_id || !detailQuery) {
            return;
        }
        const strict = typeof strictSearch === "boolean" ? strictSearch : state.strictSearch;
        const url = `/api/v1/listing-detail?${buildCommonQuery(
            { ad_id: item.ad_id },
            { query: detailQuery, strictSearch: strict }
        )}`;

        state.error = null;
        renderError();
        await runTrackedRequest("detail", url, {
            apply(payload) {
                state.detail = payload ? { ...payload, query: payload.query || detailQuery } : null;
                state.detailImageIndex = 0;
                renderDetailModal();
            },
            fail(error) {
                if (error.status === 404) {
                    showToast("Объявление уже снято с продажи");
                    return;
                }
                state.error = error.message || "Не удалось открыть карточку.";
                renderError();
            },
        });
    }

    function closeDetail() {
        cancelRequest("detail");
        closeDetailModal();
    }

    function bindTelegramBackButton() {
        const backButton = window.Telegram?.WebApp?.BackButton;
        const modal = elements.detailModal;
        if (!backButton || !modal || !supportsTelegramVersion("6.1")) {
            return;
        }
        const sync = () => {
            try {
                if (modal.hidden) {
                    backButton.hide();
                } else {
                    backButton.show();
                }
            } catch (_) {
                // BackButton is optional chrome; the modal has its own close button.
            }
        };
        backButton.onClick(() => {
            if (!modal.hidden) {
                closeDetail();
            }
        });
        if (typeof MutationObserver === "function") {
            new MutationObserver(sync).observe(modal, {
                attributes: true,
                attributeFilter: ["hidden"],
            });
        }
        sync();
    }

    // Trackers and their events load as one "trackers" request: a newer load (after a
    // create, delete or clear) aborts the older one, so a late stale answer cannot bring
    // back a deleted tracker or cleared events.
    async function loadTrackers() {
        if (!hasTelegramInitData()) {
            cancelRequest("trackers");
            state.trackers = [];
            state.trackerEvents = [];
            state.trackerStatus = "";
            state.loadErrors.events = null;
            renderTrackers();
            renderTrackerEvents();
            renderTrackerStatus();
            return;
        }

        const { token, signal } = beginRequest("trackers");
        renderTrackers();
        renderTrackerEvents();
        const [trackersResult, eventsResult] = await Promise.allSettled([
            getJson("/api/v1/trackers", { signal }),
            getJson("/api/v1/tracker-events", { signal }),
        ]);
        if (!isCurrentRequest("trackers", token)) {
            return;
        }
        endRequest("trackers", token);

        if (trackersResult.status === "fulfilled") {
            state.trackers = trackersResult.value || [];
            state.trackerStatus = "";
            state.trackerStatusKind = "info";
        } else {
            state.trackerStatus = trackersResult.reason?.message || "Не удалось загрузить трекеры.";
            state.trackerStatusKind = "error";
        }

        if (eventsResult.status === "fulfilled") {
            state.trackerEvents = eventsResult.value || [];
            state.loadErrors.events = null;
        } else {
            state.loadErrors.events =
                eventsResult.reason?.message || "Не удалось загрузить события.";
        }

        renderTrackers();
        renderTrackerEvents();
        renderTrackerStatus();
    }

    async function loadLeads() {
        if (!hasTelegramInitData()) {
            state.leads = [];
            state.loadErrors.leads = null;
            renderLeads();
            return;
        }
        await runTrackedRequest("leads", "/api/v1/leads", {
            onStart: renderLeads,
            apply(payload) {
                state.leads = payload || [];
                state.loadErrors.leads = null;
                renderLeads();
            },
            fail(error) {
                state.loadErrors.leads = error.message || "Не удалось загрузить покупки.";
                renderLeads();
            },
        });
    }

    async function loadWatchlist() {
        if (!hasTelegramInitData()) {
            state.watchlist = [];
            state.loadErrors.watchlist = null;
            renderWatchlist();
            return;
        }
        await runTrackedRequest("watchlist", "/api/v1/watchlist", {
            onStart: renderWatchlist,
            apply(payload) {
                state.watchlist = payload || [];
                state.loadErrors.watchlist = null;
                renderWatchlist();
            },
            fail(error) {
                state.loadErrors.watchlist = error.message || "Не удалось загрузить избранное.";
                renderWatchlist();
            },
        });
    }

    // The board is heavy (several market crawls), so it is loaded only when the
    // deals view is shown, at most once per currency in flight, and cached briefly.
    function loadOpportunityBoard({ force = false } = {}) {
        if (!hasTelegramInitData()) {
            cancelRequest("board");
            boardInFlight = null;
            state.opportunityBoard = emptyOpportunityBoard();
            state.loadErrors.board = null;
            renderOpportunityBoard();
            return Promise.resolve();
        }

        const currency = state.currency;
        if (boardInFlight && boardInFlight.currency === currency) {
            return boardInFlight.promise;
        }
        // A board still loading in another currency (BYN -> USD -> BYN) must not land
        // after this call decided the stored board is fresh.
        cancelStaleBoard(currency);
        const isFresh =
            state.opportunityBoard.currency === currency &&
            !state.loadErrors.board &&
            Date.now() - boardLoadedAt < BOARD_TTL_MS;
        if (!force && isFresh) {
            return Promise.resolve();
        }

        const entry = { currency, promise: null };
        entry.promise = runTrackedRequest(
            "board",
            `/api/v1/opportunity-board?currency=${encodeURIComponent(currency)}`,
            {
                timeoutMs: LONG_TIMEOUT_MS,
                onStart: renderOpportunityBoard,
                apply(payload) {
                    state.opportunityBoard = {
                        ...emptyOpportunityBoard(),
                        ...(payload || {}),
                        partial: Boolean(payload?.partial),
                        currency,
                    };
                    state.loadErrors.board = null;
                    // A partial board (the API hit its deadline) is not cached as fresh.
                    boardLoadedAt = state.opportunityBoard.partial ? 0 : Date.now();
                    renderOpportunityBoard();
                },
                fail(error) {
                    state.loadErrors.board = error.message || "Не удалось загрузить подборку.";
                    renderOpportunityBoard();
                },
            }
        ).finally(() => {
            if (boardInFlight === entry) {
                boardInFlight = null;
            }
        });
        boardInFlight = entry;
        return entry.promise;
    }

    function cancelStaleBoard(currency) {
        if (boardInFlight && boardInFlight.currency !== currency) {
            cancelRequest("board");
            boardInFlight = null;
        }
    }

    async function createTracker() {
        if (telegramOnly()) {
            return;
        }

        const query = state.activeQuery.trim();
        if (!query) {
            showToast("Сначала выполните поиск");
            return;
        }

        // No client-side duplicate check: the API compares the query, the strict mode and
        // every filter, and answers 409 with a Russian message shown in the tracker status.
        state.trackerStatus = "";
        renderTrackerStatus();
        try {
            await postJson("/api/v1/trackers", {
                query,
                strict_mode: state.strictSearch,
                interval_min: 15,
                min_discount_percent: parseMinDiscount(state.trackerMinDiscountPercent),
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
            showToast(state.trackerStatus);
        }
    }

    // Returns true only when the lead was actually saved.
    async function addLeadFromListing(item, source = "manual", queryOverride = null) {
        if (!item?.ad_id || telegramOnly()) {
            return false;
        }
        try {
            const marketEstimate = (item.flip_estimates || []).find((entry) => entry.label === "По рынку");
            // No status: the API keeps the status of an existing lead and uses "new" otherwise.
            await postJson("/api/v1/leads", {
                query: queryOverride || state.activeQuery || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
                target_resale_byn: marketEstimate?.target_price || null,
                source,
            });
            showToast("Добавлено в покупки");
            await loadLeads();
            return true;
        } catch (error) {
            showToast(error.message || "Не удалось добавить в покупки");
            return false;
        }
    }

    async function addWatchlistFromListing(item, queryOverride = null) {
        if (!item?.ad_id || telegramOnly()) {
            return false;
        }
        try {
            await postJson("/api/v1/watchlist", {
                query: queryOverride || state.activeQuery || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
            });
            showToast("Добавлено в избранное");
            await loadWatchlist();
            return true;
        } catch (error) {
            showToast(error.message || "Не удалось добавить в избранное");
            return false;
        }
    }

    // The update helpers resolve to true only when the server saved the change, so the
    // renderers can put a rejected value back into the field.
    function updateLeadStatus(leadId, nextStatus) {
        return updateLeadMeta(leadId, { status: nextStatus });
    }

    async function updateLeadMeta(leadId, payload) {
        try {
            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
        } catch (error) {
            showToast(error.message || "Не удалось обновить");
            return false;
        }
        await loadLeads();
        return true;
    }

    function updateWatchlistStatus(watchlistId, workflowStatus) {
        return updateWatchlistMeta(watchlistId, { workflow_status: workflowStatus });
    }

    async function updateWatchlistMeta(watchlistId, payload) {
        try {
            await requestJson(`/api/v1/watchlist/${watchlistId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
        } catch (error) {
            showToast(error.message || "Не удалось обновить");
            return false;
        }
        await loadWatchlist();
        return true;
    }

    async function promoteWatchlistToLead(item) {
        if (!item?.ad_id) {
            return;
        }
        const created = await addLeadFromListing(
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
        if (created) {
            await updateWatchlistMeta(item.id, { workflow_status: "in_progress" });
        }
    }

    async function deleteWatchlistItem(watchlistId) {
        try {
            await deleteJson(`/api/v1/watchlist/${watchlistId}`);
            showToast("Удалено из избранного");
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        }
    }

    async function refreshWatchlist() {
        if (telegramOnly() || isPending("refresh")) {
            return;
        }
        const { token, signal } = beginRequest("refresh");
        renderWatchlist();
        let payload = null;
        let failure = null;
        try {
            payload = await postJson(
                "/api/v1/watchlist/refresh",
                {},
                { timeoutMs: LONG_TIMEOUT_MS, signal }
            );
        } catch (error) {
            failure = error;
        }
        if (!isCurrentRequest("refresh", token)) {
            return;
        }
        endRequest("refresh", token);
        renderWatchlist();

        if (failure) {
            if (failure.timeout || failure.status === 504) {
                // The server may still finish and save part of the prices.
                showToast("Проверка цен заняла слишком много времени. Часть цен могла обновиться.");
                await loadWatchlist();
            } else {
                showToast(failure.message || "Не удалось обновить цены");
            }
            return;
        }

        const updated = payload?.updated ?? 0;
        const missing = payload?.missing ?? 0;
        const drops = payload?.price_drops ?? 0;
        const skipped = Number(payload?.skipped) || 0;
        let message = `Проверено: ${updated}, пропало: ${missing}, падений цены: ${drops}`;
        if (skipped > 0) {
            message += `. Не успели проверить: ${skipped}, повторите позже`;
        }
        showToast(message);
        await loadWatchlist();
    }

    async function clearTrackerEvents() {
        if (telegramOnly()) {
            return;
        }
        if (!state.trackerEvents.length) {
            showToast("Лента событий уже пуста");
            return;
        }
        const confirmed = await confirmAction(
            "Удалить все события трекеров? Это действие нельзя отменить."
        );
        if (!confirmed) {
            return;
        }
        try {
            await deleteJson("/api/v1/tracker-events");
        } catch (error) {
            // Keep the local feed: the server still has the events.
            showToast(error.message || "Не удалось очистить события");
            return;
        }
        state.trackerEvents = [];
        state.loadErrors.events = null;
        showToast("События очищены");
        renderTrackerEvents();
        // A load started before the DELETE could still answer with the old events:
        // restart it, which aborts the stale one.
        if (isPending("trackers")) {
            void loadTrackers();
        }
    }

    function applySavedSearchSettings(item) {
        const discount = Math.round(item.target_discount_percent ?? DEFAULT_MIN_DISCOUNT_PERCENT);
        state.strictSearch = Boolean(item.strict_mode);
        state.discountFromPercent = discount;
        state.trackerMinDiscountPercent = parseMinDiscount(discount);
        state.trackerMaxPriceByn = item.max_price_byn ?? null;
        state.trackerExcludeDuplicates = Boolean(item.exclude_duplicates);
        state.trackerSellerType = item.seller_type || "";
        state.trackerCondition = item.condition || "";
        state.trackerRegionName = item.region_name || "";
        state.trackerConfigKeyword = item.config_keyword || "";
    }

    async function openOpportunityQuery(item) {
        if (!item) {
            return;
        }

        applySavedSearchSettings(item);
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

        // Opening a card must not change the submitted query or the strict mode.
        await openListingDetail(item.listing, {
            query: item.query,
            strictSearch: Boolean(item.strict_mode),
        });
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
        // A board still loading in the old currency is dropped in every view.
        cancelStaleBoard(currency);
        // The board is reloaded in the new currency right away only when it is
        // visible; otherwise its stored currency makes the next open reload it.
        const boardRequest = state.activeView === "deals" ? loadOpportunityBoard() : null;

        if (state.activeQuery) {
            await runSearch(state.activeQuery, state.activeView);
        } else {
            renderAll();
        }
        await boardRequest;
    }

    function parseStrictParam(value) {
        const text = String(value ?? "").trim().toLowerCase();
        if (text === "1" || text === "true") return true;
        if (text === "0" || text === "false") return false;
        return null;
    }

    async function applyLaunchParams() {
        const params = new URLSearchParams(window.location.search);
        const query = params.get("query")?.trim() || "";
        const view = normalizeView(params.get("view")) || "overview";
        const strict = parseStrictParam(params.get("strict"));
        if (strict !== null) {
            state.strictSearch = strict;
            renderStrictSearch();
        }
        if (isKnownView(view)) {
            setActiveView(view);
        }
        if (state.activeView === "deals") {
            void loadOpportunityBoard();
        }
        if (!query) {
            renderAll();
            return;
        }
        elements.searchInput.value = query;
        state.query = query;
        await search(view);
    }

    async function runSearch(query, target = "overview") {
        state.activeQuery = query;
        state.error = null;
        state.searchError = null;
        state.searchRequestId += 1;
        const requestId = state.searchRequestId;
        for (const kind of SEARCH_REQUEST_KINDS) {
            cancelRequest(kind);
        }
        lastComparisonKey = "";
        state.comparisonLoading = false;
        state.loadErrors.listings = null;
        state.loadErrors.deals = null;
        clearSearchData();
        focusTarget(target);

        if (!query) {
            state.loading = false;
            renderAll();
            return;
        }

        state.loading = true;
        // Listings and history arrive only after the stats, so they count as loading
        // from the start.
        setPending("listings", true);
        setPending("deals", true);
        setPending("history", true);
        renderAll();

        const { token, signal } = beginRequest("stats");
        try {
            const stats = await getJson(`/api/v1/price-stats?${buildCommonQuery()}`, { signal });
            if (!isActiveRequest(requestId) || !isCurrentRequest("stats", token)) {
                return;
            }
            endRequest("stats", token);
            state.stats = stats;
            state.loading = false;
            loadSearchDependencies(requestId);
            renderAll();
        } catch (error) {
            if (!isActiveRequest(requestId) || !isCurrentRequest("stats", token)) {
                return;
            }
            endRequest("stats", token);
            clearSearchData();
            setPending("listings", false);
            setPending("deals", false);
            setPending("history", false);
            state.searchError = error.message || "Не удалось загрузить аналитику.";
            state.error = state.searchError;
            state.loading = false;
            renderAll();
            return;
        }

        if (state.comparisonQuery.trim()) {
            await loadComparison();
        }
    }

    // "Повторить" after a failed /price-stats: the whole search failed, so reloading only
    // one list would never clear the failure. Reruns it and stays on the current view.
    async function retrySearch() {
        if (!state.activeQuery) {
            return;
        }
        await runSearch(state.activeQuery, state.activeView);
    }

    async function search(target = "overview") {
        const query = elements.searchInput.value.trim();
        state.query = query;
        await runSearch(query, target);
    }

    async function searchFor(query, { strictSearch, view } = {}) {
        const text = String(query || "").trim();
        elements.searchInput.value = text;
        state.query = text;
        if (typeof strictSearch === "boolean") {
            state.strictSearch = strictSearch;
            renderStrictSearch();
        }
        await search(view || "overview");
    }

    function loadViewData(view) {
        if (view === "tracking") {
            void loadTrackers();
        }
        if (view === "monitoring") {
            void loadWatchlist();
        }
        if (view === "deals") {
            void loadLeads();
            void loadOpportunityBoard();
        }
        // After a failed search the deals tab shows that failure with its own retry.
        if (view === "cheap" && state.activeQuery && !isPending("deals") && !state.searchError) {
            void loadDeals();
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
            if (state.activeQuery) {
                void runSearch(state.activeQuery, state.activeView);
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
                loadViewData(view);
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
                const query = (chip.dataset.compareQuery || "").trim();
                if (!query) {
                    return;
                }
                const existing = parseComparisonQueries(state.comparisonQuery);
                const alreadySelected = existing.some(
                    (item) => normalizedQuery(item) === normalizedQuery(query)
                );
                let nextValues = existing;
                if (!alreadySelected) {
                    // With two queries selected the chip replaces the last one.
                    nextValues = existing.length < 2 ? [...existing, query] : [existing[0], query];
                }
                state.comparisonQuery = nextValues.join(", ");
                elements.compareInput.value = state.comparisonQuery;
                setPanelOpen("comparison", true);
                renderComparison();

                const compareQueries = nextValues.filter(
                    (item) => normalizedQuery(item) !== normalizedQuery(state.activeQuery)
                );
                if (comparisonKey(compareQueries) !== lastComparisonKey) {
                    void loadComparison();
                }
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
                if (state.activeQuery) {
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
                if (state.activeQuery) {
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
            if (state.activeQuery) {
                void loadDeals();
            }
        });

        elements.trackQueryButton?.addEventListener("click", () => {
            void createTracker();
        });

        elements.clearEventsButton?.addEventListener("click", () => {
            void clearTrackerEvents();
        });

        elements.reloadLeadsButton?.addEventListener("click", () => {
            void loadLeads();
        });

        elements.refreshWatchlistButton?.addEventListener("click", () => {
            void refreshWatchlist();
        });

        elements.reloadOpportunityBoardButton?.addEventListener("click", () => {
            void loadOpportunityBoard({ force: true });
        });

        elements.trackerMinDiscountInput?.addEventListener("input", () => {
            state.trackerMinDiscountPercent = parseMinDiscount(elements.trackerMinDiscountInput.value);
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
            closeDetail();
        });

        elements.detailOverlay?.addEventListener("click", () => {
            closeDetail();
        });

        elements.detailAddLeadButton?.addEventListener("click", () => {
            if (state.detail) {
                void addLeadFromListing(
                    state.detail,
                    "detail_modal",
                    state.detail.query || state.activeQuery
                );
            }
        });

        elements.detailAddWatchlistButton?.addEventListener("click", () => {
            if (state.detail) {
                void addWatchlistFromListing(state.detail, state.detail.query || state.activeQuery);
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && (state.detail || !elements.detailModal?.hidden)) {
                closeDetail();
            }
        });

        bindTelegramBackButton();

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
    }

    return {
        bindEvents,
        search,
        retrySearch,
        searchFor,
        loadListings,
        loadDeals,
        loadRates,
        loadHistory,
        loadComparison,
        swapComparisonQueries,
        loadTrackers,
        loadLeads,
        loadWatchlist,
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
        clearTrackerEvents,
        deleteTracker,
        openOpportunityQuery,
        openOpportunityDetail,
        openListingDetail,
        closeDetail,
        setCurrency,
        applyLaunchParams,
    };
}
