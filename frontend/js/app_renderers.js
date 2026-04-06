/* global Chart */

function escapeHtml(str) {
    if (typeof str !== "string") return str;
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

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
            button.classList.toggle("active", button.dataset.view === state.activeView);
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
        if (!state.stats || !state.query) {
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

        elements.summaryQuery.textContent = state.query;
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
        if (elements.dealFromInput) {
            elements.dealFromInput.value = String(state.discountFromPercent);
        }
        if (elements.dealToInput) {
            elements.dealToInput.value = String(state.discountToPercent);
        }
    }

    function renderTrackerInputs() {
        if (elements.trackerMinDiscountInput) {
            elements.trackerMinDiscountInput.value = String(state.trackerMinDiscountPercent ?? 10);
        }
        if (elements.trackerMaxPriceInput) {
            elements.trackerMaxPriceInput.value = state.trackerMaxPriceByn ?? "";
        }
        if (elements.trackerExcludeDuplicatesToggle) {
            elements.trackerExcludeDuplicatesToggle.checked = Boolean(state.trackerExcludeDuplicates);
        }
        if (elements.trackerSellerSelect) {
            elements.trackerSellerSelect.value = state.trackerSellerType || "";
        }
        if (elements.trackerConditionSelect) {
            elements.trackerConditionSelect.value = state.trackerCondition || "";
        }
        if (elements.trackerRegionInput) {
            elements.trackerRegionInput.value = state.trackerRegionName || "";
        }
        if (elements.trackerConfigInput) {
            elements.trackerConfigInput.value = state.trackerConfigKeyword || "";
        }
    }

    function renderSavedSearchInputs() {
        if (elements.savedSearchGroupInput) {
            elements.savedSearchGroupInput.value = state.savedSearchGroupName || "Мои модели";
        }
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

    function renderComparison() {
        if (!state.query) {
            elements.comparisonSection.hidden = true;
            elements.comparisonGrid.innerHTML = "";
            elements.comparisonSummary.hidden = true;
            elements.comparisonSummary.innerHTML = "";
            return;
        }

        elements.comparisonSection.hidden = false;
        elements.compareInput.value = state.comparisonQuery;
        elements.compareButton.disabled =
            state.comparisonLoading || !state.comparisonQuery.trim() || !state.query.trim();

        if (state.comparisonLoading) {
            elements.comparisonNote.textContent = "Сравниваю запросы...";
            elements.comparisonSummary.hidden = true;
            elements.comparisonSummary.innerHTML = "";
            elements.comparisonGrid.innerHTML = "";
            return;
        }

        if (!state.comparisonQuery.trim()) {
            elements.comparisonNote.textContent =
                "Сравните текущий запрос с другим товаром или другой конфигурацией.";
            elements.comparisonSummary.hidden = true;
            elements.comparisonSummary.innerHTML = "";
            elements.comparisonGrid.innerHTML = "";
            return;
        }

        if (!state.comparisonItems.length) {
            elements.comparisonNote.textContent =
                "Введите до двух дополнительных запросов через запятую и нажмите «Сравнить».";
            elements.comparisonSummary.hidden = true;
            elements.comparisonSummary.innerHTML = "";
            elements.comparisonGrid.innerHTML = "";
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
            `${otherItems.reduce((sum, item) => sum + Number(item.cheap_count || 0), 0)} дешёвых лотов в compare`,
            `рынок ${state.comparisonItems.map((item) => item.total_results || 0).join(" / ")}`,
        ];
        elements.comparisonSummary.innerHTML = compareSummaryItems
            .map((item) => `<span class="compare-summary-chip">${item}</span>`)
            .join("");
        elements.comparisonSummary.hidden = false;

        elements.comparisonGrid.innerHTML = state.comparisonItems
            .map((item, index) => {
                const isBase = index === 0;
                const bestListing = item.best_listing || null;
                const medianDelta = isBase
                    ? { text: "база", className: "" }
                    : comparisonDelta(Number(item.median || 0), Number(baseItem?.median || 0));
                const trend = item.trend_percent == null
                    ? "нет истории"
                    : `${item.trend_percent > 0 ? "+" : ""}${item.trend_percent.toFixed(1)}%`;
                return `
                    <article class="compare-card">
                        <span class="compare-kicker">${isBase ? "База" : "Сравнение"}</span>
                        <strong class="compare-query">${escapeHtml(item.query)}</strong>
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
                                <span class="compare-meta">${bestListing ? `${escapeHtml(bestListing.title)} · ${formatPrice(bestListing.price)}` : "Лучший оффер пока не найден"}</span>
                            </div>
                        </div>
                    </article>
                `;
            })
            .join("");
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
            elements.stats.fairRange.innerHTML = `
                <span class="stat-range-item">${formatPrice(state.stats.fair_price_from)}</span>
                <span class="stat-range-sep">-</span>
                <span class="stat-range-item">${formatPrice(state.stats.fair_price_to)}</span>
            `;
        } else {
            elements.stats.fairRange.textContent = "—";
        }
        elements.stats.coverage.textContent =
            `${state.stats.analyzed_count || state.stats.count || 0} / ${state.stats.total_results || 0}`;
        elements.marketTotalBadge.textContent = `${state.stats.total_results || 0} на рынке`;
        elements.statsSection.hidden = false;
        elements.chartSection.hidden = state.stats.count <= 0;
        if (elements.chartSection.hidden || !state.panels.distribution) {
            destroyChart();
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
            card.innerHTML = `
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
            `;
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
            card.innerHTML = `
                <div class="geo-head">
                    <strong class="geo-name">${escapeHtml(region.region_name)}</strong>
                    <span class="geo-share">${region.share_percent}% выборки</span>
                </div>
                <span class="geo-price mono">${formatPrice(region.median)}</span>
                <div class="geo-meta">
                    <span>${region.count} с ценой</span>
                    <span>ср. ${formatPrice(region.mean)}</span>
                </div>
            `;
            elements.geographyGrid.appendChild(card);
        }

        elements.geographySection.hidden = false;
    }

    function buildListingNode(item) {
        const listing = document.createElement("article");
        listing.className = "listing";

        const condition = item.condition ? `<span class="tag">${formatCondition(item.condition)}</span>` : "";
        const seller = item.seller_type ? `<span class="tag">${formatSeller(item.seller_type)}</span>` : "";
        const region = item.region_name ? `<span class="tag">${escapeHtml(item.region_name)}</span>` : "";
        const delta = formatDelta(item.price_vs_median);
        const deltaMarkup = delta
            ? `<span class="delta ${deltaClass(item.price_vs_median)}">${delta}</span>`
            : "";
        const fairMarkup = item.fair_price_label
            ? `<span class="listing-signal ${item.fair_price_band || ""}">${escapeHtml(item.fair_price_label)}</span>`
            : "";
        const verdictMarkup = item.deal_verdict
            ? `<span class="listing-signal verdict verdict-${verdictClassName(item.deal_verdict)}">${escapeHtml(item.deal_verdict)}${item.deal_score ? ` · ${Math.round(item.deal_score)}` : ""}</span>`
            : "";
        const liquidityMarkup = item.liquidity
            ? `<span class="listing-signal">${escapeHtml(item.liquidity.label)} ликвидность · ${Math.round(item.liquidity.score)}</span>`
            : "";
        const duplicateMarkup = item.is_duplicate
            ? `<span class="listing-flag duplicate">Похоже на дубль${item.duplicate_count > 1 ? ` ×${item.duplicate_count + 1}` : ""}</span>`
            : "";
        const anomalyMarkup = (item.anomaly_labels || [])
            .map((label) => `<span class="listing-flag anomaly">${escapeHtml(label)}</span>`)
            .join("");
        const thumbMarkup = item.thumbnail
            ? `<img class="listing-thumb" src="${item.thumbnail}" alt="">`
            : `<div class="listing-thumb placeholder">Нет фото</div>`;
        const dateMarkup = item.list_time ? `<span class="listing-date">${formatDate(item.list_time)}</span>` : "";

        listing.innerHTML = `
            <div class="listing-main">
                ${thumbMarkup}
                <div class="listing-info">
                    <span class="listing-name">${escapeHtml(item.title)}</span>
                    <div class="listing-tags">${condition}${seller}${region}${dateMarkup}</div>
                    <div class="listing-signals">${verdictMarkup}${liquidityMarkup}${fairMarkup}${duplicateMarkup}${anomalyMarkup}</div>
                    ${(item.deal_reasons || []).length ? `<div class="listing-reasons">${item.deal_reasons.map((reason) => escapeHtml(reason)).join(" · ")}</div>` : ""}
                    ${(item.flip_estimates || []).length ? `<div class="listing-reasons">flip: ${(item.flip_estimates || []).map((estimate) => `${escapeHtml(estimate.label)} ${Math.round(estimate.profit_byn)} BYN`).join(" · ")}</div>` : ""}
                </div>
            </div>
            <div class="listing-right">
                <span class="listing-price mono">${formatPrice(item.price)}</span>
                ${deltaMarkup}
            </div>
            <div class="listing-actions">
                <button class="ghost-btn small" type="button">Карточка</button>
                <button class="ghost-btn small" data-role="lead" type="button">В Inbox</button>
                <button class="ghost-btn small" data-role="watch" type="button">Watch</button>
                <a class="primary-link small" href="${item.link}" target="_blank" rel="noreferrer noopener">Kufar</a>
            </div>
        `;

        const detailButton = listing.querySelector("button");
        detailButton?.addEventListener("click", () => {
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
        if (!items.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = emptyText;
            container.appendChild(note);
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

    function renderListings() {
        const hasContent = renderListingsCollection(
            state.listings,
            elements.listingsList,
            elements.listingsTotalBadge,
            "По этому запросу пока нечего показать."
        );
        elements.listingsSection.hidden = !hasContent;
    }

    function renderDeals() {
        const rangeLabel = `${state.discountFromPercent}-${state.discountToPercent}`;
        const hasContent = renderListingsCollection(
            state.dealListings,
            elements.dealsList,
            elements.dealsTotalBadge,
            `Нет лотов в диапазоне ${rangeLabel}% ниже медианы. Попробуйте расширить диапазон или другой запрос.`,
            state.stats?.total_results || state.listings.length || state.dealListings.length
        );
        elements.dealsSection.hidden = !state.query;
        if (!hasContent && state.query) {
            elements.dealsSection.hidden = false;
        }
    }

    function renderSavedSearches() {
        elements.savedSearchesList.innerHTML = "";
        if (elements.savedSearchesNote) {
            elements.savedSearchesNote.textContent =
                "Сохраняйте связки моделей и конфигураций, чтобы быстро запускать поиск, сравнение и ежедневную проверку.";
        }

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Сохранённые поиски доступны внутри Telegram Mini App.";
            elements.savedSearchesList.appendChild(note);
            return;
        }

        if (!state.savedSearches.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Сохранённых поисков пока нет.";
            elements.savedSearchesList.appendChild(note);
            return;
        }

        if (elements.savedSearchesNote) {
            const groupsCount = new Set(
                state.savedSearches.map((item) => item.group_name || "Без группы")
            ).size;
            elements.savedSearchesNote.textContent =
                `${state.savedSearches.length} поисков в ${groupsCount} группах. Один тап запускает весь набор моделей.`;
        }

        const groups = new Map();
        for (const savedSearch of state.savedSearches) {
            const groupName = savedSearch.group_name || "Без группы";
            if (!groups.has(groupName)) {
                groups.set(groupName, []);
            }
            groups.get(groupName).push(savedSearch);
        }

        for (const [groupName, searches] of groups.entries()) {
            const group = document.createElement("div");
            group.className = "saved-search-group";
            const modelLabels = searches
                .slice(0, 3)
                .map((item) => item.config_summary || item.query)
                .filter(Boolean);
            group.innerHTML = `
                <div class="tracker-row tracker-row-group">
                    <div class="tracker-row-main">
                        <strong class="tracker-query">${escapeHtml(groupName)}</strong>
                        <span class="tracker-meta mono">${searches.length} запросов • ${modelLabels.map((l) => escapeHtml(l)).join(" • ")}</span>
                    </div>
                    <div class="tracker-row-actions">
                        <button class="ghost-btn small" data-role="run-group" type="button">Запустить набор</button>
                    </div>
                </div>
            `;
            group.querySelector('[data-role="run-group"]')?.addEventListener("click", () => {
                void actions.applySavedSearchGroup(searches);
            });
            elements.savedSearchesList.appendChild(group);

            for (const savedSearch of searches) {
                const row = document.createElement("div");
                row.className = "tracker-row";
                const meta = [
                    savedSearch.strict_mode ? "строгий" : "",
                    savedSearch.config_summary ? escapeHtml(savedSearch.config_summary) : "",
                    savedSearch.target_discount_percent ? `от -${Math.round(savedSearch.target_discount_percent)}%` : "",
                    savedSearch.exclude_duplicates ? "без дублей" : "",
                    savedSearch.seller_type === "Частное лицо" ? "частники" : "",
                    savedSearch.condition ? escapeHtml(savedSearch.condition) : "",
                    savedSearch.region_name ? escapeHtml(savedSearch.region_name) : "",
                ].filter(Boolean);
                row.innerHTML = `
                    <div class="tracker-row-main">
                        <strong class="tracker-query">${escapeHtml(savedSearch.name)}</strong>
                        <span class="tracker-meta mono">${meta.join(" • ")}</span>
                    </div>
                    <div class="tracker-row-actions">
                        <button class="ghost-btn small" data-role="open" type="button">Открыть</button>
                        <button class="ghost-btn small" data-role="compare" type="button">Сравнить</button>
                        <button class="ghost-btn small danger" data-role="delete" type="button">Удалить</button>
                    </div>
                `;
                row.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                    void actions.applySavedSearch(savedSearch, "search");
                });
                row.querySelector('[data-role="compare"]')?.addEventListener("click", () => {
                    void actions.applySavedSearch(savedSearch, "compare");
                });
                row.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                    void actions.deleteSavedSearch(savedSearch.id);
                });
                elements.savedSearchesList.appendChild(row);
            }
        }
    }

    function renderOpportunityBoard() {
        elements.opportunityBoardList.innerHTML = "";
        elements.opportunityBoardDrops.innerHTML = "";
        elements.opportunityBoardRare.innerHTML = "";
        elements.opportunityBoardSignals.innerHTML = "";
        if (elements.opportunityBoardNote) {
            elements.opportunityBoardNote.textContent =
                "Показываем лучшие дешёвые лоты по сохранённым поискам.";
        }

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Board доступен внутри Telegram Mini App.";
            elements.opportunityBoardList.appendChild(note);
            return;
        }

        if (!(state.opportunityBoard.items || []).length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Добавьте сохранённые поиски, чтобы увидеть лучшие лоты.";
            elements.opportunityBoardList.appendChild(note);
            return;
        }

        elements.opportunityBoardNote.textContent = `${(state.opportunityBoard.items || []).length} кандидатов на выкуп, ${(state.opportunityBoard.top_price_drops || []).length} падений цены, ${(state.opportunityBoard.rare_opportunities || []).length} редких офферов.`;

        function buildOpportunityCard(item, extraBadge = "") {
            const card = document.createElement("article");
            card.className = "opportunity-card";
            const reasons = (item.listing.deal_reasons || []).map((reason) => escapeHtml(reason)).join(" · ");
            const badge = item.signal_label || extraBadge;
            card.innerHTML = `
                <div class="opportunity-top">
                    <span class="badge">${escapeHtml(item.saved_search_name)}</span>
                    <span class="listing-signal verdict verdict-${verdictClassName(item.listing.deal_verdict || "Смотреть")}">${escapeHtml(item.listing.deal_verdict || "Смотреть")} · ${Math.round(item.listing.deal_score || 0)}</span>
                </div>
                <strong class="opportunity-title">${escapeHtml(item.listing.title)}</strong>
                <div class="opportunity-meta">
                    <span class="mono">${formatPrice(item.listing.price)}</span>
                    <span>${escapeHtml(item.listing.region_name || "Без региона")}</span>
                    ${badge ? `<span class="board-inline-flag">${escapeHtml(badge)}</span>` : ""}
                </div>
                ${reasons ? `<p class="opportunity-copy">${reasons}</p>` : ""}
                <div class="listing-actions">
                    <button class="ghost-btn small" data-role="open-query" type="button">Открыть запрос</button>
                    <button class="ghost-btn small" data-role="open-detail" type="button">Карточка</button>
                    <button class="ghost-btn small" data-role="lead" type="button">В Inbox</button>
                    <button class="ghost-btn small" data-role="watch" type="button">Watch</button>
                    <a class="primary-link small" href="${item.listing.link}" target="_blank" rel="noreferrer noopener">Kufar</a>
                </div>
            `;
            card.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
                void actions.openOpportunityQuery(item);
            });
            card.querySelector('[data-role="open-detail"]')?.addEventListener("click", () => {
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

        for (const item of state.opportunityBoard.items || []) {
            elements.opportunityBoardList.appendChild(buildOpportunityCard(item));
        }

        if (!(state.opportunityBoard.rare_opportunities || []).length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Редких офферов пока нет.";
            elements.opportunityBoardRare.appendChild(note);
        } else {
            for (const item of state.opportunityBoard.rare_opportunities || []) {
                elements.opportunityBoardRare.appendChild(buildOpportunityCard(item, "Редкий оффер"));
            }
        }

        if (!(state.opportunityBoard.top_price_drops || []).length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Пока нет свежих падений цены.";
            elements.opportunityBoardDrops.appendChild(note);
        } else {
            for (const signal of state.opportunityBoard.top_price_drops || []) {
                const row = document.createElement("div");
                row.className = "tracker-row signal-row";
                row.innerHTML = `
                    <div class="tracker-row-main">
                        <strong class="tracker-query">${escapeHtml(signal.title)}</strong>
                        <span class="tracker-meta mono">${escapeHtml(signal.subtitle)} • ${escapeHtml(signal.metric)}</span>
                    </div>
                    <div class="tracker-row-actions">
                        <button class="ghost-btn small" type="button">Открыть</button>
                    </div>
                `;
                row.querySelector("button")?.addEventListener("click", () => {
                    elements.searchInput.value = signal.query;
                    state.query = signal.query;
                    void actions.search("overview");
                });
                elements.opportunityBoardDrops.appendChild(row);
            }
        }

        if (!(state.opportunityBoard.market_signals || []).length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Сигналы рынка появятся, когда накопится больше saved searches и истории.";
            elements.opportunityBoardSignals.appendChild(note);
        } else {
            for (const signal of state.opportunityBoard.market_signals || []) {
                const row = document.createElement("div");
                row.className = "tracker-row signal-row";
                row.innerHTML = `
                    <div class="tracker-row-main">
                        <strong class="tracker-query">${escapeHtml(signal.title)}</strong>
                        <span class="tracker-meta mono">${escapeHtml(signal.subtitle)} • ${escapeHtml(signal.metric)}</span>
                    </div>
                `;
                elements.opportunityBoardSignals.appendChild(row);
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

    function workflowLabel(value) {
        const labels = {
            new: "Новый",
            reviewing: "Смотреть",
            in_progress: "В работе",
            negotiating: "Торг",
            bought: "Купил",
            reselling: "В продаже",
            sold: "Продано",
            skipped: "Пропустить",
            deferred: "Позже",
            watching: "Слежу",
        };
        return labels[value] || value || "Без статуса";
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

    function leadMatchesFilter(lead) {
        if (state.leadFilter === "all") {
            return true;
        }
        if (state.leadFilter === "active") {
            return ["new", "reviewing", "in_progress", "negotiating", "deferred"].includes(lead.status);
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
            return ["price_drop", "missing", "duplicate"].includes(item.market_status) ||
                ["reviewing", "in_progress"].includes(item.workflow_status);
        }
        if (state.watchlistFilter === "watching") {
            return item.workflow_status === "watching";
        }
        if (state.watchlistFilter === "risk") {
            return ["price_drop", "missing", "duplicate"].includes(item.market_status);
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
            skipped: 3,
        };
        return `${marketOrder[item.market_status] ?? 9}:${workflowOrder[item.workflow_status] ?? 9}`;
    }

    function renderLeads() {
        elements.leadInboxList.innerHTML = "";
        renderLeadFilters();
        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Inbox доступен внутри Telegram Mini App.";
            elements.leadInboxList.appendChild(note);
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
        if (!filteredLeads.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "По текущему фильтру Inbox пуст.";
            elements.leadInboxList.appendChild(note);
            return;
        }
        const activeCount = state.leads.filter((lead) => ["new", "reviewing", "in_progress", "negotiating", "deferred"].includes(lead.status)).length;
        elements.leadInboxNote.textContent = `${filteredLeads.length} из ${state.leads.length} лотов в фильтре. Активных: ${activeCount}.`;
        for (const lead of filteredLeads) {
            const row = document.createElement("div");
            row.className = "tracker-row workflow-row";
            row.innerHTML = `
                <div class="tracker-row-main">
                    <strong class="tracker-query">${escapeHtml(lead.title)}</strong>
                    <span class="tracker-meta mono">${escapeHtml(workflowLabel(lead.status))} • ${lead.price_byn ? `${Math.round(lead.price_byn)} BYN` : "без цены"}${lead.target_resale_byn ? ` • цель ${Math.round(lead.target_resale_byn)} BYN` : ""}</span>
                </div>
                <div class="tracker-row-actions">
                    <select class="deal-select" data-role="status">
                        <option value="new">Новый</option>
                        <option value="reviewing">Смотреть</option>
                        <option value="in_progress">В работе</option>
                        <option value="negotiating">Торг</option>
                        <option value="bought">Купил</option>
                        <option value="reselling">В продаже</option>
                        <option value="sold">Продано</option>
                        <option value="skipped">Пропустить</option>
                    </select>
                    <a class="primary-link small" href="${lead.link}" target="_blank" rel="noreferrer noopener">Kufar</a>
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
            `;
            const select = row.querySelector('[data-role="status"]');
            if (select) {
                select.value = lead.status || "new";
                select.addEventListener("change", () => {
                    void actions.updateLeadStatus(lead.id, select.value);
                });
            }
            const targetInput = row.querySelector('[data-role="target"]');
            if (targetInput) {
                targetInput.value = lead.target_resale_byn ?? "";
                targetInput.addEventListener("change", () => {
                    const rawValue = targetInput.value.trim();
                    const nextValue = rawValue ? Math.abs(Number(rawValue)) : null;
                    void actions.updateLeadMeta(lead.id, {
                        target_resale_byn: Number.isFinite(nextValue) ? nextValue : null,
                    });
                });
            }
            const notesInput = row.querySelector('[data-role="notes"]');
            if (notesInput) {
                notesInput.value = lead.notes || "";
                notesInput.addEventListener("change", () => {
                    void actions.updateLeadMeta(lead.id, {
                        notes: notesInput.value.trim() || null,
                    });
                });
            }
            elements.leadInboxList.appendChild(row);
        }
    }

    function renderWatchlist() {
        elements.watchlistList.innerHTML = "";
        renderWatchlistFilters();
        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Watchlist доступен внутри Telegram Mini App.";
            elements.watchlistList.appendChild(note);
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
        if (!filteredWatchlist.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "По текущему фильтру Watchlist пуст.";
            elements.watchlistList.appendChild(note);
            return;
        }
        const riskCount = state.watchlist.filter((item) => ["price_drop", "missing", "duplicate"].includes(item.market_status)).length;
        elements.watchlistNote.textContent = `${filteredWatchlist.length} из ${state.watchlist.length} объявлений в фильтре. Сигналов: ${riskCount}.`;
        for (const item of filteredWatchlist) {
            const row = document.createElement("div");
            row.className = "tracker-row workflow-row";
            const delta = item.price_delta_byn == null
                ? "без изменений"
                : `${item.price_delta_byn > 0 ? "+" : ""}${Math.round(item.price_delta_byn)} BYN`;
            row.innerHTML = `
                <div class="tracker-row-main">
                    <strong class="tracker-query">${escapeHtml(item.title)}</strong>
                    <span class="tracker-meta mono">${escapeHtml(workflowLabel(item.workflow_status))} • ${escapeHtml(marketLabel(item.market_status))} • ${delta}</span>
                </div>
                <div class="tracker-row-actions">
                    <select class="deal-select" data-role="status">
                        <option value="watching">Слежу</option>
                        <option value="reviewing">Смотреть</option>
                        <option value="in_progress">В работе</option>
                        <option value="skipped">Пропустить</option>
                    </select>
                    <button class="ghost-btn small" data-role="lead" type="button">В Inbox</button>
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
            `;
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
                notesInput.value = item.notes || "";
                notesInput.addEventListener("change", () => {
                    void actions.updateWatchlistMeta(item.id, {
                        notes: notesInput.value.trim() || null,
                    });
                });
            }
            elements.watchlistList.appendChild(row);
        }
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
            const trackerMeta = [
                `каждые ${tracker.interval_min} мин`,
                tracker.strict_mode ? "строгий" : "",
                tracker.min_discount_percent ? `от -${Math.round(tracker.min_discount_percent)}%` : "",
                tracker.max_price_byn ? `до ${Math.round(tracker.max_price_byn)} BYN` : "",
                tracker.exclude_duplicates ? "без дублей" : "",
                tracker.seller_type === "Частное лицо" ? "частники" : "",
            ].filter(Boolean);
            row.innerHTML = `
                <div class="tracker-row-main">
                    <strong class="tracker-query">${escapeHtml(tracker.query)}</strong>
                    <span class="tracker-meta mono">${trackerMeta.join(" • ")}</span>
                </div>
                <div class="tracker-row-actions">
                    <button class="ghost-btn small" data-role="open" type="button">Открыть</button>
                    <button class="ghost-btn small danger" data-role="delete" type="button">Удалить</button>
                </div>
            `;
            row.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                elements.searchInput.value = tracker.query;
                state.query = tracker.query;
                state.strictSearch = Boolean(tracker.strict_mode);
                state.trackerMinDiscountPercent = Math.round(tracker.min_discount_percent || 10);
                state.trackerMaxPriceByn = tracker.max_price_byn ?? null;
                state.trackerExcludeDuplicates = Boolean(tracker.exclude_duplicates);
                state.trackerSellerType = tracker.seller_type || "";
                state.trackerCondition = tracker.condition || "";
                state.trackerRegionName = tracker.region_name || "";
                state.trackerConfigKeyword = tracker.config_keyword || "";
                renderStrictSearch();
                renderTrackerInputs();
                renderLoading();
                void actions.search();
            });
            row.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteTracker(tracker.id);
            });
            elements.trackersList.appendChild(row);
        }
    }

    function renderTrackerEvents() {
        elements.trackerEventsList.innerHTML = "";

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-event-empty";
            note.textContent = "Откройте Mini App внутри Telegram, чтобы видеть последние события.";
            elements.trackerEventsList.appendChild(note);
            return;
        }

        const filteredEvents = state.trackerEvents.filter((event) => {
            if (state.trackerEventFilter === "all") {
                return true;
            }
            return event.event_type === state.trackerEventFilter;
        });

        if (!filteredEvents.length) {
            const note = document.createElement("p");
            note.className = "tracker-event-empty";
            note.textContent = state.trackerEventFilter === "all"
                ? "Событий пока нет. Они появятся после первой проверки scheduler."
                : "По этому фильтру событий пока нет.";
            elements.trackerEventsList.appendChild(note);
            return;
        }

        for (const event of filteredEvents) {
            const row = document.createElement("article");
            row.className = "tracker-event-row";
            const typeLabel = event.event_type === "price_drop" ? "Падение цены" : "Новый лот";
            const typeClass = event.event_type === "price_drop" ? "drop" : "new";
            const meta = [];
            if (event.query) {
                meta.push(event.strict_mode ? `${escapeHtml(event.query)} • строгий` : escapeHtml(event.query));
            }
            if (event.price_byn) {
                meta.push(`${Math.round(event.price_byn)} р.`);
            }
            if (event.delta_byn) {
                meta.push(`-${Math.round(event.delta_byn)} р.`);
            }
            row.innerHTML = `
                <div class="tracker-event-top">
                    <span class="tracker-event-type ${typeClass}">${typeLabel}</span>
                    <span class="tracker-event-time mono">${formatDate(event.created_at)}</span>
                </div>
                <strong class="tracker-event-title">${escapeHtml(event.title)}</strong>
                <div class="tracker-event-meta">${meta.map((item) => `<span>${item}</span>`).join("")}</div>
                <div class="listing-actions">
                    <button class="ghost-btn small" data-role="open-query" type="button">Открыть запрос</button>
                    <button class="ghost-btn small" data-role="lead" type="button">В Inbox</button>
                    <a class="primary-link small" href="${event.link}" target="_blank" rel="noreferrer noopener">Kufar</a>
                </div>
            `;
            row.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
                elements.searchInput.value = event.query;
                state.query = event.query;
                state.strictSearch = Boolean(event.strict_mode);
                renderStrictSearch();
                void actions.search("overview");
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
            elements.trackerEventsList.appendChild(row);
        }
    }

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
        elements.detailProfit.innerHTML = "";
        for (const estimate of detail.flip_estimates || []) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${escapeHtml(estimate.label)}</span>
                <span class="detail-field-value">${formatPrice(estimate.target_price)} • ${Math.round(estimate.profit_byn)} BYN (${estimate.profit_percent > 0 ? "+" : ""}${estimate.profit_percent}%)</span>
            `;
            elements.detailProfit.appendChild(item);
        }
        elements.detailProfitBlock.hidden = (detail.flip_estimates || []).length === 0;

        elements.detailLiquidity.innerHTML = "";
        if (detail.liquidity) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${escapeHtml(detail.liquidity.label)}</span>
                <span class="detail-field-value">${Math.round(detail.liquidity.score)} • ${(detail.liquidity.reasons || []).map((reason) => escapeHtml(reason)).join(" · ")}</span>
            `;
            elements.detailLiquidity.appendChild(item);
        }
        elements.detailLiquidityBlock.hidden = !detail.liquidity;

        const metaItems = [
            detail.category ? escapeHtml(detail.category) : "",
            detail.condition ? formatCondition(detail.condition) : "",
            detail.seller_type ? formatSeller(detail.seller_type) : "",
            detail.region_name ? escapeHtml(detail.region_name) : "",
            detail.list_time ? formatDate(detail.list_time) : "",
            detail.fair_price_label ? escapeHtml(detail.fair_price_label) : "",
            detail.is_duplicate ? "Похоже на дубль" : "",
            ...(detail.anomaly_labels || []).map((label) => escapeHtml(label)),
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
                <span class="detail-field-label">${escapeHtml(field.label)}</span>
                <span class="detail-field-value">${escapeHtml(field.value)}</span>
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
                <span class="detail-field-label">${escapeHtml(field.label)}</span>
                <span class="detail-field-value">${escapeHtml(field.value)}</span>
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

    function renderHistory() {
        const hasHistory = state.history.length > 0;
        elements.historySection.hidden = !state.query;
        renderHistoryRangeButtons();
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
    }

    function renderHistoryChart() {
        const canvas = document.getElementById("historyChart");
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
                value: `${formatPrice(Math.min(...state.history.map((point) => point.median)))} - ${formatPrice(Math.max(...state.history.map((point) => point.median)))}`,
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
        const lineColor = isDark ? "#F59E0B" : "#D97706";
        const fillColor = isDark ? "rgba(245,158,11,0.12)" : "rgba(217,119,6,0.12)";
        const muted = isDark ? "rgba(136,128,120,0.75)" : "rgba(114,105,94,0.75)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.05)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";

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
    }

    function renderAll() {
        renderLoading();
        renderError();
        renderHelper();
        renderSummary();
        renderCurrencyButtons();
        renderStrictSearch();
        renderViewTabs();
        renderViews();
        renderSortButtons();
        renderDiscountButtons();
        renderTrackerEventFilters();
        renderHistoryRangeButtons();
        renderDealInputs();
        renderTrackerInputs();
        renderSavedSearchInputs();
        renderStats();
        renderHistory();
        renderComparison();
        renderSegments();
        renderGeography();
        renderPanels();
        renderListings();
        renderDeals();
        renderRates();
        renderTrackerStatus();
        renderTrackers();
        renderTrackerEvents();
        renderLeads();
        renderWatchlist();
        renderSavedSearches();
        renderOpportunityBoard();
    }

    return {
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
        renderSavedSearches,
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
