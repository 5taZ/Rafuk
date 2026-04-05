/* global Chart */

function analyticsApp() {
    const state = {
        query: "",
        currency: "BYN",
        sort: "newest",
        loading: false,
        error: null,
        stats: null,
        listings: [],
        segments: null,
        chart: null,
        usdRateByn: null,
        trackers: [],
        trackerStatus: "",
        trackerStatusKind: "info",
        detail: null,
        detailImageIndex: 0,
        activeView: "overview",
    };

    const elements = {};

    function cacheElements() {
        elements.searchInput = document.getElementById("search-input");
        elements.searchButton = document.getElementById("search-btn");
        elements.searchButtonLabel = document.getElementById("search-btn-label");
        elements.errorBar = document.getElementById("error-bar");
        elements.errorText = document.getElementById("error-text");
        elements.helperPanel = document.getElementById("helper-panel");
        elements.summaryStrip = document.getElementById("summary-strip");
        elements.summaryQuery = document.getElementById("summary-query");
        elements.summaryMedian = document.getElementById("summary-median");
        elements.summaryCoverage = document.getElementById("summary-coverage");
        elements.viewTabs = Array.from(document.querySelectorAll("[data-view]"));
        elements.views = {
            overview: document.getElementById("overview-view"),
            ads: document.getElementById("ads-view"),
            trackers: document.getElementById("trackers-view"),
        };
        elements.statsSection = document.getElementById("stats-section");
        elements.chartSection = document.getElementById("chart-section");
        elements.segmentsSection = document.getElementById("segments-section");
        elements.listingsSection = document.getElementById("listings-section");
        elements.marketTotalBadge = document.getElementById("market-total-badge");
        elements.listingsTotalBadge = document.getElementById("listings-total-badge");
        elements.stats = {
            median: document.getElementById("stat-median"),
            mean: document.getElementById("stat-mean"),
            min: document.getElementById("stat-min"),
            max: document.getElementById("stat-max"),
            coverage: document.getElementById("stat-coverage"),
        };
        elements.rateStrip = document.getElementById("rate-strip");
        elements.usdRateValue = document.getElementById("usd-rate-value");
        elements.segmentsGrid = document.getElementById("segments-grid");
        elements.listingsList = document.getElementById("listings-list");
        elements.sortButtons = Array.from(document.querySelectorAll("[data-sort]"));
        elements.quickChips = Array.from(document.querySelectorAll("[data-query]"));
        elements.trackerPanel = document.getElementById("tracker-panel");
        elements.trackQueryButton = document.getElementById("track-query-btn");
        elements.reloadTrackersButton = document.getElementById("reload-trackers-btn");
        elements.trackerStatus = document.getElementById("tracker-status");
        elements.trackersList = document.getElementById("trackers-list");
        elements.detailModal = document.getElementById("detail-modal");
        elements.detailOverlay = document.getElementById("detail-overlay");
        elements.detailClose = document.getElementById("detail-close");
        elements.detailMainImage = document.getElementById("detail-main-image");
        elements.detailNoImage = document.getElementById("detail-no-image");
        elements.detailThumbs = document.getElementById("detail-thumbs");
        elements.detailTitle = document.getElementById("detail-title");
        elements.detailPrice = document.getElementById("detail-price");
        elements.detailMeta = document.getElementById("detail-meta");
        elements.detailDescription = document.getElementById("detail-description");
        elements.detailLink = document.getElementById("detail-link");
        elements.detailParamsBlock = document.getElementById("detail-params-block");
        elements.detailParams = document.getElementById("detail-params");
        elements.detailSellerBlock = document.getElementById("detail-seller-block");
        elements.detailSeller = document.getElementById("detail-seller");
        elements.currencyButtons = {
            BYN: document.getElementById("btn-byn"),
            USD: document.getElementById("btn-usd"),
        };
    }

    function initTelegramTheme() {
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.expand();
            window.Telegram.WebApp.ready();
            const scheme = window.Telegram.WebApp.colorScheme;
            document.documentElement.setAttribute(
                "data-theme",
                scheme === "light" ? "light" : "dark"
            );
        } else {
            document.documentElement.setAttribute("data-theme", "dark");
        }
    }

    function formatPrice(value) {
        if (value == null || value === 0) return "—";
        const numeric = Number(value);
        if (Number.isNaN(numeric)) return "—";

        if (state.currency === "BYN") {
            if (numeric >= 10000) {
                return `${(numeric / 1000).toFixed(1).replace(/\.0$/, "")} тыс. р.`;
            }
            if (numeric >= 1000) {
                return `${(numeric / 1000).toFixed(2).replace(/0+$/, "").replace(/\.$/, "")} тыс. р.`;
            }
            return `${Math.round(numeric)} р.`;
        }

        return `$${numeric.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
    }

    function formatRate(value) {
        if (!value) return "—";
        return `${Number(value).toFixed(4)} BYN`;
    }

    function formatCondition(condition) {
        const map = {
            "Новый": "Новый",
            "Б/у": "Б/у",
            new: "Новый",
            used: "Б/у",
            "1": "Б/у",
            "2": "Новый",
        };
        return map[condition] || condition || "";
    }

    function formatSeller(seller) {
        const map = {
            "Частное лицо": "Частное",
            "Магазин": "Магазин",
            private: "Частное",
            shop: "Магазин",
        };
        return map[seller] || seller || "";
    }

    function formatDelta(delta) {
        if (delta == null || Math.abs(delta) < 0.5) return "";
        const sign = delta > 0 ? "+" : "";
        return `${sign}${Math.round(delta)}%`;
    }

    function deltaClass(delta) {
        if (!delta || Math.abs(delta) < 0.5) return "";
        return delta > 0 ? "over" : "under";
    }

    function formatDate(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return new Intl.DateTimeFormat("ru-BY", {
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
        }).format(date);
    }

    function hasTelegramInitData() {
        return Boolean(
            window.Telegram &&
            window.Telegram.WebApp &&
            window.Telegram.WebApp.initData
        );
    }

    async function getJson(url, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (
            window.Telegram &&
            window.Telegram.WebApp &&
            window.Telegram.WebApp.initData
        ) {
            headers["X-Telegram-Init-Data"] = window.Telegram.WebApp.initData;
        }
        if (options.body && !headers["Content-Type"]) {
            headers["Content-Type"] = "application/json";
        }

        const response = await fetch(url, { ...options, headers });
        if (!response.ok) {
            let message = `Ошибка ${response.status}`;
            if (response.status === 502) {
                message = "API недоступен. Поднимите uvicorn на 0.0.0.0:8010 и обновите Mini App.";
            }
            try {
                const payload = await response.json();
                if (payload.detail) message = payload.detail;
            } catch (_) {}
            throw new Error(message);
        }
        if (response.status === 204) {
            return null;
        }
        return response.json();
    }

    async function loadRates() {
        try {
            const payload = await getJson("/api/v1/currency-rates");
            state.usdRateByn = payload?.rates?.USD || null;
            renderRates();
        } catch (_) {}
    }

    function renderRates() {
        if (!state.usdRateByn) {
            elements.rateStrip.hidden = true;
            return;
        }
        elements.usdRateValue.textContent = formatRate(state.usdRateByn);
        elements.rateStrip.hidden = false;
    }

    function renderError() {
        const message = typeof state.error === "string" ? state.error.trim() : "";
        if (!message) {
            elements.errorBar.hidden = true;
            elements.errorBar.classList.remove("is-visible");
            elements.errorText.textContent = "";
            return;
        }
        elements.errorText.textContent = message;
        elements.errorBar.hidden = false;
        elements.errorBar.classList.add("is-visible");
    }

    function renderLoading() {
        elements.searchButton.disabled = state.loading || !state.query.trim();
        elements.searchInput.disabled = state.loading;
        if (state.loading) {
            elements.searchButtonLabel.innerHTML = '<span class="spin"></span>';
        } else {
            elements.searchButtonLabel.textContent = "Найти";
        }
    }

    function renderCurrencyButtons() {
        for (const [currency, button] of Object.entries(elements.currencyButtons)) {
            button.classList.toggle("active", state.currency === currency);
        }
    }

    function renderViewTabs() {
        for (const button of elements.viewTabs) {
            button.classList.toggle("active", button.dataset.view === state.activeView);
        }
    }

    function renderSummary() {
        if (!state.stats || !state.query) {
            elements.summaryStrip.hidden = true;
            elements.summaryQuery.textContent = "—";
            elements.summaryMedian.textContent = "—";
            elements.summaryCoverage.textContent = "—";
            return;
        }

        elements.summaryQuery.textContent = state.query;
        elements.summaryMedian.textContent = formatPrice(state.stats.median);
        elements.summaryCoverage.textContent =
            `${state.stats.analyzed_count || state.stats.count || 0} / ${state.stats.total_results || 0}`;
        elements.summaryStrip.hidden = false;
    }

    function renderHelper() {
        const shouldShow =
            !state.loading &&
            !state.error &&
            !state.stats &&
            state.activeView !== "trackers";
        elements.helperPanel.hidden = !shouldShow;
    }

    function renderViews() {
        for (const [name, panel] of Object.entries(elements.views)) {
            panel.hidden = state.activeView !== name;
        }
    }

    function renderSortButtons() {
        for (const button of elements.sortButtons) {
            button.classList.toggle("active", button.dataset.sort === state.sort);
        }
    }

    function renderStats() {
        if (!state.stats) {
            elements.statsSection.hidden = true;
            elements.chartSection.hidden = true;
            return;
        }

        elements.stats.median.textContent = formatPrice(state.stats.median);
        elements.stats.mean.textContent = formatPrice(state.stats.mean);
        elements.stats.min.textContent = formatPrice(state.stats.min);
        elements.stats.max.textContent = formatPrice(state.stats.max);
        elements.stats.coverage.textContent =
            `${state.stats.analyzed_count || state.stats.count || 0} / ${state.stats.total_results || 0}`;
        elements.marketTotalBadge.textContent = `${state.stats.total_results || 0} на рынке`;
        elements.statsSection.hidden = false;
        elements.chartSection.hidden = state.stats.count <= 0;
    }

    function renderSegments() {
        elements.segmentsGrid.innerHTML = "";
        if (!state.segments) {
            elements.segmentsSection.hidden = true;
            return;
        }

        const segments = [
            {
                type: "new",
                typeLabel: "Новый",
                sellerLabel: "Частное лицо",
                data: state.segments.new_private,
            },
            {
                type: "new",
                typeLabel: "Новый",
                sellerLabel: "Магазин",
                data: state.segments.new_shop,
            },
            {
                type: "used",
                typeLabel: "Б/у",
                sellerLabel: "Частное лицо",
                data: state.segments.used_private,
            },
            {
                type: "used",
                typeLabel: "Б/у",
                sellerLabel: "Магазин",
                data: state.segments.used_shop,
            },
        ].filter((segment) => segment.data && segment.data.count > 0);

        if (segments.length === 0) {
            elements.segmentsSection.hidden = true;
            return;
        }

        for (const segment of segments) {
            const card = document.createElement("div");
            card.className = "seg-card";
            card.innerHTML = `
                <div class="seg-head">
                    <span class="seg-pill ${segment.type}">${segment.typeLabel}</span>
                    <span class="seg-seller">${segment.sellerLabel}</span>
                </div>
                <span class="seg-price mono">${formatPrice(segment.data.median)}</span>
                <span class="seg-count">${segment.data.count} с ценой</span>
            `;
            elements.segmentsGrid.appendChild(card);
        }

        elements.segmentsSection.hidden = false;
    }

    function renderListings() {
        elements.listingsList.innerHTML = "";
        if (!state.listings.length) {
            elements.listingsSection.hidden = true;
            return;
        }

        for (const item of state.listings) {
            const listing = document.createElement("article");
            listing.className = "listing";

            const condition = item.condition ? `<span class="tag">${formatCondition(item.condition)}</span>` : "";
            const seller = item.seller_type ? `<span class="tag">${formatSeller(item.seller_type)}</span>` : "";
            const delta = formatDelta(item.price_vs_median);
            const deltaMarkup = delta
                ? `<span class="delta ${deltaClass(item.price_vs_median)}">${delta}</span>`
                : "";
            const thumbMarkup = item.thumbnail
                ? `<img class="listing-thumb" src="${item.thumbnail}" alt="">`
                : `<div class="listing-thumb placeholder">Нет фото</div>`;
            const dateMarkup = item.list_time ? `<span class="listing-date">${formatDate(item.list_time)}</span>` : "";

            listing.innerHTML = `
                <div class="listing-main">
                    ${thumbMarkup}
                    <div class="listing-info">
                        <span class="listing-name">${item.title}</span>
                        <div class="listing-tags">${condition}${seller}${dateMarkup}</div>
                    </div>
                </div>
                <div class="listing-right">
                    <span class="listing-price mono">${formatPrice(item.price)}</span>
                    ${deltaMarkup}
                </div>
                <div class="listing-actions">
                    <button class="ghost-btn small" type="button">Карточка</button>
                    <a class="primary-link small" href="${item.link}" target="_blank" rel="noreferrer noopener">Kufar</a>
                </div>
            `;

            const detailButton = listing.querySelector("button");
            detailButton?.addEventListener("click", () => {
                void openListingDetail(item);
            });
            elements.listingsList.appendChild(listing);
        }

        const total = state.stats?.total_results || state.listings.length;
        elements.listingsTotalBadge.textContent = `${state.listings.length} из ${total}`;
        elements.listingsSection.hidden = false;
    }

    function renderTrackerStatus() {
        const message = typeof state.trackerStatus === "string" ? state.trackerStatus.trim() : "";
        if (!message) {
            elements.trackerStatus.hidden = true;
            elements.trackerStatus.textContent = "";
            elements.trackerStatus.className = "tracker-status";
            return;
        }

        elements.trackerStatus.textContent = message;
        elements.trackerStatus.className = `tracker-status is-visible ${state.trackerStatusKind}`;
        elements.trackerStatus.hidden = false;
    }

    function renderTrackers() {
        elements.trackersList.innerHTML = "";

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Откройте Mini App внутри Telegram, чтобы управлять трекерами.";
            elements.trackersList.appendChild(note);
            return;
        }

        if (!state.trackers.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Активных трекеров пока нет.";
            elements.trackersList.appendChild(note);
            return;
        }

        for (const tracker of state.trackers) {
            const row = document.createElement("div");
            row.className = "tracker-row";
            row.innerHTML = `
                <div class="tracker-row-main">
                    <strong class="tracker-query">${tracker.query}</strong>
                    <span class="tracker-meta mono">каждые ${tracker.interval_min} мин</span>
                </div>
                <div class="tracker-row-actions">
                    <button class="ghost-btn small" data-role="open" type="button">Открыть</button>
                    <button class="ghost-btn small danger" data-role="delete" type="button">Удалить</button>
                </div>
            `;
            row.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                elements.searchInput.value = tracker.query;
                state.query = tracker.query;
                renderLoading();
                void search();
            });
            row.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void deleteTracker(tracker.id);
            });
            elements.trackersList.appendChild(row);
        }
    }

    function destroyChart() {
        if (state.chart) {
            state.chart.destroy();
            state.chart = null;
        }
    }

    function renderDetailModal() {
        if (!state.detail) {
            elements.detailModal.hidden = true;
            return;
        }

        const detail = state.detail;
        const images = detail.images || [];
        const hasImages = images.length > 0;
        const currentImage = hasImages ? images[state.detailImageIndex] || images[0] : null;

        elements.detailTitle.textContent = detail.title || "Объявление";
        elements.detailPrice.textContent = formatPrice(detail.price);
        elements.detailLink.href = detail.link || "#";
        elements.detailDescription.textContent = detail.description || "";
        elements.detailDescription.hidden = !detail.description;

        const metaItems = [
            detail.category,
            detail.condition ? formatCondition(detail.condition) : "",
            detail.seller_type ? formatSeller(detail.seller_type) : "",
            detail.list_time ? formatDate(detail.list_time) : "",
            formatDelta(detail.price_vs_median),
        ].filter(Boolean);
        elements.detailMeta.innerHTML = metaItems.map((item) => `<span class="detail-pill">${item}</span>`).join("");

        elements.detailMainImage.hidden = !hasImages;
        elements.detailNoImage.hidden = hasImages;
        if (currentImage) {
            elements.detailMainImage.src = currentImage;
            elements.detailMainImage.alt = detail.title || "Фото объявления";
        } else {
            elements.detailMainImage.removeAttribute("src");
        }

        elements.detailThumbs.innerHTML = "";
        for (const [index, image] of images.entries()) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `detail-thumb${state.detailImageIndex === index ? " active" : ""}`;
            button.innerHTML = `<img src="${image}" alt="">`;
            button.addEventListener("click", () => {
                state.detailImageIndex = index;
                renderDetailModal();
            });
            elements.detailThumbs.appendChild(button);
        }

        elements.detailParams.innerHTML = "";
        const params = detail.parameters || [];
        for (const field of params) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${field.label}</span>
                <span class="detail-field-value">${field.value}</span>
            `;
            elements.detailParams.appendChild(item);
        }
        elements.detailParamsBlock.hidden = params.length === 0;

        elements.detailSeller.innerHTML = "";
        const sellerFields = detail.seller_fields || [];
        for (const field of sellerFields) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${field.label}</span>
                <span class="detail-field-value">${field.value}</span>
            `;
            elements.detailSeller.appendChild(item);
        }
        elements.detailSellerBlock.hidden = sellerFields.length === 0;
        elements.detailModal.hidden = false;
    }

    function closeDetailModal() {
        state.detail = null;
        state.detailImageIndex = 0;
        renderDetailModal();
    }

    async function switchView(view) {
        state.activeView = view;
        renderViewTabs();
        renderViews();
        renderHelper();

        if (view === "ads" && state.query && !state.listings.length && !state.loading) {
            await search("ads");
        }
    }

    async function openListingDetail(item) {
        try {
            const detail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(state.query)}&ad_id=${item.ad_id}&currency=${state.currency}`
            );
            state.detail = detail;
            state.detailImageIndex = 0;
            renderDetailModal();
        } catch (error) {
            state.error = error.message || "Ошибка загрузки карточки";
            renderError();
        }
    }

    function renderChart(stats) {
        if (stats) {
            state.stats = stats;
        }
        if (!state.stats || state.stats.count === 0) {
            destroyChart();
            return;
        }

        const canvas = document.getElementById("priceChart");
        if (!canvas) return;

        destroyChart();

        const isDark = document.documentElement.getAttribute("data-theme") !== "light";
        const muted = isDark ? "rgba(136,128,120,0.6)" : "rgba(114,105,94,0.6)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";
        const amber = isDark ? "#F59E0B" : "#D97706";
        const values = [
            state.stats.min,
            state.stats.q1,
            state.stats.median,
            state.stats.q3,
            state.stats.max,
        ];
        const alphas = [0.22, 0.4, 0.9, 0.4, 0.22];

        state.chart = new Chart(canvas, {
            type: "bar",
            data: {
                labels: ["Мин", "Q1", "Медиана", "Q3", "Макс"],
                datasets: [
                    {
                        data: values,
                        backgroundColor: alphas.map((alpha) => `rgba(245,158,11,${alpha})`),
                        borderColor: alphas.map((alpha) => `rgba(245,158,11,${Math.min(alpha + 0.3, 1)})`),
                        borderWidth: 1.5,
                        borderRadius: 5,
                        borderSkipped: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: tooltipBackground,
                        titleColor: tooltipText,
                        bodyColor: amber,
                        borderColor: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
                        borderWidth: 1,
                        padding: 10,
                        callbacks: {
                            label(context) {
                                return ` ${formatPrice(context.parsed.y)}`;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 10 },
                        },
                    },
                    y: {
                        grid: { color: grid },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 4,
                            callback(value) {
                                return formatPrice(value);
                            },
                        },
                    },
                },
            },
        });
    }

    function renderAll() {
        renderLoading();
        renderError();
        renderHelper();
        renderSummary();
        renderCurrencyButtons();
        renderViewTabs();
        renderViews();
        renderSortButtons();
        renderStats();
        renderSegments();
        renderListings();
        renderRates();
        renderTrackerStatus();
        renderTrackers();
    }

    async function loadTrackers() {
        if (!hasTelegramInitData()) {
            state.trackers = [];
            state.trackerStatus = "";
            renderTrackers();
            renderTrackerStatus();
            return;
        }

        try {
            state.trackers = await getJson("/api/v1/trackers");
            state.trackerStatus = "";
            state.trackerStatusKind = "info";
            renderTrackers();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось загрузить трекеры";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        }
    }

    async function createTracker() {
        const query = elements.searchInput.value.trim();
        if (!query) {
            state.trackerStatus = "Сначала введите запрос для мониторинга.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
            return;
        }

        if (!hasTelegramInitData()) {
            state.trackerStatus = "Эта функция доступна только внутри Telegram Mini App.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
            return;
        }

        try {
            await getJson("/api/v1/trackers", {
                method: "POST",
                body: JSON.stringify({ query, interval_min: 30 }),
            });
            state.trackerStatus = `Трекер для "${query}" добавлен.`;
            state.trackerStatusKind = "success";
            await loadTrackers();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось создать трекер";
            state.trackerStatusKind = "error";
        } finally {
            renderTrackerStatus();
            renderTrackers();
        }
    }

    async function deleteTracker(trackerId) {
        try {
            await getJson(`/api/v1/trackers/${trackerId}`, { method: "DELETE" });
            state.trackerStatus = "Трекер удалён.";
            state.trackerStatusKind = "success";
            await loadTrackers();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось удалить трекер";
            state.trackerStatusKind = "error";
        } finally {
            renderTrackerStatus();
            renderTrackers();
        }
    }

    async function search(targetView = null) {
        state.query = elements.searchInput.value.trim();
        if (!state.query) return;
        state.activeView = targetView || (state.activeView === "trackers" ? "overview" : state.activeView);

        state.loading = true;
        state.error = null;
        state.stats = null;
        state.listings = [];
        state.segments = null;
        destroyChart();
        renderAll();

        try {
            const [statsData, listingsData] = await Promise.all([
                getJson(`/api/v1/price-stats?query=${encodeURIComponent(state.query)}&currency=${state.currency}`),
                getJson(`/api/v1/listings?query=${encodeURIComponent(state.query)}&sort=${state.sort}&currency=${state.currency}`),
            ]);

            state.stats = statsData;
            state.listings = listingsData.listings || [];
            renderAll();
            renderChart(statsData);

            getJson(`/api/v1/segments?query=${encodeURIComponent(state.query)}&currency=${state.currency}`)
                .then((data) => {
                    state.segments = data;
                    renderSegments();
                })
                .catch(() => {});
        } catch (error) {
            state.error = error.message || "Ошибка загрузки данных";
            renderAll();
        } finally {
            state.loading = false;
            renderAll();
        }
    }

    async function loadListings(sortOrder) {
        if (state.sort === sortOrder) return;
        state.sort = sortOrder;
        renderSortButtons();
        state.query = elements.searchInput.value.trim();
        if (!state.query) return;

        state.loading = true;
        renderLoading();
        try {
            const data = await getJson(
                `/api/v1/listings?query=${encodeURIComponent(state.query)}&sort=${sortOrder}&currency=${state.currency}`
            );
            state.listings = data.listings || [];
            renderListings();
        } catch (error) {
            state.error = error.message || "Ошибка обновления";
            renderError();
        } finally {
            state.loading = false;
            renderLoading();
        }
    }

    async function setCurrency(currency) {
        if (state.currency === currency) return;
        state.currency = currency;
        renderCurrencyButtons();
        if (elements.searchInput.value.trim() && state.stats) {
            await search();
        }
    }

    function bindEvents() {
        elements.searchButton.addEventListener("click", () => {
            void search();
        });
        elements.searchInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                void search();
            }
        });
        elements.searchInput.addEventListener("input", () => {
            state.query = elements.searchInput.value;
            renderLoading();
        });

        elements.currencyButtons.BYN.addEventListener("click", () => setCurrency("BYN"));
        elements.currencyButtons.USD.addEventListener("click", () => setCurrency("USD"));

        for (const button of elements.sortButtons) {
            button.addEventListener("click", () => loadListings(button.dataset.sort));
        }

        for (const chip of elements.quickChips) {
            chip.addEventListener("click", () => {
                const query = chip.dataset.query || "";
                elements.searchInput.value = query;
                state.query = query;
                renderLoading();
                void search("overview");
            });
        }

        for (const button of elements.viewTabs) {
            button.addEventListener("click", () => {
                void switchView(button.dataset.view || "overview");
            });
        }

        elements.trackQueryButton.addEventListener("click", () => {
            void createTracker();
        });
        elements.reloadTrackersButton.addEventListener("click", () => {
            void loadTrackers();
        });
        elements.detailClose.addEventListener("click", closeDetailModal);
        elements.detailOverlay.addEventListener("click", closeDetailModal);
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !elements.detailModal.hidden) {
                closeDetailModal();
            }
        });
    }

    function init() {
        cacheElements();
        initTelegramTheme();
        bindEvents();
        state.query = elements.searchInput.value.trim();
        renderAll();
        loadRates();
        void loadTrackers();
    }

    return {
        init,
        search,
        loadListings,
        renderChart,
        renderBoxPlot: renderChart,
        setCurrency,
        formatPrice,
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const app = analyticsApp();
    app.init();
});
