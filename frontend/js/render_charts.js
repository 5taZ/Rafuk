/**
 * render_charts.js — Price chart, history chart, profit dashboard, history deals.
 *
 * Chart.js is loaded lazily on first chart paint instead of being a
 * blocking <script> tag in the document head. That's ~80 KB of JS
 * that the watchlist / trackers / leads users never need.
 */
/* global Chart */

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
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        escapeHtml: escapeHtml,
        safeUrl: safeUrl,
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

        // Lazy-load Chart.js on demand. If the library hasn't arrived
        // yet, paint a quiet skeleton (the existing aria-busy state on
        // .chart-section is enough — we just bail and re-enter once
        // the lib resolves).
        if (typeof window.Chart !== "function") {
            ensureChartLib()
                .then(() => renderChart())
                .catch(() => {
                    /* Network failure surfaces via the regular error
                       toast on the next render attempt. */
                });
            return;
        }

        destroyChart();

        const isDark = document.documentElement.getAttribute("data-theme") !== "light";
        const muted = isDark ? "rgba(136,128,120,0.6)" : "rgba(114,105,94,0.6)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";
        const accentColor = isDark ? "#3B82F6" : "#2563EB";
        const values = [
            state.misc.stats.min,
            state.misc.stats.q1,
            state.misc.stats.median,
            state.misc.stats.q3,
            state.misc.stats.max,
        ];
        const alphas = [0.22, 0.4, 0.9, 0.4, 0.22];

        // Canvas is marked aria-hidden; the wrapper div carries the accessible label
        canvas.setAttribute("aria-hidden", "true");

        state.charts.distribution = new Chart(canvas, {
            type: "bar",
            data: {
                labels: ["Мин", "Q1", "Медиана", "Q3", "Макс"],
                datasets: [
                    {
                        data: values,
                        backgroundColor: alphas.map((alpha) => `rgba(59,146,246,${alpha})`),
                        borderColor: alphas.map((alpha) => `rgba(59,146,246,${Math.min(alpha + 0.3, 1)})`),
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

    /* ===== History ===== */

    function renderHistory() {
        return safeRender('renderHistory', () => {
            const hasHistory = state.charts.historyData.length > 0;
        elements.historySection.hidden = !state.search.query;
        if (context._hooks?.renderHistoryRangeButtons) context._hooks.renderHistoryRangeButtons();
        elements.historyEmpty.hidden = hasHistory;
        elements.historySummary.hidden = !hasHistory;
        domClear(elements.historySummary);
        if (!state.search.query) {
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
        if (!canvas || !state.charts.historyData.length) {
            destroyHistoryChart();
            return;
        }

        // Lazy-load Chart.js — mirrors the guard in renderChart().
        if (typeof window.Chart !== "function") {
            ensureChartLib()
                .then(() => renderHistoryChart())
                .catch(() => {
                    /* error surfaced via toast on next attempt */
                });
            return;
        }

        const firstPoint = state.charts.historyData[0];
        const lastPoint = state.charts.historyData[state.charts.historyData.length - 1];
        const delta = firstPoint && lastPoint && firstPoint.median
            ? ((lastPoint.median - firstPoint.median) / firstPoint.median) * 100
            : 0;
        const summaryItems = [
            {
                label: "Сейчас",
                value: formatPrice(lastPoint?.median),
                meta: `${state.charts.historyData.length} точек`,
            },
            {
                label: "Тренд",
                value: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`,
                meta: `${state.misc.historyDays} дней`,
            },
            {
                label: "Диапазон",
                value: `${formatPrice(state.charts.historyData.reduce((min, p) => Math.min(min, p.median), Infinity))} - ${formatPrice(state.charts.historyData.reduce((max, p) => Math.max(max, p.median), -Infinity))}`,
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
        const isDark = document.documentElement.getAttribute("data-theme") !== "light";
        const lineColor = isDark ? "#3B82F6" : "#2563EB";
        const fillColor = isDark ? "rgba(59,130,246,0.12)" : "rgba(37,99,235,0.12)";
        const muted = isDark ? "rgba(136,128,120,0.75)" : "rgba(114,105,94,0.75)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.05)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";

        // Canvas is marked aria-hidden; the wrapper div carries the accessible label
        canvas.setAttribute("aria-hidden", "true");

        state.charts.history = new Chart(canvas, {
            type: "line",
            data: {
                labels: state.charts.historyData.map((point) => formatDate(point.snapshot_at) || ""),
                datasets: [
                    {
                        label: "Медиана",
                        data: state.charts.historyData.map((point) => point.median),
                        borderColor: lineColor,
                        backgroundColor: fillColor,
                        fill: true,
                        tension: 0.28,
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
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 6,
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
        const profit = Number(dashboard.total_profit_byn || 0);
        const revenue = Number(dashboard.total_revenue_byn || 0);
        const roi = Number(dashboard.average_roi_percent || 0);
        const sold = Number(dashboard.sold_leads || 0);
        const expenses = Number(dashboard.total_expenses_byn || 0);

        return [
            {
                label: "Прибыль",
                value: `${profit >= 0 ? "+" : ""}${Math.round(profit)} BYN`,
                sub: `выручка ${Math.round(revenue)} · расходы ${Math.round(expenses)}`,
                className: profit >= 0 ? "is-accent" : "is-warning",
            },
            {
                label: "ROI",
                value: `${roi >= 0 ? "+" : ""}${roi.toFixed(1)}%`,
                sub: `средний по ${sold} продажам`,
                className: roi >= 0 ? "is-accent" : "is-warning",
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

        for (const lead of sortedLeads) {
            const card = document.createElement("div");
            card.className = "history-deal-card";
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
                const validated = safeUrl(lead.thumbnail);
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

            elements.historyDealsList.appendChild(card);
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
