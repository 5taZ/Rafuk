/* global Chart */

function createAppRenderers(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatRate,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        hasTelegramInitData,
    } = context;

    // Canonical workflow vocabularies, shared with the API, the bot and the database.
    const LEAD_STATUS_OPTIONS = [
        ["new", "Новый"],
        ["reviewing", "Смотреть"],
        ["in_progress", "В работе"],
        ["negotiating", "Торг"],
        ["deferred", "Позже"],
        ["bought", "Купил"],
        ["reselling", "В продаже"],
        ["sold", "Продано"],
        ["skipped", "Пропустить"],
    ];
    const WATCHLIST_STATUS_OPTIONS = [
        ["watching", "Слежу"],
        ["reviewing", "Смотреть"],
        ["in_progress", "В работе"],
        ["deferred", "Позже"],
        ["skipped", "Пропустить"],
    ];
    const ACTIVE_LEAD_STATUSES = ["new", "reviewing", "in_progress", "negotiating", "deferred"];
    const MARKET_RISK_STATUSES = ["price_drop", "missing", "duplicate"];
    const LEAD_FILTER_LABELS = {
        all: "Все",
        active: "Активные",
        buy: "Куплено/в продаже",
        done: "Завершено",
    };
    const WATCHLIST_FILTER_LABELS = {
        all: "Все",
        attention: "Требуют внимания",
        watching: "Слежу",
        risk: "Риск/сигнал",
    };

    // ---- Safe markup helpers ------------------------------------------------

    const HTML_ESCAPES = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
        "`": "&#96;",
    };

    function escapeHtml(value) {
        if (value == null) {
            return "";
        }
        return String(value).replace(/[&<>"'`]/g, (char) => HTML_ESCAPES[char]);
    }

    // Only absolute http(s) URLs are allowed in href/src; anything else yields "".
    function safeUrl(value) {
        const raw = typeof value === "string" ? value.trim() : "";
        if (!raw) {
            return "";
        }
        try {
            const url = new URL(raw);
            return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
        } catch (_) {
            return "";
        }
    }

    // Markup produced by the html`` tag: every interpolated value is escaped unless it is
    // itself a SafeMarkup (nested html`` result) or an array of such values.
    class SafeMarkup {
        constructor(value) {
            this.value = value;
        }

        toString() {
            return this.value;
        }
    }

    function interpolate(value) {
        if (value instanceof SafeMarkup) {
            return value.value;
        }
        if (Array.isArray(value)) {
            return value.map(interpolate).join("");
        }
        return escapeHtml(value);
    }

    function html(strings, ...values) {
        let markup = strings[0];
        values.forEach((value, index) => {
            markup += interpolate(value) + strings[index + 1];
        });
        return new SafeMarkup(markup);
    }

    function setHtml(element, markup) {
        element.innerHTML = interpolate(markup);
    }

    function externalLink(url, className, label) {
        const href = safeUrl(url);
        if (!href) {
            return html``;
        }
        return html`<a class="${className}" href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>`;
    }

    // ---- Formatting helpers -------------------------------------------------

    function hasPrice(value) {
        if (value == null || value === "") {
            return false;
        }
        const numeric = Number(value);
        return Number.isFinite(numeric) && numeric > 0;
    }

    // formatPrice() reads state.currency; data loaded in another currency (the opportunity
    // board) is formatted by switching it synchronously for the duration of the call.
    function formatPriceIn(value, currency) {
        if (!currency || currency === state.currency) {
            return formatPrice(value);
        }
        const previousCurrency = state.currency;
        state.currency = currency;
        try {
            return formatPrice(value);
        } finally {
            state.currency = previousCurrency;
        }
    }

    function formatListingPrice(value, currency = null) {
        return hasPrice(value) ? formatPriceIn(value, currency) : "без цены";
    }

    function formatByn(value) {
        const numeric = Number(value);
        if (value == null || value === "" || !Number.isFinite(numeric)) {
            return "—";
        }
        return `${Math.round(numeric)} BYN`;
    }

    function currentQuery() {
        const value = typeof state.activeQuery === "string" ? state.activeQuery : state.query;
        return String(value || "").trim();
    }

    function loadError(key) {
        const message = (state.loadErrors || {})[key];
        return typeof message === "string" && message.trim() ? message.trim() : null;
    }

    function isListingsLoading() {
        return Boolean(state.listingsLoading) || (Boolean(state.loading) && !state.listings.length);
    }

    function buildEmptyNote(text, className = "tracker-empty") {
        const note = document.createElement("p");
        note.className = className;
        note.textContent = text;
        return note;
    }

    function buildErrorNote(message, retry = null) {
        const note = document.createElement("div");
        note.className = "tracker-status is-visible error";
        note.setAttribute("role", "alert");
        const text = document.createElement("span");
        text.textContent = message;
        note.appendChild(text);
        if (typeof retry === "function") {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ghost-btn small";
            button.textContent = "Повторить";
            button.addEventListener("click", () => {
                void retry();
            });
            note.appendChild(document.createTextNode(" "));
            note.appendChild(button);
        }
        return note;
    }

    // ---- Input preservation (re-rendering must not eat what the user is typing) --

    function isEditing(element) {
        return Boolean(element) && document.activeElement === element;
    }

    function setFieldValue(element, value) {
        if (!element || isEditing(element)) {
            return;
        }
        element.value = value;
    }

    function captureFocusedField(container) {
        const active = document.activeElement;
        if (!container || !active || !container.contains(active)) {
            return null;
        }
        if (!active.matches("input, textarea, select") || !active.dataset.role) {
            return null;
        }
        const row = active.closest("[data-row-id]");
        if (!row) {
            return null;
        }
        let selectionStart = null;
        let selectionEnd = null;
        try {
            selectionStart = active.selectionStart;
            selectionEnd = active.selectionEnd;
        } catch (_) {
            // Some input types (number) do not expose a selection.
        }
        return {
            rowId: row.dataset.rowId,
            role: active.dataset.role,
            isSelect: active.tagName === "SELECT",
            value: active.value,
            selectionStart,
            selectionEnd,
        };
    }

    function restoreFocusedField(container, snapshot) {
        if (!container || !snapshot) {
            return;
        }
        const row = Array.from(container.querySelectorAll("[data-row-id]"))
            .find((candidate) => candidate.dataset.rowId === snapshot.rowId);
        const field = row
            ? Array.from(row.querySelectorAll("[data-role]"))
                .find((candidate) => candidate.dataset.role === snapshot.role)
            : null;
        if (!field) {
            return;
        }
        if (!snapshot.isSelect) {
            field.value = snapshot.value;
        }
        field.focus({ preventScroll: true });
        if (snapshot.selectionStart != null && snapshot.selectionEnd != null) {
            try {
                field.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
            } catch (_) {
                // Not supported for this input type.
            }
        }
    }

    // Commits an edited value on change and on blur, but only when it differs from the
    // last saved value, so a value restored after a re-render is still saved on blur.
    function bindCommittedInput(input, savedValue, parse, commit) {
        let committed = savedValue;
        const flush = () => {
            const nextValue = parse(input.value);
            if (nextValue === committed) {
                return;
            }
            committed = nextValue;
            commit(nextValue);
        };
        input.addEventListener("change", flush);
        input.addEventListener("blur", flush);
    }

    function parseMoneyInput(rawValue) {
        const trimmed = String(rawValue || "").trim();
        if (!trimmed) {
            return null;
        }
        const numeric = Math.abs(Number(trimmed));
        return Number.isFinite(numeric) ? numeric : null;
    }

    function parseNotesInput(rawValue) {
        return String(rawValue || "").trim() || null;
    }

    // ---- Basic UI -----------------------------------------------------------

    function showToast(message) {
        if (!elements.toastContainer) return;
        const toast = document.createElement("div");
        toast.className = "toast";
        toast.setAttribute("role", "status");
        toast.textContent = message;
        elements.toastContainer.appendChild(toast);
        const duration = Math.min(4000, 1600 + String(message || "").length * 30);
        // Start the CSS fade-out (0.2 s) so it ends exactly when the toast is removed.
        toast.style.setProperty("--toast-fade-delay", `${Math.max(0, duration - 200)}ms`);
        setTimeout(() => {
            toast.remove();
        }, duration);
    }

    function renderRates() {
        if (!state.usdRateByn) {
            elements.rateStrip.hidden = true;
            return;
        }
        const source = typeof state.rateSource === "string" ? state.rateSource : null;
        const isReserve = Boolean(source) && source !== "nbrb";
        const label = elements.rateStrip.querySelector(".rate-label");
        if (label) {
            if (!isReserve) {
                label.textContent = "Курс НБРБ";
            } else if (source === "stale") {
                label.textContent = "Резервный курс (последний НБРБ)";
            } else {
                label.textContent = "Резервный курс";
            }
        }
        elements.rateStrip.classList.toggle("is-reserve", isReserve);
        elements.rateStrip.title = isReserve
            ? "НБРБ сейчас недоступен: цены в USD пересчитаны по резервному курсу."
            : "";
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
        elements.searchButton.setAttribute("aria-busy", String(Boolean(state.loading)));
        if (state.loading) {
            elements.searchButtonLabel.innerHTML = '<span class="spin"></span>';
        } else {
            elements.searchButtonLabel.textContent = "Найти";
        }
    }

    function renderCurrencyButtons() {
        for (const [currency, button] of Object.entries(elements.currencyButtons)) {
            button.classList.toggle("active", state.currency === currency);
            button.setAttribute("aria-pressed", String(state.currency === currency));
            button.disabled = state.loading;
        }
    }

    function renderStrictSearch() {
        if (elements.strictSearchToggle) {
            elements.strictSearchToggle.checked = state.strictSearch;
        }
    }

    function renderViewTabs() {
        for (const button of elements.viewTabs) {
            const isActive = button.dataset.view === state.activeView;
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-current", isActive ? "page" : "false");
        }
    }

    function renderPanels() {
        for (const button of elements.panelToggles) {
            const panelName = button.dataset.panelToggle;
            const isOpen = Boolean(state.panels[panelName]);
            const body = elements.panelBodies[panelName];

            if (body) {
                body.hidden = !isOpen;
            }

            button.textContent = isOpen ? "Свернуть" : "Показать";
            button.setAttribute("aria-expanded", String(isOpen));
            button.classList.toggle("is-open", isOpen);
        }
    }

    function setPanelOpen(panelName, isOpen) {
        if (!(panelName in state.panels)) {
            return;
        }

        state.panels[panelName] = Boolean(isOpen);
        renderPanels();

        if (panelName === "distribution") {
            if (state.panels.distribution) {
                renderChart();
            } else {
                destroyChart();
            }
        }

        if (panelName === "history") {
            if (state.panels.history) {
                renderHistory();
            } else {
                destroyHistoryChart();
            }
        }
    }

    function renderSummary() {
        const query = currentQuery();
        if (!state.stats || !query) {
            elements.summaryStrip.hidden = true;
            elements.summaryQuery.textContent = "—";
            elements.summarySignal.textContent = "—";
            elements.summaryMedian.textContent = "—";
            elements.summaryMarketTotal.textContent = "—";
            elements.summaryCoverage.textContent = "—";
            return;
        }

        const totalResults = Number(state.stats.total_results || 0);
        const analyzedCount = Number(state.stats.analyzed_count || state.stats.count || 0);
        const marketMedian = Number(state.stats.median || 0);
        const marketMean = Number(state.stats.mean || 0);
        const marketMin = Number(state.stats.min || 0);
        const marketMax = Number(state.stats.max || 0);
        const spreadRatio = marketMedian > 0 ? (marketMax - marketMin) / marketMedian : 0;
        const meanDeltaRatio = marketMedian > 0 ? Math.abs(marketMean - marketMedian) / marketMedian : 0;
        let signal = "Рынок читается ровно, медиана подходит как главный ориентир.";
        if (analyzedCount < 5) {
            signal = "Выборка маленькая, смотрите объявления и сравнивайте вручную.";
        } else if (spreadRatio > 0.8 || meanDeltaRatio > 0.12) {
            signal = "Рынок неоднородный: сначала смотрите медиану, затем историю и сегменты.";
        } else if (totalResults > analyzedCount * 1.6) {
            signal = "Часть рынка без цены, ориентируйтесь на медиану и полный список объявлений.";
        }

        elements.summaryQuery.textContent = query;
        elements.summarySignal.textContent = signal;
        elements.summaryMedian.textContent = formatPrice(state.stats.median);
        elements.summaryMarketTotal.textContent = String(totalResults || 0);
        elements.summaryCoverage.textContent =
            `${analyzedCount} / ${totalResults}`;
        elements.summaryStrip.hidden = false;
    }

    function renderHelper() {
        const shouldShow =
            !state.loading &&
            !state.error &&
            !state.stats &&
            state.activeView !== "tracking" &&
            state.activeView !== "monitoring" &&
            state.activeView !== "deals";
        elements.helperPanel.hidden = !shouldShow;
    }

    function renderViews() {
        for (const [name, panel] of Object.entries(elements.views)) {
            panel.hidden = state.activeView !== name;
        }
    }

    function heroStat(value, suffix) {
        return html`<span class="hero-stat"><span class="hero-stat-val mono">${value}</span> ${suffix}</span>`;
    }

    function renderTrackingHeroStats() {
        if (!elements.trackingHeroStats) return;
        setHtml(elements.trackingHeroStats, [
            heroStat(state.trackers.length, "трекеров"),
            heroStat(state.trackerEvents.length, "событий"),
        ]);
    }

    function renderCheapHeroStats() {
        if (!elements.cheapHeroStats) return;
        let cheapCount = state.dealListings.length;
        if (isListingsLoading()) {
            cheapCount = "…";
        } else if (loadError("deals")) {
            cheapCount = "—";
        }
        const range = state.discountFromPercent === state.discountToPercent
            ? `${state.discountFromPercent}%`
            : `${state.discountFromPercent}-${state.discountToPercent}%`;
        setHtml(elements.cheapHeroStats, [
            heroStat(cheapCount, "лотов дешевле рынка"),
            html`<span class="hero-stat">диапазон <span class="hero-stat-val mono">${range}</span></span>`,
        ]);
    }

    function renderMonitoringHeroStats() {
        if (!elements.monitoringHeroStats) return;
        const watchCount = state.watchlist.length;
        const riskCount = state.watchlist
            .filter((item) => MARKET_RISK_STATUSES.includes(item.market_status)).length;
        setHtml(elements.monitoringHeroStats, [
            heroStat(watchCount, "объявлений"),
            heroStat(riskCount, "сигналов"),
        ]);
    }

    function renderDealsHeroStats() {
        if (!elements.dealsHeroStats) return;
        const activeLeads = state.leads.filter((lead) => ACTIVE_LEAD_STATUSES.includes(lead.status)).length;
        const totalLeads = state.leads.length;
        const oppCount = (state.opportunityBoard.items || []).length;
        setHtml(elements.dealsHeroStats, [
            heroStat(activeLeads, `активных из ${totalLeads}`),
            heroStat(oppCount, "кандидатов"),
        ]);
    }

    function renderSortButtons() {
        for (const button of elements.sortButtons) {
            button.classList.toggle("active", button.dataset.sort === state.sort);
        }
    }

    function renderDiscountButtons() {
        for (const button of elements.discountButtons) {
            const from = Number(button.dataset.discountFrom);
            const to = Number(button.dataset.discountTo);
            button.classList.toggle(
                "active",
                from === state.discountFromPercent && to === state.discountToPercent
            );
        }
    }

    function renderTrackerEventFilters() {
        // Filter labels and counts are now rendered inside renderTrackerEvents()
        // This function only toggles active state for standalone calls
        for (const button of elements.trackerEventFilterButtons) {
            button.classList.toggle("active", button.dataset.eventFilter === state.trackerEventFilter);
        }
    }

    function renderHistoryRangeButtons() {
        if (elements.historyBadge) {
            elements.historyBadge.textContent = `${state.historyDays} дней`;
        }
        for (const button of elements.historyRangeButtons) {
            button.classList.toggle(
                "active",
                Number(button.dataset.historyDays) === state.historyDays
            );
        }
    }

    function renderDealInputs() {
        setFieldValue(elements.dealFromInput, String(state.discountFromPercent));
        setFieldValue(elements.dealToInput, String(state.discountToPercent));
    }

    function renderTrackerInputs() {
        setFieldValue(elements.trackerMinDiscountInput, String(state.trackerMinDiscountPercent ?? 10));
        setFieldValue(elements.trackerMaxPriceInput, state.trackerMaxPriceByn ?? "");
        if (elements.trackerExcludeDuplicatesToggle) {
            elements.trackerExcludeDuplicatesToggle.checked = Boolean(state.trackerExcludeDuplicates);
        }
        setFieldValue(elements.trackerSellerSelect, state.trackerSellerType || "");
        setFieldValue(elements.trackerConditionSelect, state.trackerCondition || "");
        setFieldValue(elements.trackerRegionInput, state.trackerRegionName || "");
        setFieldValue(elements.trackerConfigInput, state.trackerConfigKeyword || "");
    }

    function verdictClassName(verdict) {
        const map = {
            "Забирать": "zabirat",
            "Смотреть": "smotret",
            "Норм": "norm",
            "Мимо": "mimo",
        };
        return map[verdict] || "neutral";
    }

    function comparisonDelta(current, base) {
        if (!base || !current) {
            return null;
        }
        const delta = ((current - base) / base) * 100;
        if (Math.abs(delta) < 0.5) {
            return { text: "≈ рынок", className: "" };
        }
        return {
            text: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`,
            className: delta > 0 ? "positive" : "negative",
        };
    }

    function clearComparisonOutput() {
        elements.comparisonSummary.hidden = true;
        elements.comparisonSummary.innerHTML = "";
        elements.comparisonGrid.innerHTML = "";
    }

    function renderComparison() {
        const query = currentQuery();
        if (!query) {
            elements.comparisonSection.hidden = true;
            clearComparisonOutput();
            return;
        }

        elements.comparisonSection.hidden = false;
        setFieldValue(elements.compareInput, state.comparisonQuery);
        elements.compareButton.disabled =
            state.comparisonLoading || !state.comparisonQuery.trim() || !query;

        if (state.comparisonLoading) {
            elements.comparisonNote.textContent = "Сравниваю запросы...";
            clearComparisonOutput();
            return;
        }

        if (!state.comparisonQuery.trim()) {
            elements.comparisonNote.textContent =
                "Сравните текущий запрос с другим товаром или другой конфигурацией.";
            clearComparisonOutput();
            return;
        }

        if (!state.comparisonItems.length) {
            elements.comparisonNote.textContent =
                "Введите до двух дополнительных запросов через запятую и нажмите «Сравнить».";
            clearComparisonOutput();
            return;
        }

        const [baseItem, ...otherItems] = state.comparisonItems;
        const baseMedian = Number(baseItem?.median || 0);
        let summary = "Сравнение собрано по медиане, дешёвым лотам, размеру рынка и лучшему текущему офферу.";
        const deltas = otherItems
            .map((item) => {
                const median = Number(item.median || 0);
                if (!baseMedian || !median) {
                    return null;
                }
                const delta = ((median - baseMedian) / baseMedian) * 100;
                const direction = delta > 0 ? "дороже" : "дешевле";
                return `${item.query} ${direction} на ${Math.abs(delta).toFixed(1)}%`;
            })
            .filter(Boolean);
        if (deltas.length) {
            summary = deltas.join(" · ");
        }
        elements.comparisonNote.textContent = summary;
        const compareSummaryItems = [
            `${state.comparisonItems.length} запроса в работе`,
            `${otherItems.reduce((sum, item) => sum + Number(item.cheap_count || 0), 0)} дешёвых лотов в сравнении`,
            `рынок ${state.comparisonItems.map((item) => item.total_results || 0).join(" / ")}`,
        ];
        setHtml(
            elements.comparisonSummary,
            compareSummaryItems.map((item) => html`<span class="compare-summary-chip">${item}</span>`)
        );
        elements.comparisonSummary.hidden = false;

        setHtml(elements.comparisonGrid, state.comparisonItems.map((item, index) => {
            const isBase = index === 0;
            const bestListing = item.best_listing || null;
            const medianDelta = isBase
                ? { text: "база", className: "" }
                : comparisonDelta(Number(item.median || 0), baseMedian);
            const trendPercent = Number(item.trend_percent);
            const trend = item.trend_percent == null || !Number.isFinite(trendPercent)
                ? "нет истории"
                : `${trendPercent > 0 ? "+" : ""}${trendPercent.toFixed(1)}%`;
            const bestText = bestListing
                ? `${bestListing.title} · ${formatListingPrice(bestListing.price)}`
                : "Лучший оффер пока не найден";
            return html`
                <article class="compare-card">
                    <span class="compare-kicker">${isBase ? "База" : "Сравнение"}</span>
                    <strong class="compare-query">${item.query}</strong>
                    <div class="compare-deltas">
                        <span class="compare-delta-chip ${medianDelta?.className || ""}">
                            медиана ${medianDelta?.text || "—"}
                        </span>
                        <span class="compare-delta-chip">
                            тренд ${trend}
                        </span>
                    </div>
                    <div class="compare-metrics">
                        <div class="compare-metric">
                            <span class="compare-label">Медиана</span>
                            <strong class="compare-value mono">${formatPrice(item.median)}</strong>
                        </div>
                        <div class="compare-metric">
                            <span class="compare-label">Дешёвые лоты</span>
                            <strong class="compare-value mono">${item.cheap_count || 0}</strong>
                        </div>
                        <div class="compare-metric wide">
                            <span class="compare-label">Размер рынка</span>
                            <strong class="compare-value mono">${item.total_results || 0}</strong>
                            <span class="compare-meta">${bestText}</span>
                        </div>
                    </div>
                </article>
            `;
        }));
    }

    function renderStats() {
        if (!state.stats) {
            elements.statsSection.hidden = true;
            elements.chartSection.hidden = true;
            destroyChart();
            return;
        }

        elements.stats.median.textContent = formatPrice(state.stats.median);
        elements.stats.mean.textContent = formatPrice(state.stats.mean);
        elements.stats.min.textContent = formatPrice(state.stats.min);
        elements.stats.max.textContent = formatPrice(state.stats.max);
        if (state.stats.fair_price_from != null && state.stats.fair_price_to != null) {
            setHtml(elements.stats.fairRange, html`
                <span class="stat-range-item">${formatPrice(state.stats.fair_price_from)}</span>
                <span class="stat-range-sep">-</span>
                <span class="stat-range-item">${formatPrice(state.stats.fair_price_to)}</span>
            `);
        } else {
            elements.stats.fairRange.textContent = "—";
        }
        elements.stats.coverage.textContent =
            `${state.stats.analyzed_count || state.stats.count || 0} / ${state.stats.total_results || 0}`;
        elements.marketTotalBadge.textContent = `${state.stats.total_results || 0} на рынке`;
        elements.statsSection.hidden = false;
        elements.chartSection.hidden = !(Number(state.stats.count) > 0);
        if (elements.chartSection.hidden || !state.panels.distribution) {
            destroyChart();
        } else {
            // Re-draws the distribution only when the stats (or currency/theme) changed.
            renderChart();
        }
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
            const totalAnalyzed = Number(state.stats?.analyzed_count || state.stats?.count || 0);
            const share = totalAnalyzed
                ? Math.round((Number(segment.data.count || 0) / totalAnalyzed) * 100)
                : 0;
            const marketMedian = Number(state.stats?.median || 0);
            const segmentMedian = Number(segment.data.median || 0);
            const delta = marketMedian && segmentMedian
                ? ((segmentMedian - marketMedian) / marketMedian) * 100
                : 0;
            const deltaClassName = Math.abs(delta) < 0.5 ? "neutral" : (delta >= 0 ? "over" : "under");
            const deltaText = Math.abs(delta) < 0.5
                ? "≈ рынок"
                : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}% к рынку`;
            const card = document.createElement("div");
            card.className = "seg-card";
            setHtml(card, html`
                <div class="seg-head">
                    <span class="seg-pill ${segment.type}">${segment.typeLabel}</span>
                    <span class="seg-seller">${segment.sellerLabel}</span>
                </div>
                <span class="seg-price mono">${formatPrice(segment.data.median)}</span>
                <div class="seg-meta-row">
                    <span class="seg-count">${segment.data.count} с ценой</span>
                    <span class="seg-share">${share}% выборки</span>
                </div>
                <span class="seg-delta ${deltaClassName}">${deltaText}</span>
            `);
            elements.segmentsGrid.appendChild(card);
        }

        elements.segmentsSection.hidden = false;
    }

    function renderGeography() {
        elements.geographyGrid.innerHTML = "";
        if (!state.geography.length) {
            elements.geographySection.hidden = true;
            return;
        }

        for (const region of state.geography) {
            const card = document.createElement("div");
            card.className = "geo-card";
            setHtml(card, html`
                <div class="geo-head">
                    <strong class="geo-name">${region.region_name}</strong>
                    <span class="geo-share">${region.share_percent}% выборки</span>
                </div>
                <span class="geo-price mono">${formatPrice(region.median)}</span>
                <div class="geo-meta">
                    <span>${region.count} с ценой</span>
                    <span>ср. ${formatPrice(region.mean)}</span>
                </div>
            `);
            elements.geographyGrid.appendChild(card);
        }

        elements.geographySection.hidden = false;
    }

    function buildListingNode(item) {
        const listing = document.createElement("article");
        listing.className = "listing";

        const condition = item.condition
            ? html`<span class="tag">${formatCondition(item.condition)}</span>`
            : "";
        const seller = item.seller_type
            ? html`<span class="tag">${formatSeller(item.seller_type)}</span>`
            : "";
        const delta = formatDelta(item.price_vs_median);
        const deltaMarkup = delta
            ? html`<span class="listing-badge ${deltaClass(item.price_vs_median)}">${delta}</span>`
            : "";
        const fairMarkup = item.fair_price_label
            ? html`<span class="listing-badge ${item.fair_price_band || "neutral"}">${item.fair_price_label}</span>`
            : "";
        const verdictMarkup = item.deal_verdict
            ? html`<span class="listing-badge verdict-${verdictClassName(item.deal_verdict)}">${item.deal_verdict}</span>`
            : "";
        const duplicateMarkup = item.is_duplicate
            ? html`<span class="listing-badge warn">Дубль${item.duplicate_count > 1 ? ` ×${item.duplicate_count + 1}` : ""}</span>`
            : "";
        const anomalyMarkup = (item.anomaly_labels || [])
            .map((label) => html`<span class="listing-badge warn">${label}</span>`);
        const thumbnail = safeUrl(item.thumbnail);
        const thumbMarkup = thumbnail
            ? html`<img class="listing-thumb" src="${thumbnail}" alt="" loading="lazy">`
            : html`<div class="listing-thumb placeholder">Нет фото</div>`;
        const badges = [verdictMarkup, deltaMarkup, fairMarkup, duplicateMarkup, ...anomalyMarkup]
            .filter(Boolean);

        setHtml(listing, html`
            <div class="listing-top">
                ${thumbMarkup}
                <div class="listing-body">
                    <span class="listing-name">${item.title}</span>
                    <div class="listing-tags">${condition}${seller}</div>
                    <span class="listing-price mono">${formatListingPrice(item.price)}</span>
                </div>
            </div>
            ${badges.length ? html`<div class="listing-badges">${badges}</div>` : ""}
            <div class="listing-actions">
                <button class="listing-btn" data-role="detail" type="button">Подробнее</button>
                <button class="listing-btn" data-role="lead" type="button">В покупки</button>
                <button class="listing-btn" data-role="watch" type="button">Следить</button>
                ${externalLink(item.link, "listing-btn listing-btn--accent", "Kufar")}
            </div>
        `);

        listing.querySelector('[data-role="detail"]')?.addEventListener("click", () => {
            void actions.openListingDetail(item);
        });
        listing.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
            void actions.addLeadFromListing(item);
        });
        listing.querySelector('[data-role="watch"]')?.addEventListener("click", () => {
            void actions.addWatchlistFromListing(item);
        });
        return listing;
    }

    function renderListingsCollection(items, container, badge, emptyText, totalOverride = null) {
        container.innerHTML = "";
        container.removeAttribute("aria-busy");
        if (!items.length) {
            container.appendChild(buildEmptyNote(emptyText));
            if (badge) {
                badge.textContent = "0";
            }
            return false;
        }

        for (const item of items) {
            container.appendChild(buildListingNode(item));
        }

        if (badge) {
            const total = totalOverride ?? state.stats?.total_results ?? items.length;
            badge.textContent = `${items.length} из ${total}`;
        }
        return true;
    }

    // Draws the loading or error state of a listings collection; returns true when drawn.
    function renderCollectionStatus(container, badge, { loading, error, loadingText, retry }) {
        if (loading) {
            container.innerHTML = "";
            container.setAttribute("aria-busy", "true");
            container.appendChild(buildEmptyNote(loadingText));
            if (badge) {
                badge.textContent = "…";
            }
            return true;
        }
        if (error) {
            container.innerHTML = "";
            container.removeAttribute("aria-busy");
            container.appendChild(buildErrorNote(error, retry));
            if (badge) {
                badge.textContent = "—";
            }
            return true;
        }
        return false;
    }

    function searchFailure() {
        return !state.stats && typeof state.error === "string" && state.error.trim()
            ? state.error.trim()
            : null;
    }

    function renderListings() {
        const query = currentQuery();
        elements.listingsSection.hidden = !query;
        if (!query) {
            elements.listingsList.innerHTML = "";
            elements.listingsTotalBadge.textContent = "";
            return;
        }
        const handled = renderCollectionStatus(elements.listingsList, elements.listingsTotalBadge, {
            loading: isListingsLoading(),
            error: loadError("listings") || searchFailure(),
            loadingText: "Загружаю объявления…",
            retry: () => actions.loadListings(),
        });
        if (handled) {
            return;
        }
        renderListingsCollection(
            state.listings,
            elements.listingsList,
            elements.listingsTotalBadge,
            state.strictSearch
                ? "По этому запросу объявлений нет. Попробуйте отключить строгий поиск."
                : "По этому запросу объявлений нет."
        );
    }

    function renderDeals() {
        const query = currentQuery();
        elements.dealsSection.hidden = !query;
        renderCheapHeroStats();
        if (!query) {
            elements.dealsList.innerHTML = "";
            elements.dealsTotalBadge.textContent = "";
            return;
        }
        const handled = renderCollectionStatus(elements.dealsList, elements.dealsTotalBadge, {
            loading: isListingsLoading(),
            error: loadError("deals") || searchFailure(),
            loadingText: "Ищу лоты дешевле рынка…",
            retry: () => actions.loadDeals(),
        });
        if (handled) {
            return;
        }
        const rangeLabel = `${state.discountFromPercent}-${state.discountToPercent}`;
        renderListingsCollection(
            state.dealListings,
            elements.dealsList,
            elements.dealsTotalBadge,
            `Нет лотов в диапазоне ${rangeLabel}% ниже медианы. Попробуйте расширить диапазон или другой запрос.`,
            state.stats?.total_results || state.listings.length || state.dealListings.length
        );
    }

    function buildSignalRow(signal, onOpen = null) {
        const row = document.createElement("div");
        row.className = "tracker-row signal-row";
        setHtml(row, html`
            <div class="tracker-row-main">
                <strong class="tracker-query">${signal.title}</strong>
                <span class="tracker-meta mono">${signal.subtitle} • ${signal.metric}</span>
            </div>
            ${onOpen ? html`
                <div class="tracker-row-actions">
                    <button class="ghost-btn small" data-role="open" type="button">Открыть</button>
                </div>
            ` : ""}
        `);
        if (onOpen) {
            row.querySelector('[data-role="open"]')?.addEventListener("click", onOpen);
        }
        return row;
    }

    function renderOpportunityBoard() {
        elements.opportunityBoardList.innerHTML = "";
        elements.opportunityBoardDrops.innerHTML = "";
        elements.opportunityBoardRare.innerHTML = "";
        elements.opportunityBoardSignals.innerHTML = "";
        renderDealsHeroStats();

        if (!hasTelegramInitData()) {
            if (elements.opportunityBoardNote) {
                elements.opportunityBoardNote.textContent =
                    "Показываем лучшие дешёвые лоты по сохранённым поискам.";
            }
            elements.opportunityBoardList.appendChild(
                buildEmptyNote("Доска возможностей доступна внутри Telegram Mini App.")
            );
            return;
        }

        const boardError = loadError("board");
        if (boardError) {
            if (elements.opportunityBoardNote) {
                elements.opportunityBoardNote.textContent = "Не удалось загрузить доску возможностей.";
            }
            elements.opportunityBoardList.appendChild(
                buildErrorNote(boardError, () => actions.loadOpportunityBoard())
            );
            return;
        }

        const board = state.opportunityBoard || {};
        const items = board.items || [];
        const drops = board.top_price_drops || [];
        const rare = board.rare_opportunities || [];
        const marketSignals = board.market_signals || [];
        const boardCurrency = board.currency || state.currency;

        if (elements.opportunityBoardNote) {
            elements.opportunityBoardNote.textContent = items.length || drops.length || rare.length
                ? `${items.length} кандидатов на выкуп, ${drops.length} падений цены, ${rare.length} редких офферов.`
                : "Показываем лучшие дешёвые лоты по сохранённым поискам.";
        }

        function buildOpportunityCard(item, extraBadge = "") {
            const card = document.createElement("article");
            card.className = "opportunity-card";
            const listing = item.listing || {};
            const reasons = (listing.deal_reasons || []).join(" · ");
            const badge = item.signal_label || extraBadge;
            const verdict = listing.deal_verdict || "Смотреть";
            setHtml(card, html`
                <div class="opportunity-top">
                    <span class="badge">${item.saved_search_name}</span>
                    <span class="listing-signal verdict verdict-${verdictClassName(verdict)}">${verdict} · ${Math.round(listing.deal_score || 0)}</span>
                </div>
                <strong class="opportunity-title">${listing.title}</strong>
                <div class="opportunity-meta">
                    <span class="mono">${formatListingPrice(listing.price, boardCurrency)}</span>
                    <span>${listing.region_name || "Без региона"}</span>
                    ${badge ? html`<span class="board-inline-flag">${badge}</span>` : ""}
                </div>
                ${reasons ? html`<p class="opportunity-copy">${reasons}</p>` : ""}
                <div class="listing-actions">
                    <button class="ghost-btn small" data-role="open-query" type="button">Открыть запрос</button>
                    <button class="ghost-btn small" data-role="open-detail" type="button">Подробнее</button>
                    <button class="ghost-btn small" data-role="lead" type="button">В покупки</button>
                    <button class="ghost-btn small" data-role="watch" type="button">Следить</button>
                    ${externalLink(listing.link, "primary-link small", "Kufar")}
                </div>
            `);
            card.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
                showToast("Загружаю...");
                void actions.openOpportunityQuery(item);
            });
            card.querySelector('[data-role="open-detail"]')?.addEventListener("click", () => {
                showToast("Открываю...");
                void actions.openOpportunityDetail(item);
            });
            card.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
                void actions.addLeadFromListing(item.listing, "opportunity_board", item.query);
            });
            card.querySelector('[data-role="watch"]')?.addEventListener("click", () => {
                void actions.addWatchlistFromListing(item.listing, item.query);
            });
            return card;
        }

        if (!items.length) {
            elements.opportunityBoardList.appendChild(
                buildEmptyNote("Добавьте сохранённые поиски, чтобы увидеть лучшие лоты.")
            );
        } else {
            for (const item of items) {
                elements.opportunityBoardList.appendChild(buildOpportunityCard(item));
            }
        }

        if (!rare.length) {
            elements.opportunityBoardRare.appendChild(buildEmptyNote("Редких офферов пока нет."));
        } else {
            for (const item of rare) {
                elements.opportunityBoardRare.appendChild(buildOpportunityCard(item, "Редкий оффер"));
            }
        }

        if (!drops.length) {
            elements.opportunityBoardDrops.appendChild(buildEmptyNote("Пока нет свежих падений цены."));
        } else {
            for (const signal of drops) {
                elements.opportunityBoardDrops.appendChild(buildSignalRow(signal, () => {
                    showToast("Загружаю...");
                    void actions.searchFor(signal.query, {
                        strictSearch: Boolean(signal.strict_mode),
                        view: "overview",
                    });
                }));
            }
        }

        if (!marketSignals.length) {
            elements.opportunityBoardSignals.appendChild(buildEmptyNote(
                "Сигналы рынка появятся, когда накопится больше сохранённых поисков и истории."
            ));
        } else {
            for (const signal of marketSignals) {
                elements.opportunityBoardSignals.appendChild(buildSignalRow(signal));
            }
        }
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

    function renderLeadFilters() {
        for (const button of elements.leadFilterButtons || []) {
            button.classList.toggle("active", button.dataset.leadFilter === state.leadFilter);
        }
    }

    function renderWatchlistFilters() {
        for (const button of elements.watchlistFilterButtons || []) {
            button.classList.toggle("active", button.dataset.watchFilter === state.watchlistFilter);
        }
    }

    const WORKFLOW_LABELS = Object.fromEntries([...LEAD_STATUS_OPTIONS, ...WATCHLIST_STATUS_OPTIONS]);

    function workflowLabel(value) {
        return WORKFLOW_LABELS[value] || value || "Без статуса";
    }

    function marketLabel(value) {
        const labels = {
            active: "На рынке",
            price_drop: "Падение цены",
            duplicate: "Есть дубли",
            missing: "Пропало",
        };
        return labels[value] || value || "Без сигнала";
    }

    function statusSelect(options, currentValue, ariaLabel) {
        const known = options.some(([value]) => value === currentValue);
        const allOptions = known || !currentValue
            ? options
            : [...options, [currentValue, workflowLabel(currentValue)]];
        return html`
            <select class="deal-select" data-role="status" aria-label="${ariaLabel}">
                ${allOptions.map(([value, label]) => html`<option value="${value}">${label}</option>`)}
            </select>
        `;
    }

    function leadMatchesFilter(lead) {
        if (state.leadFilter === "all") {
            return true;
        }
        if (state.leadFilter === "active") {
            return ACTIVE_LEAD_STATUSES.includes(lead.status);
        }
        if (state.leadFilter === "buy") {
            return ["bought", "reselling"].includes(lead.status);
        }
        if (state.leadFilter === "done") {
            return ["sold", "skipped"].includes(lead.status);
        }
        return true;
    }

    function watchlistMatchesFilter(item) {
        if (state.watchlistFilter === "all") {
            return true;
        }
        if (state.watchlistFilter === "attention") {
            return MARKET_RISK_STATUSES.includes(item.market_status) ||
                ["reviewing", "in_progress"].includes(item.workflow_status);
        }
        if (state.watchlistFilter === "watching") {
            return item.workflow_status === "watching";
        }
        if (state.watchlistFilter === "risk") {
            return MARKET_RISK_STATUSES.includes(item.market_status);
        }
        return true;
    }

    function leadSortValue(item) {
        const order = {
            in_progress: 0,
            negotiating: 1,
            reviewing: 2,
            new: 3,
            deferred: 4,
            bought: 5,
            reselling: 6,
            sold: 7,
            skipped: 8,
        };
        return order[item.status] ?? 99;
    }

    function watchlistSortValue(item) {
        const marketOrder = {
            price_drop: 0,
            missing: 1,
            duplicate: 2,
            active: 3,
        };
        const workflowOrder = {
            in_progress: 0,
            reviewing: 1,
            watching: 2,
            deferred: 3,
            skipped: 4,
        };
        return `${marketOrder[item.market_status] ?? 9}:${workflowOrder[item.workflow_status] ?? 9}`;
    }

    function buildLeadRow(lead) {
        const row = document.createElement("div");
        row.className = "tracker-row workflow-row";
        row.dataset.rowId = String(lead.id);
        const priceText = hasPrice(lead.price_byn) ? formatByn(lead.price_byn) : "без цены";
        const targetText = hasPrice(lead.target_resale_byn) ? ` • цель ${formatByn(lead.target_resale_byn)}` : "";
        setHtml(row, html`
            <div class="tracker-row-main">
                <strong class="tracker-query">${lead.title}</strong>
                <span class="tracker-meta mono">${workflowLabel(lead.status)} • ${priceText}${targetText}</span>
            </div>
            <div class="tracker-row-actions">
                ${statusSelect(LEAD_STATUS_OPTIONS, lead.status || "new", "Статус сделки")}
                ${externalLink(lead.link, "primary-link small", "Kufar")}
            </div>
            <div class="workflow-fields">
                <label class="workflow-field">
                    <span class="workflow-field-label">Цель</span>
                    <div class="deal-range-input-wrap workflow-input-wrap">
                        <input data-role="target" type="number" min="0" step="1" inputmode="numeric" placeholder="цена продажи">
                        <span>BYN</span>
                    </div>
                </label>
                <label class="workflow-field workflow-field-wide">
                    <span class="workflow-field-label">Заметка</span>
                    <div class="deal-range-input-wrap workflow-input-wrap">
                        <input data-role="notes" type="text" placeholder="позвонил, торг, забрать вечером">
                    </div>
                </label>
            </div>
        `);
        const select = row.querySelector('[data-role="status"]');
        if (select) {
            select.value = lead.status || "new";
            select.addEventListener("change", () => {
                void actions.updateLeadStatus(lead.id, select.value);
            });
        }
        const targetInput = row.querySelector('[data-role="target"]');
        if (targetInput) {
            const savedTarget = parseMoneyInput(lead.target_resale_byn ?? "");
            targetInput.value = savedTarget ?? "";
            bindCommittedInput(targetInput, savedTarget, parseMoneyInput, (nextValue) => {
                void actions.updateLeadMeta(lead.id, { target_resale_byn: nextValue });
            });
        }
        const notesInput = row.querySelector('[data-role="notes"]');
        if (notesInput) {
            const savedNotes = parseNotesInput(lead.notes);
            notesInput.value = lead.notes || "";
            bindCommittedInput(notesInput, savedNotes, parseNotesInput, (nextValue) => {
                void actions.updateLeadMeta(lead.id, { notes: nextValue });
            });
        }
        return row;
    }

    function renderLeads() {
        const focused = captureFocusedField(elements.leadInboxList);
        elements.leadInboxList.innerHTML = "";
        renderLeadFilters();
        renderDealsHeroStats();
        if (!hasTelegramInitData()) {
            elements.leadInboxNote.textContent = "Лоты, которые вы взяли в работу или решили проверить позже.";
            elements.leadInboxList.appendChild(buildEmptyNote("Сделки доступны внутри Telegram Mini App."));
            return;
        }
        const leadsError = loadError("leads");
        if (leadsError) {
            elements.leadInboxNote.textContent = "Не удалось загрузить сделки.";
            elements.leadInboxList.appendChild(buildErrorNote(leadsError, () => actions.loadLeads()));
            return;
        }
        const filteredLeads = [...state.leads]
            .filter(leadMatchesFilter)
            .sort((left, right) => {
                const rankDelta = leadSortValue(left) - leadSortValue(right);
                if (rankDelta !== 0) {
                    return rankDelta;
                }
                return String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
            });
        const activeCount = state.leads.filter((lead) => ACTIVE_LEAD_STATUSES.includes(lead.status)).length;
        if (!state.leads.length) {
            elements.leadInboxNote.textContent = "Лоты, которые вы взяли в работу или решили проверить позже.";
            elements.leadInboxList.appendChild(buildEmptyNote(
                "Сделок пока нет. Добавьте лот через поиск или раздел «Дешевле рынка»."
            ));
            return;
        }
        elements.leadInboxNote.textContent =
            `${filteredLeads.length} из ${state.leads.length} лотов в фильтре · ${activeCount} активных`;
        if (!filteredLeads.length) {
            const filterLabel = LEAD_FILTER_LABELS[state.leadFilter] || state.leadFilter;
            elements.leadInboxList.appendChild(buildEmptyNote(
                `В фильтре «${filterLabel}» сделок нет. Всего сделок: ${state.leads.length}, выберите «Все», чтобы увидеть их.`
            ));
            return;
        }
        for (const lead of filteredLeads) {
            elements.leadInboxList.appendChild(buildLeadRow(lead));
        }
        restoreFocusedField(elements.leadInboxList, focused);
    }

    function buildWatchlistRow(item) {
        const row = document.createElement("div");
        row.className = "tracker-row workflow-row";
        row.dataset.rowId = String(item.id);
        const priceDelta = Number(item.price_delta_byn);
        const delta = item.price_delta_byn == null || !Number.isFinite(priceDelta)
            ? "без изменений"
            : `${priceDelta > 0 ? "+" : ""}${Math.round(priceDelta)} BYN`;
        setHtml(row, html`
            <div class="tracker-row-main">
                <strong class="tracker-query">${item.title}</strong>
                <span class="tracker-meta mono">${workflowLabel(item.workflow_status)} • ${marketLabel(item.market_status)} • ${delta}</span>
            </div>
            <div class="tracker-row-actions">
                ${statusSelect(WATCHLIST_STATUS_OPTIONS, item.workflow_status || "watching", "Статус лота")}
                <button class="ghost-btn small" data-role="lead" type="button">В покупки</button>
                <button class="ghost-btn small danger" data-role="delete" type="button">Удалить</button>
            </div>
            <div class="workflow-fields">
                <label class="workflow-field workflow-field-wide">
                    <span class="workflow-field-label">Заметка</span>
                    <div class="deal-range-input-wrap workflow-input-wrap">
                        <input data-role="notes" type="text" placeholder="что проверить при следующем созвоне">
                    </div>
                </label>
            </div>
        `);
        const select = row.querySelector('[data-role="status"]');
        if (select) {
            select.value = item.workflow_status || "watching";
            select.addEventListener("change", () => {
                void actions.updateWatchlistStatus(item.id, select.value);
            });
        }
        row.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
            void actions.promoteWatchlistToLead(item);
        });
        row.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
            void actions.deleteWatchlistItem(item.id);
        });
        const notesInput = row.querySelector('[data-role="notes"]');
        if (notesInput) {
            const savedNotes = parseNotesInput(item.notes);
            notesInput.value = item.notes || "";
            bindCommittedInput(notesInput, savedNotes, parseNotesInput, (nextValue) => {
                void actions.updateWatchlistMeta(item.id, { notes: nextValue });
            });
        }
        return row;
    }

    function renderWatchlist() {
        const focused = captureFocusedField(elements.watchlistList);
        elements.watchlistList.innerHTML = "";
        renderWatchlistFilters();
        renderMonitoringHeroStats();
        const defaultNote = "Добавляйте объявления через кнопку «Следить» в карточке или списке.";
        if (!hasTelegramInitData()) {
            elements.watchlistNote.textContent = defaultNote;
            elements.watchlistList.appendChild(
                buildEmptyNote("Отслеживание лотов доступно внутри Telegram Mini App.")
            );
            return;
        }
        const watchlistError = loadError("watchlist");
        if (watchlistError) {
            elements.watchlistNote.textContent = "Не удалось загрузить избранное.";
            elements.watchlistList.appendChild(buildErrorNote(watchlistError, () => actions.loadWatchlist()));
            return;
        }
        if (!state.watchlist.length) {
            elements.watchlistNote.textContent = defaultNote;
            elements.watchlistList.appendChild(buildEmptyNote("Сохранённых лотов пока нет."));
            return;
        }
        const filteredWatchlist = [...state.watchlist]
            .filter(watchlistMatchesFilter)
            .sort((left, right) => {
                const rankDelta = watchlistSortValue(left).localeCompare(watchlistSortValue(right));
                if (rankDelta !== 0) {
                    return rankDelta;
                }
                return String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
            });
        const riskCount = state.watchlist
            .filter((item) => MARKET_RISK_STATUSES.includes(item.market_status)).length;
        elements.watchlistNote.textContent =
            `${filteredWatchlist.length} из ${state.watchlist.length} объявлений в фильтре. Сигналов: ${riskCount}.`;
        if (!filteredWatchlist.length) {
            const filterLabel = WATCHLIST_FILTER_LABELS[state.watchlistFilter] || state.watchlistFilter;
            elements.watchlistList.appendChild(buildEmptyNote(
                `В фильтре «${filterLabel}» лотов нет. Всего сохранено: ${state.watchlist.length}, выберите «Все», чтобы увидеть их.`
            ));
            return;
        }
        for (const item of filteredWatchlist) {
            elements.watchlistList.appendChild(buildWatchlistRow(item));
        }
        restoreFocusedField(elements.watchlistList, focused);
    }

    function renderTrackers() {
        elements.trackersList.innerHTML = "";
        renderTrackingHeroStats();

        if (!hasTelegramInitData()) {
            elements.trackersList.appendChild(
                buildEmptyNote("Откройте Mini App внутри Telegram, чтобы управлять трекерами.")
            );
            return;
        }

        if (!state.trackers.length) {
            elements.trackersList.appendChild(buildEmptyNote("Активных трекеров пока нет."));
            return;
        }

        for (const tracker of state.trackers) {
            const row = document.createElement("div");
            row.className = "tracker-row";
            const trackerMeta = [
                `каждые ${tracker.interval_min} мин`,
                tracker.strict_mode ? "строгий" : "",
                tracker.min_discount_percent ? `от -${Math.round(tracker.min_discount_percent)}%` : "",
                tracker.max_price_byn ? `до ${Math.round(tracker.max_price_byn)} BYN` : "",
                tracker.exclude_duplicates ? "без дублей" : "",
                tracker.seller_type === "Частное лицо" ? "частники" : "",
            ].filter(Boolean);
            setHtml(row, html`
                <div class="tracker-row-main">
                    <strong class="tracker-query">${tracker.query}</strong>
                    <span class="tracker-meta mono">${trackerMeta.join(" • ")}</span>
                </div>
                <div class="tracker-row-actions">
                    <button class="ghost-btn small" data-role="open" type="button">Открыть</button>
                    <button class="ghost-btn small danger" data-role="delete" type="button">Удалить</button>
                </div>
            `);
            row.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                showToast("Загружаю...");
                state.trackerMinDiscountPercent = Math.round(tracker.min_discount_percent ?? 10);
                state.trackerMaxPriceByn = tracker.max_price_byn ?? null;
                state.trackerExcludeDuplicates = Boolean(tracker.exclude_duplicates);
                state.trackerSellerType = tracker.seller_type || "";
                state.trackerCondition = tracker.condition || "";
                state.trackerRegionName = tracker.region_name || "";
                state.trackerConfigKeyword = tracker.config_keyword || "";
                renderTrackerInputs();
                void actions.searchFor(tracker.query, { strictSearch: Boolean(tracker.strict_mode) });
            });
            row.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteTracker(tracker.id);
            });
            elements.trackersList.appendChild(row);
        }
    }

    function buildTrackerEventRow(event) {
        const row = document.createElement("article");
        const isPriceDrop = event.event_type === "price_drop";
        row.className = `tracker-event-row${isPriceDrop ? " price-drop" : ""}`;
        const typeLabel = isPriceDrop ? "Падение цены" : "Новый лот";
        const typeClass = isPriceDrop ? "drop" : "new";
        const meta = [];
        if (event.query) {
            meta.push(event.strict_mode ? `${event.query} • строгий` : event.query);
        }
        if (event.price_byn) {
            meta.push(`${Math.round(event.price_byn)} р.`);
        }
        if (event.delta_byn) {
            meta.push(`-${Math.round(event.delta_byn)} р.`);
        }
        setHtml(row, html`
            <div class="tracker-event-top">
                <span class="tracker-event-type ${typeClass}">${typeLabel}</span>
                <span class="tracker-event-time mono">${formatDate(event.created_at)}</span>
            </div>
            <strong class="tracker-event-title">${event.title}</strong>
            <div class="tracker-event-meta">${meta.map((item) => html`<span>${item}</span>`)}</div>
            <div class="listing-actions">
                <button class="listing-btn" data-role="open-query" type="button">Открыть</button>
                <button class="listing-btn" data-role="lead" type="button">В покупки</button>
                ${externalLink(event.link, "listing-btn listing-btn--accent", "Kufar")}
            </div>
        `);
        row.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
            showToast("Открываю...");
            // The event carries its own query and strict mode; the global search stays untouched.
            void actions.openListingDetail(
                {
                    ad_id: event.ad_id,
                    title: event.title,
                    link: event.link,
                    price_byn: event.price_byn,
                },
                {
                    query: event.query || currentQuery(),
                    strictSearch: Boolean(event.strict_mode),
                }
            );
        });
        row.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
            void actions.addLeadFromListing(
                {
                    ad_id: event.ad_id,
                    title: event.title,
                    link: event.link,
                    price_byn: event.price_byn,
                    flip_estimates: [],
                },
                "tracker_event",
                event.query
            );
        });
        return row;
    }

    function renderTrackerEvents() {
        elements.trackerEventsList.innerHTML = "";
        renderTrackingHeroStats();

        if (!hasTelegramInitData()) {
            elements.trackerEventsList.appendChild(buildEmptyNote(
                "Откройте Mini App внутри Telegram, чтобы видеть события.",
                "tracker-event-empty"
            ));
            return;
        }

        const dropCount = state.trackerEvents.filter((e) => e.event_type === "price_drop").length;
        const newCount = state.trackerEvents.filter((e) => e.event_type === "new_listing").length;
        const totalCount = state.trackerEvents.length;

        for (const button of elements.trackerEventFilterButtons) {
            const filter = button.dataset.eventFilter;
            const count = filter === "all" ? totalCount : filter === "price_drop" ? dropCount : newCount;
            const label = filter === "all" ? "Все" : filter === "price_drop" ? "Упали в цене" : "Новые лоты";
            button.textContent = count > 0 ? `${label} (${count})` : label;
            button.classList.toggle("active", filter === state.trackerEventFilter);
        }

        const eventsError = loadError("events");
        if (eventsError) {
            elements.trackerEventsList.appendChild(buildErrorNote(eventsError, () => actions.loadTrackers()));
            return;
        }

        const filteredEvents = state.trackerEvents.filter((event) => {
            if (state.trackerEventFilter === "all") {
                return true;
            }
            return event.event_type === state.trackerEventFilter;
        });

        if (!filteredEvents.length) {
            elements.trackerEventsList.appendChild(buildEmptyNote(
                state.trackerEventFilter === "all" || !totalCount
                    ? "Событий пока нет. Они появятся после первой проверки планировщика."
                    : "По этому фильтру событий пока нет.",
                "tracker-event-empty"
            ));
            return;
        }

        for (const event of filteredEvents) {
            elements.trackerEventsList.appendChild(buildTrackerEventRow(event));
        }
    }

    // ---- Detail modal -------------------------------------------------------

    let detailReturnFocus = null;

    function buildDetailField(label, value) {
        const item = document.createElement("div");
        item.className = "detail-field";
        const labelNode = document.createElement("span");
        labelNode.className = "detail-field-label";
        labelNode.textContent = label == null ? "" : String(label);
        const valueNode = document.createElement("span");
        valueNode.className = "detail-field-value";
        valueNode.textContent = value == null ? "" : String(value);
        item.append(labelNode, valueNode);
        return item;
    }

    function restoreDetailFocus() {
        const target = detailReturnFocus;
        detailReturnFocus = null;
        if (target && target.isConnected && typeof target.focus === "function") {
            target.focus({ preventScroll: true });
        }
    }

    function renderDetailModal() {
        const wasOpen = !elements.detailModal.hidden;
        if (!state.detail) {
            elements.detailModal.hidden = true;
            if (wasOpen) {
                restoreDetailFocus();
            }
            return;
        }

        const detail = state.detail;
        const images = (detail.images || []).map(safeUrl).filter(Boolean);
        const hasImages = images.length > 0;
        const currentImage = hasImages ? images[state.detailImageIndex] || images[0] : null;

        elements.detailTitle.textContent = detail.title || "Объявление";
        elements.detailPrice.textContent = formatListingPrice(detail.price);
        const link = safeUrl(detail.link);
        if (link) {
            elements.detailLink.href = link;
        } else {
            elements.detailLink.removeAttribute("href");
        }
        // .listing-btn sets display:flex, so the hidden attribute alone does not hide it.
        elements.detailLink.hidden = !link;
        elements.detailLink.style.display = link ? "" : "none";
        elements.detailDescription.textContent = detail.description || "";
        elements.detailDescription.hidden = !detail.description;

        elements.detailProfit.innerHTML = "";
        for (const estimate of detail.flip_estimates || []) {
            // target_price is computed from BYN statistics regardless of the display currency.
            const profitPercent = Number(estimate.profit_percent) || 0;
            elements.detailProfit.appendChild(buildDetailField(
                estimate.label,
                `${formatByn(estimate.target_price)} • ${formatByn(estimate.profit_byn)} (${profitPercent > 0 ? "+" : ""}${profitPercent}%)`
            ));
        }
        elements.detailProfitBlock.hidden = (detail.flip_estimates || []).length === 0;

        elements.detailLiquidity.innerHTML = "";
        if (detail.liquidity) {
            elements.detailLiquidity.appendChild(buildDetailField(
                detail.liquidity.label,
                `${Math.round(detail.liquidity.score || 0)} • ${(detail.liquidity.reasons || []).join(" · ")}`
            ));
        }
        elements.detailLiquidityBlock.hidden = !detail.liquidity;

        const metaItems = [
            detail.category,
            detail.condition ? formatCondition(detail.condition) : "",
            detail.seller_type ? formatSeller(detail.seller_type) : "",
            detail.region_name || "",
            detail.list_time ? formatDate(detail.list_time) : "",
            detail.fair_price_label || "",
            detail.is_duplicate ? "Похоже на дубль" : "",
            ...(detail.anomaly_labels || []),
            formatDelta(detail.price_vs_median),
        ].filter(Boolean);
        setHtml(elements.detailMeta, metaItems.map((item) => html`<span class="detail-pill">${item}</span>`));

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
            const isActive = state.detailImageIndex === index;
            const button = document.createElement("button");
            button.type = "button";
            button.className = `detail-thumb${isActive ? " active" : ""}`;
            button.setAttribute("aria-label", `Фото ${index + 1} из ${images.length}`);
            button.setAttribute("aria-pressed", String(isActive));
            const thumb = document.createElement("img");
            thumb.src = image;
            thumb.alt = "";
            button.appendChild(thumb);
            button.addEventListener("click", () => {
                state.detailImageIndex = index;
                renderDetailModal();
                elements.detailThumbs.children[index]?.focus({ preventScroll: true });
            });
            elements.detailThumbs.appendChild(button);
        }

        elements.detailParams.innerHTML = "";
        const params = detail.parameters || [];
        for (const field of params) {
            elements.detailParams.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailParamsBlock.hidden = params.length === 0;

        elements.detailSeller.innerHTML = "";
        const sellerFields = detail.seller_fields || [];
        for (const field of sellerFields) {
            elements.detailSeller.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailSellerBlock.hidden = sellerFields.length === 0;
        elements.detailModal.hidden = false;

        if (!wasOpen) {
            const active = document.activeElement;
            detailReturnFocus = active && active !== document.body && !elements.detailModal.contains(active)
                ? active
                : null;
            elements.detailClose?.focus({ preventScroll: true });
        }
    }

    function closeDetailModal() {
        state.detail = null;
        state.detailImageIndex = 0;
        renderDetailModal();
    }

    // ---- Charts -------------------------------------------------------------

    // What each chart was last drawn from, to avoid re-creating it on every renderAll().
    const chartSources = {
        distribution: null,
        history: null,
    };

    function chartsAvailable() {
        return typeof Chart !== "undefined";
    }

    function currentTheme() {
        return document.documentElement.getAttribute("data-theme") || "dark";
    }

    function chartBox(canvas) {
        return canvas.closest(".chart-box") || canvas.parentElement;
    }

    function chartFallbackNote(canvas, create) {
        const box = chartBox(canvas);
        const sibling = box?.nextElementSibling;
        if (sibling && sibling.classList.contains("chart-fallback")) {
            return sibling;
        }
        if (!create || !box) {
            return null;
        }
        const note = document.createElement("p");
        note.className = "section-note chart-fallback";
        box.after(note);
        return note;
    }

    function showChartFallback(canvas, text) {
        const box = chartBox(canvas);
        if (box) {
            box.hidden = true;
        }
        const note = chartFallbackNote(canvas, true);
        if (note) {
            note.textContent = text;
            note.hidden = false;
        }
    }

    function hideChartFallback(canvas) {
        const box = chartBox(canvas);
        if (box) {
            box.hidden = false;
        }
        const note = chartFallbackNote(canvas, false);
        if (note) {
            note.hidden = true;
        }
    }

    // Releases a canvas still bound to a chart this module lost track of.
    function releaseCanvas(canvas) {
        const orphan = typeof Chart.getChart === "function" ? Chart.getChart(canvas) : null;
        orphan?.destroy();
    }

    function describeCanvas(canvas, label) {
        canvas.setAttribute("role", "img");
        canvas.setAttribute("aria-label", label);
    }

    function destroyChart() {
        if (state.chart) {
            state.chart.destroy();
            state.chart = null;
        }
        chartSources.distribution = null;
    }

    function destroyHistoryChart() {
        if (state.historyChart) {
            state.historyChart.destroy();
            state.historyChart = null;
        }
        chartSources.history = null;
    }

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

        const canvas = document.getElementById("priceChart");
        if (!canvas) return;

        const labels = ["Мин", "Q1", "Медиана", "Q3", "Макс"];
        const values = [
            state.stats.min,
            state.stats.q1,
            state.stats.median,
            state.stats.q3,
            state.stats.max,
        ];
        const description = labels
            .map((label, index) => `${label} ${formatPrice(values[index])}`)
            .join(", ");
        describeCanvas(canvas, `Распределение цен: ${description}`);

        if (!chartsAvailable()) {
            destroyChart();
            showChartFallback(canvas, `График недоступен. ${description}`);
            return;
        }
        hideChartFallback(canvas);

        const sourceKey = JSON.stringify([values, state.currency, currentTheme()]);
        if (state.chart && chartSources.distribution === sourceKey) {
            return;
        }
        destroyChart();

        const isDark = currentTheme() !== "light";
        const muted = isDark ? "rgba(136,128,120,0.6)" : "rgba(114,105,94,0.6)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";
        const amber = isDark ? "#F59E0B" : "#D97706";
        const alphas = [0.22, 0.4, 0.9, 0.4, 0.22];

        releaseCanvas(canvas);
        state.chart = new Chart(canvas, {
            type: "bar",
            data: {
                labels,
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
        chartSources.distribution = sourceKey;
    }

    function historyPoints() {
        return state.history
            .map((point) => ({
                x: new Date(point.snapshot_at).getTime(),
                y: Number(point.median),
            }))
            .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y) && point.y > 0)
            .sort((left, right) => left.x - right.x);
    }

    const axisDayFormatter = new Intl.DateTimeFormat("ru-BY", { day: "2-digit", month: "short" });
    const axisTimeFormatter = new Intl.DateTimeFormat("ru-BY", { hour: "2-digit", minute: "2-digit" });

    function renderHistorySummary(points) {
        const firstPoint = points[0];
        const lastPoint = points[points.length - 1];
        const delta = firstPoint && lastPoint && firstPoint.y
            ? ((lastPoint.y - firstPoint.y) / firstPoint.y) * 100
            : 0;
        const medians = points.map((point) => point.y);
        const summaryItems = [
            {
                label: "Сейчас",
                value: formatPrice(lastPoint?.y),
                meta: `${points.length} точек`,
            },
            {
                label: "Тренд",
                value: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`,
                meta: `${state.historyDays} дней`,
            },
            {
                label: "Диапазон",
                value: `${formatPrice(Math.min(...medians))} - ${formatPrice(Math.max(...medians))}`,
                meta: "по медиане",
            },
        ];
        setHtml(elements.historySummary, summaryItems.map((item) => html`
            <div class="history-summary-card">
                <span class="history-summary-label">${item.label}</span>
                <strong class="history-summary-value mono">${item.value}</strong>
                <span class="history-summary-meta">${item.meta}</span>
            </div>
        `));
        elements.historySummary.hidden = false;
    }

    function renderHistory() {
        const query = currentQuery();
        const points = query ? historyPoints() : [];
        const hasHistory = points.length > 0;
        elements.historySection.hidden = !query;
        renderHistoryRangeButtons();
        elements.historyEmpty.hidden = hasHistory;
        if (!hasHistory) {
            elements.historySummary.hidden = true;
            elements.historySummary.innerHTML = "";
            destroyHistoryChart();
            const canvas = document.getElementById("historyChart");
            if (canvas) {
                hideChartFallback(canvas);
                chartBox(canvas).hidden = true;
            }
            return;
        }
        renderHistorySummary(points);
        if (!state.panels.history) {
            destroyHistoryChart();
            return;
        }
        renderHistoryChart(points);
    }

    function renderHistoryChart(points = historyPoints()) {
        const canvas = document.getElementById("historyChart");
        if (!canvas || !points.length) {
            destroyHistoryChart();
            return;
        }

        const firstPoint = points[0];
        const lastPoint = points[points.length - 1];
        const spanMs = lastPoint.x - firstPoint.x;
        const showTime = spanMs <= 2 * 24 * 60 * 60 * 1000;
        const formatAxisDate = (value) => {
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) {
                return "";
            }
            return showTime
                ? `${axisDayFormatter.format(date)} ${axisTimeFormatter.format(date)}`
                : axisDayFormatter.format(date);
        };
        const medians = points.map((point) => point.y);
        const description = [
            `Динамика медианы: ${points.length} точек`,
            `с ${formatDate(firstPoint.x)} по ${formatDate(lastPoint.x)}`,
            `от ${formatPrice(Math.min(...medians))} до ${formatPrice(Math.max(...medians))}`,
            `сейчас ${formatPrice(lastPoint.y)}`,
        ].join(", ");
        describeCanvas(canvas, description);

        if (!chartsAvailable()) {
            destroyHistoryChart();
            const recent = points.slice(-5)
                .map((point) => `${formatDate(point.x)}: ${formatPrice(point.y)}`)
                .join("; ");
            showChartFallback(canvas, `График недоступен. Последние точки: ${recent}`);
            return;
        }
        hideChartFallback(canvas);

        const source = state.historyChart ? chartSources.history : null;
        if (
            source &&
            source.history === state.history &&
            source.currency === state.currency &&
            source.theme === currentTheme()
        ) {
            return;
        }
        if (state.activeView !== "overview") {
            // Drawing into a hidden view wastes work; renderAll() redraws it on return.
            destroyHistoryChart();
            return;
        }
        destroyHistoryChart();

        const isDark = currentTheme() !== "light";
        const lineColor = isDark ? "#F59E0B" : "#D97706";
        const fillColor = isDark ? "rgba(245,158,11,0.12)" : "rgba(217,119,6,0.12)";
        const muted = isDark ? "rgba(136,128,120,0.75)" : "rgba(114,105,94,0.75)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.05)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";

        releaseCanvas(canvas);
        state.historyChart = new Chart(canvas, {
            type: "line",
            data: {
                datasets: [
                    {
                        label: "Медиана",
                        // A linear time axis keeps uneven gaps between snapshots visible.
                        data: points,
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
                            title(items) {
                                return items.length ? formatDate(items[0].parsed.x) : "";
                            },
                            label(context) {
                                return ` ${formatPrice(context.parsed.y)}`;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        type: "linear",
                        min: firstPoint.x,
                        max: spanMs > 0 ? lastPoint.x : firstPoint.x + 60 * 60 * 1000,
                        grid: { display: false },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 6,
                            maxRotation: 0,
                            callback(value) {
                                return formatAxisDate(value);
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
        chartSources.history = {
            history: state.history,
            currency: state.currency,
            theme: currentTheme(),
        };
    }

    // Each step is isolated: a failing renderer (e.g. Chart.js missing) must not
    // leave the rest of the interface stale.
    function renderAll() {
        const steps = [
            renderLoading,
            renderError,
            renderHelper,
            renderSummary,
            renderCurrencyButtons,
            renderStrictSearch,
            renderViewTabs,
            renderViews,
            renderTrackingHeroStats,
            renderCheapHeroStats,
            renderMonitoringHeroStats,
            renderDealsHeroStats,
            renderSortButtons,
            renderDiscountButtons,
            renderTrackerEventFilters,
            renderHistoryRangeButtons,
            renderDealInputs,
            renderTrackerInputs,
            renderPanels,
            renderStats,
            renderHistory,
            renderComparison,
            renderSegments,
            renderGeography,
            renderListings,
            renderDeals,
            renderRates,
            renderTrackerStatus,
            renderTrackers,
            renderTrackerEvents,
            renderLeads,
            renderWatchlist,
            renderOpportunityBoard,
        ];
        for (const step of steps) {
            try {
                step();
            } catch (error) {
                console.error(`[renderAll] ${step.name} failed`, error);
            }
        }
    }

    return {
        showToast,
        escapeHtml,
        safeUrl,
        renderRates,
        renderError,
        renderLoading,
        renderCurrencyButtons,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
        renderTrackingHeroStats,
        renderCheapHeroStats,
        renderMonitoringHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderTrackerEventFilters,
        renderDealInputs,
        renderTrackerInputs,
        renderComparison,
        renderStats,
        renderSegments,
        renderGeography,
        renderListingsCollection,
        renderListings,
        renderDeals,
        renderTrackerStatus,
        renderTrackers,
        renderTrackerEvents,
        renderLeads,
        renderWatchlist,
        renderOpportunityBoard,
        destroyChart,
        destroyHistoryChart,
        renderDetailModal,
        closeDetailModal,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderAll,
    };
}
