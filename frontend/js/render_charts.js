/**
 * render_charts.js — Price chart, history chart, profit dashboard, history deals.
 */
/* global Chart */

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
        safeRender: safeRender,
    } = context;

    /* ===== Chart lifecycle ===== */

    function destroyChart() {
        if (state.chart) {
            state.chart.destroy();
            state.chart = null;
        }
    }

    function destroyHistoryChart() {
        if (state.historyChart) {
            state.historyChart.destroy();
            state.historyChart = null;
        }
    }

    /* ===== Price Distribution Chart ===== */

    function renderChart(stats) {
        if (stats) {
            state.stats = stats;
        }
        if (
            !state.stats ||
            state.stats.count === 0 ||
            elements.chartSection.hidden ||
            !state.panels.distribution
        ) {
            destroyChart();
            return;
        }

        const canvas = elements.priceChartCanvas;
        if (!canvas) return;

        destroyChart();

        const isDark = document.documentElement.getAttribute("data-theme") !== "light";
        const muted = isDark ? "rgba(136,128,120,0.6)" : "rgba(114,105,94,0.6)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";
        const accentColor = isDark ? "#3B82F6" : "#2563EB";
        const values = [
            state.stats.min,
            state.stats.q1,
            state.stats.median,
            state.stats.q3,
            state.stats.max,
        ];
        const alphas = [0.22, 0.4, 0.9, 0.4, 0.22];

        // Canvas is marked aria-hidden; the wrapper div carries the accessible label
        canvas.setAttribute("aria-hidden", "true");

        state.chart = new Chart(canvas, {
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
            const hasHistory = state.history.length > 0;
        elements.historySection.hidden = !state.query;
        if (context._hooks?.renderHistoryRangeButtons) context._hooks.renderHistoryRangeButtons();
        elements.historyEmpty.hidden = hasHistory;
        elements.historySummary.hidden = !hasHistory;
        elements.historySummary.innerHTML = "";
        if (!state.query) {
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
        if (!canvas || !state.history.length) {
            destroyHistoryChart();
            return;
        }

        const firstPoint = state.history[0];
        const lastPoint = state.history[state.history.length - 1];
        const delta = firstPoint && lastPoint && firstPoint.median
            ? ((lastPoint.median - firstPoint.median) / firstPoint.median) * 100
            : 0;
        const summaryItems = [
            {
                label: "Сейчас",
                value: formatPrice(lastPoint?.median),
                meta: `${state.history.length} точек`,
            },
            {
                label: "Тренд",
                value: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`,
                meta: `${state.historyDays} дней`,
            },
            {
                label: "Диапазон",
                value: `${formatPrice(state.history.reduce((min, p) => Math.min(min, p.median), Infinity))} - ${formatPrice(state.history.reduce((max, p) => Math.max(max, p.median), -Infinity))}`,
                meta: "по медиане",
            },
        ];
        elements.historySummary.innerHTML = summaryItems
            .map(
                (item) => `
                    <div class="history-summary-card">
                        <span class="history-summary-label">${item.label}</span>
                        <strong class="history-summary-value mono">${item.value}</strong>
                        <span class="history-summary-meta">${item.meta}</span>
                    </div>
                `
            )
            .join("");
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

        state.historyChart = new Chart(canvas, {
            type: "line",
            data: {
                labels: state.history.map((point) => formatDate(point.snapshot_at) || ""),
                datasets: [
                    {
                        label: "Медиана",
                        data: state.history.map((point) => point.median),
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

    function renderProfitDashboard() {
        return safeRender('renderProfitDashboard', () => {
        if (!elements.profitCards) return;
        elements.profitCards.innerHTML = "";

        if (!hasTelegramInitData() || !state.leads.length) {
            elements.profitDashboardSection.hidden = true;
            return;
        }

        elements.profitDashboardSection.hidden = false;

        const closedLeads = state.leads.filter(
            (l) => l.status === "closed" && l.buy_price_byn && l.sold_price_byn
        );

        let totalInvested = 0;
        let totalSoldRevenue = 0;

        closedLeads.forEach((l) => {
            totalInvested += Number(l.buy_price_byn || 0);
            totalSoldRevenue += Number(l.sold_price_byn || 0);
        });

        const displayProfit = totalSoldRevenue - totalInvested;
        const roi = totalInvested > 0 ? (((totalSoldRevenue - totalInvested) / totalInvested) * 100).toFixed(1) : "0";

        const cards = [
            {
                label: "Вложено",
                value: `${Math.round(totalInvested)} BYN`,
                sub: `${closedLeads.length} закрытых сделок`,
                className: "",
            },
            {
                label: "Прибыль",
                value: `${displayProfit >= 0 ? "+" : ""}${Math.round(displayProfit)} BYN`,
                sub: `${closedLeads.length} закрытых`,
                className: displayProfit >= 0 ? "is-accent" : "is-warning",
            },
            {
                label: "ROI",
                value: `${roi}%`,
                sub: "средний",
                className: Number(roi) >= 0 ? "is-accent" : "is-warning",
            },
        ];

        for (const card of cards) {
            const el = document.createElement("div");
            el.className = `profit-card ${card.className}`;
            el.innerHTML = `
                <span class="profit-card-label">${card.label}</span>
                <span class="profit-card-value mono">${card.value}</span>
                <span class="profit-card-sub">${card.sub}</span>
            `;
            elements.profitCards.appendChild(el);
        }
        });
    }

    /* ===== History Deals ===== */

    function renderHistoryDeals() {
        return safeRender('renderHistoryDeals', () => {
        if (!elements.historyDealsList) return;
        elements.historyDealsList.innerHTML = "";

        const closedLeads = state.leads.filter((l) => l.status === "closed");

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

            const thumbMarkup = lead.thumbnail
                ? `<img class="history-deal-thumb" src="${escapeHtml(lead.thumbnail)}" alt="" loading="lazy">`
                : `<div class="history-deal-thumb-placeholder">📦</div>`;

            card.innerHTML = `
                ${thumbMarkup}
                <div class="history-deal-info">
                    <strong class="history-deal-title">${escapeHtml(lead.title)}</strong>
                    <div class="history-deal-meta">
                        <span class="history-deal-price">${buyPrice} → ${soldPrice} BYN</span>
                        <span class="history-deal-date">${dateStr}</span>
                    </div>
                </div>
                <div class="history-deal-profit-wrap">
                    <div class="history-deal-profit ${profitClass}">
                        ${profit !== null ? `${profitSign}${Math.round(profit)}` : "—"}
                    </div>
                    <span class="history-deal-profit-currency">BYN</span>
                </div>
                <button class="history-deal-delete" data-role="delete-history-deal" type="button" aria-label="Удалить из истории">✕</button>
            `;

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
