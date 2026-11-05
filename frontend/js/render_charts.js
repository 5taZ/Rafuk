/**
 * render_charts.js — Price chart, history chart, profit dashboard, history deals.
 *
 * Chart.js is loaded lazily on first chart paint instead of being a
 * blocking <script> tag in the document head. That's ~80 KB of JS
 * that the watchlist / trackers / leads users never need.
 */
/* global Chart */

// FR-04: scope the top-level bindings — a second execution of this file
// (late load after a timeout + retry) threw "Identifier 'CHART_JS_URL'
// has already been declared".
(function () {

const CHART_JS_URL =
    "js/vendor/chart.umd.min.js";
const CHART_JS_INTEGRITY =
    "sha384-vsrfeLOOY6KuIYKDlmVH5UiBmgIdB1oEf7p01YgWHuqmOHfZr374+odEv96n9tNC";

let _chartLibPromise = null;

function ensureChartLib() {
    if (typeof window.Chart === "function") return Promise.resolve();
    if (_chartLibPromise) return _chartLibPromise;
    _chartLibPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = CHART_JS_URL;
        script.integrity = CHART_JS_INTEGRITY;
        script.crossOrigin = "anonymous";
        script.async = true;
        script.onload = () => resolve();
        script.onerror = () => {
            // Reset so a later retry (e.g. user opens overview again
            // after a flaky network) can try again from scratch.
            _chartLibPromise = null;
            reject(new Error("Chart.js failed to load"));
        };
        document.head.appendChild(script);
    });
    return _chartLibPromise;
}

function createRenderCharts(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatPriceRange,
        formatCondition,
        formatSeller,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        safeImageUrl,
        optimizedImage,
        safeRender: safeRender,
        // OPUS-13 wave 74: dom_helpers ride through context because
        // this file loads as a lazy <script> in _lazy_charts_stub
        // and can't see bundle-IIFE-scoped callables.
        domEl,
        domClear,
        domFragment,
    } = context;

    /* ===== Chart lifecycle ===== */

    function destroyChart() {
        if (state.charts.distribution) {
            state.charts.distribution.destroy();
            state.charts.distribution = null;
        }
    }

    function destroyHistoryChart() {
        if (state.charts.history) {
            state.charts.history.destroy();
            state.charts.history = null;
        }
    }

    /* ===== Price Distribution Chart ===== */

    function renderChart(stats) {
        if (stats) {
            state.misc.stats = stats;
        }
        if (
            !state.misc.stats ||
            state.misc.stats.count === 0 ||
            elements.chartSection.hidden ||
            !state.panels.distribution
        ) {
            destroyChart();
            return;
        }

        const canvas = elements.priceChartCanvas;
        if (!canvas) return;
        const bins = state.misc.stats.histogram || [];
        if (!bins.length) {
            destroyChart();
            return;
        }
        if (elements.distributionNote) {
            elements.distributionNote.textContent = `${state.misc.stats.count} с ценой после исключения выбросов. Учитываются состояние, продавец и регион.`
                + (state.misc.stats.fallback_used ? " Мало точных совпадений — показаны похожие объявления." : "")
                + (state.misc.stats.count < 3 ? " Мало данных для оценки рынка." : "");
        }

        // Lazy-load Chart.js on demand. If the library hasn't arrived
        // yet, paint a quiet skeleton (the existing aria-busy state on
        // .chart-section is enough — we just bail and re-enter once
        // the lib resolves).
        if (typeof window.Chart !== "function") {
            ensureChartLib()
                .then(() => renderChart())
                .catch(() => {
                    if (elements.distributionNote) elements.distributionNote.textContent = "Не удалось загрузить график. Сверните и откройте блок повторно.";
                });
            return;
        }

        destroyChart();

        // FE-NEW-5: read colors from CSS tokens at render time so dark/light theme + future brand refresh propagate.
        const _root = document.documentElement;
        const _style = getComputedStyle(_root);
        const _token = (name, fallback) => (_style.getPropertyValue(name).trim() || fallback);

        const isDark = _root.getAttribute("data-theme") !== "light";
        const muted = isDark ? "rgba(136,128,120,0.6)" : "rgba(114,105,94,0.6)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)";
        const tooltipBackground = _token("--bg-hover", isDark ? "#1A1A1D" : "#FFFFFF");
        const tooltipText = _token("--text", isDark ? "#F2EFE8" : "#1A1917");
        const accentColor = _token("--accent", isDark ? "#3B82F6" : "#2563EB");
        const priceNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
        const labels = bins.map((bin, index) => bin.from_price === bin.to_price
            ? priceNumber.format(bin.from_price)
            : `${priceNumber.format(bin.from_price)} – ${index === bins.length - 1 ? "" : "< "}${priceNumber.format(bin.to_price)}`);
        canvas.removeAttribute("aria-hidden");
        canvas.setAttribute("aria-label", `Распределение цен, ${state.misc.currency}: ${bins.map((bin, i) => `${labels[i]}: ${bin.count}`).join("; ")}`);

        state.charts.distribution = new Chart(canvas, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        label: "Объявлений",
                        data: bins.map((bin) => bin.count),
                        backgroundColor: "rgba(59,130,246,0.45)",
                        borderColor: accentColor,
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
                        bodyColor: accentColor,
                        borderColor: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
                        borderWidth: 1,
                        padding: 10,
                        callbacks: {
                            label(context) {
                                const count = context.parsed.y;
                                const share = count / state.misc.stats.count * 100;
                                return ` Объявлений: ${count} (${share.toFixed(1)}% выборки)`;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        title: { display: true, text: `Цена, ${state.misc.currency}`, color: muted },
                        grid: { display: false },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 10 },
                            maxRotation: 45,
                            maxTicksLimit: 6,
                        },
                    },
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: "Объявлений", color: muted },
                        grid: { color: grid },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 4,
                            precision: 0,
                        },
                    },
                },
            },
        });
    }

    /* ===== History ===== */

    function renderHistory() {
        return safeRender('renderHistory', () => {
            const hasHistory = state.charts.historyData.length > 0;
        elements.historySection.hidden = !state.search.activeQuery;
        if (context._hooks?.renderHistoryRangeButtons) context._hooks.renderHistoryRangeButtons();
        elements.historyEmpty.hidden = hasHistory;
        // FR-02: tell a failed load apart from "history still accumulating".
        const emptyEl = elements.historyEmpty;
        const loading = Boolean(state.charts.historyLoading || state.ui.loading);
        if (elements.historyNote) {
            elements.historyNote.textContent = "Замеры по запросу и категории, без фильтров состояния, продавца и региона. Промежутки без замеров не заполняются."
                + (state.misc.currency !== "BYN" ? " Все цены пересчитаны по текущему курсу НБРБ." : "")
                + (state.misc.stats?.fallback_used ? " Показана история похожих объявлений." : "");
        }
        if (emptyEl && !emptyEl.dataset.defaultText) emptyEl.dataset.defaultText = emptyEl.textContent.trim();
        if (emptyEl) {
            emptyEl.textContent = loading ? "Загружаю историю цен…" : state.charts.historyError
                ? "Не удалось загрузить историю цен. Потяните вниз или повторите поиск."
                : emptyEl.dataset.defaultText;
        }
        if (elements.historyChartCanvas?.parentElement) {
            elements.historyChartCanvas.parentElement.hidden = !hasHistory;
        }
        elements.historySummary.hidden = !hasHistory;
        domClear(elements.historySummary);
        if (!state.search.activeQuery) {
            destroyHistoryChart();
            return;
        }
        if (!hasHistory || !state.panels.history) {
            destroyHistoryChart();
            return;
        }
        renderHistoryChart();
        });
    }

    function renderHistoryChart() {
        return safeRender('renderHistoryChart', () => {
        const canvas = elements.historyChartCanvas;
        if (!canvas || !state.search.activeQuery || !state.panels.history
            || elements.historySection.hidden || !state.charts.historyData.length) {
            destroyHistoryChart();
            return;
        }

        // Lazy-load Chart.js — mirrors the guard in renderChart().
        if (typeof window.Chart !== "function") {
            ensureChartLib()
                .then(() => renderHistory())
                .catch(() => {
                    elements.historyEmpty.hidden = false;
                    elements.historyEmpty.textContent = "Не удалось загрузить график. Сверните и откройте блок повторно.";
                    elements.historySummary.hidden = true;
                });
            return;
        }

        const points = state.charts.historyData
            .filter((point) => Number.isFinite(Date.parse(point.snapshot_at))
                && Number.isFinite(point.median) && point.analyzed_count > 0)
            .slice().sort((a, b) => Date.parse(a.snapshot_at) - Date.parse(b.snapshot_at));
        if (!points.length) {
            elements.historySummary.hidden = true;
            destroyHistoryChart();
            return;
        }
        const firstPoint = points[0];
        const lastPoint = points[points.length - 1];
        const hasTrend = points.length > 1 && firstPoint.median > 0;
        const delta = hasTrend
            ? ((lastPoint.median - firstPoint.median) / firstPoint.median) * 100
            : 0;
        const summaryItems = [
            {
                label: "Последний замер",
                value: formatPrice(lastPoint?.median),
                meta: `${formatDate(lastPoint.snapshot_at)} · ${lastPoint.analyzed_count} с ценой`,
            },
            {
                label: "Изменение",
                value: hasTrend ? `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%` : "—",
                meta: hasTrend ? "между первым и последним замером"
                    : points.length < 2 ? "Нужны минимум два замера" : "Начальная медиана равна нулю",
            },
            {
                label: "Диапазон",
                value: formatPriceRange(
                    points.reduce((min, p) => Math.min(min, p.median), Infinity),
                    points.reduce((max, p) => Math.max(max, p.median), -Infinity),
                ),
                meta: "по медиане",
            },
        ];
        elements.historySummary.replaceChildren(
            domFragment(
                summaryItems.map((item) => domEl(
                    "div",
                    { className: "history-summary-card" },
                    domEl("span", { className: "history-summary-label", text: item.label }),
                    domEl("strong", { className: "history-summary-value mono", text: item.value }),
                    domEl("span", { className: "history-summary-meta", text: item.meta }),
                ))
            )
        );
        elements.historySummary.hidden = false;

        destroyHistoryChart();
        // FE-NEW-5: read colors from CSS tokens at render time so dark/light theme + future brand refresh propagate.
        const _root = document.documentElement;
        const _style = getComputedStyle(_root);
        const _token = (name, fallback) => (_style.getPropertyValue(name).trim() || fallback);

        const isDark = _root.getAttribute("data-theme") !== "light";
        const lineColor = _token("--accent", isDark ? "#3B82F6" : "#2563EB");
        const fillColor = isDark ? "rgba(59,130,246,0.12)" : "rgba(37,99,235,0.12)";
        const muted = isDark ? "rgba(136,128,120,0.75)" : "rgba(114,105,94,0.75)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.05)";
        const tooltipBackground = _token("--bg-hover", isDark ? "#1A1A1D" : "#FFFFFF");
        const tooltipText = _token("--text", isDark ? "#F2EFE8" : "#1A1917");

        canvas.removeAttribute("aria-hidden");
        canvas.setAttribute("aria-label", `Динамика медианы: ${points.length} замеров, с ${formatDate(firstPoint.snapshot_at)} по ${formatDate(lastPoint.snapshot_at)}. Последняя медиана: ${formatPrice(lastPoint.median)}. Изменение: ${hasTrend ? `${delta.toFixed(1)}%` : "недостаточно данных"}.`);

        state.charts.history = new Chart(canvas, {
            type: "line",
            data: {
                datasets: [
                    {
                        label: "Медиана",
                        data: points.map((point) => ({ x: Date.parse(point.snapshot_at), y: point.median })),
                        borderColor: lineColor,
                        backgroundColor: fillColor,
                        fill: false,
                        tension: 0,
                        pointRadius: 2.5,
                        pointHoverRadius: 4,
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
                        bodyColor: lineColor,
                        borderColor: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
                        borderWidth: 1,
                        padding: 10,
                        callbacks: {
                            title(items) {
                                return items.length ? formatDate(new Date(items[0].parsed.x).toISOString()) : "";
                            },
                            label(context) {
                                const point = points[context.dataIndex];
                                return ` ${formatPrice(context.parsed.y)} · ${point.analyzed_count} с ценой`;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        type: "linear",
                        min: Date.parse(firstPoint.snapshot_at) - (points.length === 1 ? 1_800_000 : 0),
                        max: Date.parse(lastPoint.snapshot_at) + (points.length === 1 ? 1_800_000 : 0),
                        grid: { display: false },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 6,
                            callback(value) {
                                return formatDate(new Date(value).toISOString());
                            },
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
        });
    }

    /* ===== Profit Dashboard ===== */

    function _renderDashboardCards(dashboard) {
        const profit = Math.round(Number(dashboard.total_profit_byn || 0));
        const revenue = Number(dashboard.total_revenue_byn || 0);
        const roi = Number(Number(dashboard.average_roi_percent || 0).toFixed(1));
        const sold = Number(dashboard.sold_leads || 0);
        const expenses = Number(dashboard.total_expenses_byn || 0);

        return [
            {
                label: "Прибыль",
                value: `${profit > 0 ? "+" : ""}${profit} BYN`,
                sub: `выручка ${Math.round(revenue)} · расходы ${Math.round(expenses)}`,
                className: profit > 0 ? "is-positive" : profit < 0 ? "is-negative" : "",
            },
            {
                label: "ROI",
                value: `${roi > 0 ? "+" : ""}${roi.toFixed(1)}%`,
                sub: `средний по ${sold} продажам`,
                className: roi > 0 ? "is-positive" : roi < 0 ? "is-negative" : "",
            },
        ];
    }

    function renderProfitDashboard() {
        return safeRender('renderProfitDashboard', () => {
        if (!elements.profitCards) return;
        domClear(elements.profitCards);

        if (!hasTelegramInitData()) {
            elements.profitDashboardSection.hidden = true;
            return;
        }

        elements.profitDashboardSection.hidden = false;

        const dashboard = state.analytics.dashboard;
        if (!dashboard) {
            // Loading or no data yet — render placeholder cards so the
            // layout doesn't jump when the first response lands.
            const placeholderCards = [
                { label: "Прибыль", value: "…", sub: state.analytics.loading ? "загружаю" : "нет данных", className: "" },
                { label: "ROI", value: "…", sub: state.analytics.loading ? "загружаю" : "нет данных", className: "" },
            ];
            for (const card of placeholderCards) {
                elements.profitCards.appendChild(
                    domEl(
                        "div",
                        { className: `profit-card ${card.className}`.trim() },
                        domEl("span", { className: "profit-card-label", text: card.label }),
                        domEl("span", { className: "profit-card-value mono", text: card.value }),
                        domEl("span", { className: "profit-card-sub", text: card.sub }),
                    ),
                );
            }
            return;
        }

        const cards = _renderDashboardCards(dashboard);
        for (const card of cards) {
            elements.profitCards.appendChild(
                domEl(
                    "div",
                    { className: `profit-card ${card.className}`.trim() },
                    domEl("span", { className: "profit-card-label", text: card.label }),
                    domEl("span", { className: "profit-card-value mono", text: card.value }),
                    domEl("span", { className: "profit-card-sub", text: card.sub }),
                ),
            );
        }
        });
    }

    /* ===== History Deals ===== */

    function renderHistoryDeals() {
        return safeRender('renderHistoryDeals', () => {
        if (!elements.historyDealsList) return;
        domClear(elements.historyDealsList);

        const closedLeads = state.leads.items.filter((l) => l.status === "closed");

        if (elements.historyDealsCount) {
            elements.historyDealsCount.textContent = String(closedLeads.length);
        }

        if (!closedLeads.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Закрытых сделок пока нет. Завершите текущие сделки, чтобы они появились здесь.";
            elements.historyDealsList.appendChild(note);
            return;
        }

        const sortedLeads = [...closedLeads].sort((a, b) => {
            return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
        });

        // RV-UI-02: closed deals are items of their own list, so the
        // "no closed deals" note never sits inside role=list.
        const dealList = domEl("div", { className: "card-list", attrs: { role: "list" } });
        elements.historyDealsList.appendChild(dealList);
        for (const lead of sortedLeads) {
            const card = document.createElement("div");
            card.className = "history-deal-card";
            card.setAttribute("role", "listitem");
            card.dataset.leadId = lead.id;

            const buyPriceBynRaw = lead.buy_price_byn ? Number(lead.buy_price_byn) : null;
            const soldPriceBynRaw = lead.sold_price_byn ? Number(lead.sold_price_byn) : null;

            const buyPrice = buyPriceBynRaw ? Math.round(buyPriceBynRaw) : "?";
            const soldPrice = soldPriceBynRaw ? Math.round(soldPriceBynRaw) : "?";
            const profit = soldPriceBynRaw && buyPriceBynRaw ? soldPriceBynRaw - buyPriceBynRaw : null;
            const profitSign = profit && profit >= 0 ? "+" : "";
            const profitClass = profit && profit >= 0 ? "history-profit-positive" : "history-profit-negative";

            const dateStr = lead.updated_at ? new Date(lead.updated_at).toLocaleDateString("ru-RU") : "";

            const thumbSrc = (() => {
                const validated = safeImageUrl(lead.thumbnail);
                if (!validated) return "";
                return typeof optimizedImage === "function"
                    ? optimizedImage(validated, { width: 160 })
                    : validated;
            })();
            const thumbNode = thumbSrc
                ? domEl("img", {
                    className: "history-deal-thumb",
                    attrs: { src: thumbSrc, alt: lead.title || "Сделка", loading: "lazy" },
                })
                : domEl("div", { className: "history-deal-thumb-placeholder", attrs: { "aria-hidden": "true" }, text: "📦" });

            card.appendChild(
                domFragment(
                    thumbNode,
                    domEl(
                        "div",
                        { className: "history-deal-info" },
                        domEl("strong", { className: "history-deal-title", text: lead.title }),
                        domEl(
                            "div",
                            { className: "history-deal-meta" },
                            domEl("span", { className: "history-deal-price", text: `${buyPrice} → ${soldPrice} BYN` }),
                            domEl("span", { className: "history-deal-date", text: dateStr }),
                        ),
                    ),
                    domEl(
                        "div",
                        { className: "history-deal-profit-wrap" },
                        domEl(
                            "div",
                            {
                                className: `history-deal-profit ${profitClass}`.trim(),
                                text: profit !== null ? `${profitSign}${Math.round(profit)}` : "—",
                            },
                        ),
                        domEl("span", { className: "history-deal-profit-currency", text: "BYN" }),
                    ),
                    domEl("button", {
                        className: "history-deal-delete",
                        type: "button",
                        text: "✕",
                        dataset: { role: "delete-history-deal" },
                        attrs: { "aria-label": "Удалить из истории" },
                    }),
                )
            );

            card.querySelector('[data-role="delete-history-deal"]')?.addEventListener("click", () => {
                void actions.deleteHistoryDeal(lead.id);
            });

            dealList.appendChild(card);
        }
        });
    }

    return {
        destroyChart,
        destroyHistoryChart,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderProfitDashboard,
        renderHistoryDeals,
    };
}

// OPUS-13: lazy-load registration; stub in the bundle proxies into
// window.App._realCreateRenderCharts once this script lands.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateRenderCharts = createRenderCharts;
}
})();
