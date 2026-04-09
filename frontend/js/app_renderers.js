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

    /**
     * Escape HTML special characters to prevent XSS attacks.
     * Uses a singleton DOM element to avoid creating new elements on every call.
     */
    const _escapeDiv = document.createElement("div");

    function escapeHtml(str) {
        if (str == null) return "";
        _escapeDiv.textContent = String(str);
        return _escapeDiv.innerHTML;
    }

    function showToast(message) {
        if (!elements.toastContainer) return;
        const toast = document.createElement("div");
        toast.className = "toast";
        toast.textContent = message;
        elements.toastContainer.appendChild(toast);
        setTimeout(() => {
            toast.remove();
        }, 1400);
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

    function setPanelOpen(panelName, isOpen, skipLoad) {
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

    function renderTrackingHeroStats() {
        if (!elements.trackingHeroStats) return;
        const trackerCount = state.trackers.length;
        const eventCount = state.trackerEvents.length;
        elements.trackingHeroStats.innerHTML = [
            `<span class="hero-stat"><span class="hero-stat-val mono">${trackerCount}</span> трекеров</span>`,
            `<span class="hero-stat"><span class="hero-stat-val mono">${eventCount}</span> событий</span>`,
        ].join("");
    }

    function renderCheapHeroStats() {
        if (!elements.cheapHeroStats) return;
        const cheapCount = state.dealListings.length;
        const range = state.discountFromPercent === state.discountToPercent
            ? `${state.discountFromPercent}%`
            : `${state.discountFromPercent}-${state.discountToPercent}%`;
        elements.cheapHeroStats.innerHTML = [
            `<span class="hero-stat"><span class="hero-stat-val mono">${cheapCount}</span> лотов дешевле рынка</span>`,
            `<span class="hero-stat">диапазон <span class="hero-stat-val mono">${range}</span></span>`,
        ].join("");
    }

    function renderMonitoringHeroStats() {
        if (!elements.monitoringHeroStats) return;
        const watchCount = state.watchlist.length;
        elements.monitoringHeroStats.innerHTML = [
            `<span class="hero-stat"><span class="hero-stat-val mono">${watchCount}</span> объявлений</span>`,
        ].join("");
    }

    function renderDealsHeroStats() {
        if (!elements.dealsHeroStats) return;
        // Only count non-closed deals (active deals in progress)
        const activeLeads = state.leads.filter((l) => l.status !== "closed");
        elements.dealsHeroStats.innerHTML = [
            `<span class="hero-stat"><span class="hero-stat-val mono">${activeLeads.length}</span> сделок</span>`,
        ].join("");
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

    function verdictClassName(verdict) {
        if (!verdict) return "neutral";
        if (verdict.includes("Хорошая")) return "zabirat";
        if (verdict.includes("Ниже")) return "smotret";
        if (verdict.includes("Средняя")) return "norm";
        if (verdict.includes("Выше")) return "mimo";
        return "neutral";
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
            `${otherItems.reduce((sum, item) => sum + Number(item.cheap_count || 0), 0)} дешёвых лотов в сравнении`,
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
                    <span class="geo-share">${escapeHtml(region.share_percent)}% выборки</span>
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

        const verdictMarkup = item.deal_verdict
            ? `<span class="listing-badge verdict-${verdictClassName(item.deal_verdict)}">${item.deal_verdict}</span>`
            : "";

        let delta = item.price_vs_median;
        if (delta == null && item.price && state.stats?.median && Number(state.stats.median) > 0) {
            delta = Math.round(((Number(item.price) - Number(state.stats.median)) / Number(state.stats.median)) * 100 * 100) / 100;
        }
        let deltaMarkup = "";
        if (delta != null) {
            const absDelta = Math.abs(delta);
            if (absDelta < 0.5) {
                deltaMarkup = `<span class="listing-badge neutral">≈0%</span>`;
            } else {
                deltaMarkup = `<span class="listing-badge ${deltaClass(delta)}">${formatDelta(delta)}</span>`;
            }
        }

        let freshnessMarkup = "";
        if (item.list_time) {
            const hours = (Date.now() - new Date(item.list_time).getTime()) / 3600000;
            if (hours <= 3) {
                freshnessMarkup = `<span class="listing-badge fresh-hot">Новое</span>`;
            } else if (hours <= 24) {
                freshnessMarkup = `<span class="listing-badge fresh-warm">Сегодня</span>`;
            }
        }

        const duplicateMarkup = item.is_duplicate
            ? `<span class="listing-badge warn">Дубль${item.duplicate_count > 1 ? ` ×${item.duplicate_count + 1}` : ""}</span>`
            : "";

        const thumbMarkup = item.thumbnail
            ? `<img class="listing-thumb" src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy">`
            : `<div class="listing-thumb placeholder">Нет фото</div>`;
        const badgesMarkup = [freshnessMarkup, verdictMarkup, deltaMarkup, duplicateMarkup].filter(Boolean).join("");

        listing.innerHTML = `
            <div class="listing-top">
                ${thumbMarkup}
                <div class="listing-body">
                    <span class="listing-name">${escapeHtml(item.title)}</span>
                    <div class="listing-tags">${condition}${seller}</div>
                    <span class="listing-price mono">${formatPrice(item.price)}</span>
                </div>
            </div>
            ${badgesMarkup ? `<div class="listing-badges">${badgesMarkup}</div>` : ""}
            <div class="listing-actions">
                <button class="listing-btn" type="button">Подробнее</button>
                <button class="listing-btn" data-role="lead" type="button">В покупки</button>
                <button class="listing-btn" data-role="watch" type="button">В избранное</button>
                <a class="listing-btn listing-btn--accent" href="${escapeHtml(item.link)}" target="_blank" rel="noreferrer noopener">Kufar</a>
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
            note.textContent = "Доска возможностей доступна внутри Telegram Mini App.";
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
            const reasons = (item.listing.deal_reasons || []).join(" · ");
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
                ${reasons ? `<p class="opportunity-copy">${escapeHtml(reasons)}</p>` : ""}
                <div class="listing-actions">
                    <button class="ghost-btn small" data-role="open-query" type="button">Открыть запрос</button>
                    <button class="ghost-btn small" data-role="open-detail" type="button">Подробнее</button>
                    <button class="ghost-btn small" data-role="lead" type="button">В покупки</button>
                    <button class="ghost-btn small" data-role="watch" type="button">В избранное</button>
                    <a class="primary-link small" href="${escapeHtml(item.listing.link)}" target="_blank" rel="noreferrer noopener">Kufar</a>
                </div>
            `;
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
                        <strong class="tracker-query">${signal.title}</strong>
                        <span class="tracker-meta mono">${signal.subtitle} • ${signal.metric}</span>
                    </div>
                    <div class="tracker-row-actions">
                        <button class="ghost-btn small" type="button">Открыть</button>
                    </div>
                `;
                row.querySelector("button")?.addEventListener("click", () => {
                    showToast("Загружаю...");
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
            note.textContent = "Сигналы рынка появятся, когда накопится больше сохранённых поисков и истории.";
            elements.opportunityBoardSignals.appendChild(note);
        } else {
            for (const signal of state.opportunityBoard.market_signals || []) {
                const row = document.createElement("div");
                row.className = "tracker-row signal-row";
                row.innerHTML = `
                    <div class="tracker-row-main">
                        <strong class="tracker-query">${signal.title}</strong>
                        <span class="tracker-meta mono">${signal.subtitle} • ${signal.metric}</span>
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
            default: "Обычное",
            important: "Важное",
            very_important: "Очень важное",
        };
        return labels[value] || value || "Обычное";
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

    function watchlistMatchesFilter(item) {
        if (state.watchlistFilter === "all") {
            return true;
        }
        return item.workflow_status === state.watchlistFilter;
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
        renderDealsHeroStats();
        renderProfitDashboard();
        renderHistoryDeals();
        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Сделки доступны внутри Telegram Mini App.";
            elements.leadInboxList.appendChild(note);
            return;
        }

        // Currency conversion setup (same pattern as watchlist)
        const rate = state.usdRateByn || 1;
        const currencySymbol = state.currency === "USD" ? "$" : "BYN";

        const filteredLeads = [...state.leads]
            .filter((l) => l.status !== "closed") // Hide closed deals from the list
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
            note.textContent = "Нет сделок в работе. Добавьте лот через поиск.";
            elements.leadInboxList.appendChild(note);
            return;
        }
        for (const lead of filteredLeads) {
            const isSold = lead.status === "sold";
            const isMissing = lead.market_status === "missing";
            const card = document.createElement("article");
            card.className = `lead-card status-${lead.status}`;
            card.dataset.leadId = lead.id;

            const priceBynRaw = lead.price_byn ? Number(lead.price_byn) : null;
            const buyPriceBynRaw = lead.buy_price_byn ? Number(lead.buy_price_byn) : null;
            const soldPriceBynRaw = lead.sold_price_byn ? Number(lead.sold_price_byn) : null;

            // Convert prices for display
            const priceByn = priceBynRaw ? (state.currency === "USD" ? Math.round(priceBynRaw / rate) : Math.round(priceBynRaw)) : null;
            const buyPrice = buyPriceBynRaw ? (state.currency === "USD" ? Math.round(buyPriceBynRaw / rate) : Math.round(buyPriceBynRaw)) : null;
            const soldPrice = soldPriceBynRaw ? (state.currency === "USD" ? Math.round(soldPriceBynRaw / rate) : Math.round(soldPriceBynRaw)) : null;

            let profitMarkup = "";
            if (isSold && soldPriceBynRaw && buyPriceBynRaw) {
                const profitRaw = soldPriceBynRaw - buyPriceBynRaw;
                const profit = state.currency === "USD" ? profitRaw / rate : profitRaw;
                const profitPercent = buyPriceBynRaw > 0 ? ((profitRaw / buyPriceBynRaw) * 100).toFixed(0) : "0";
                const profitSign = profit >= 0 ? "+" : "";
                const profitClass = profit >= 0 ? "profit-positive" : "profit-negative";
                const profitLabel = profit >= 0 ? "Потенциальная прибыль" : "Потенциальный убыток";
                profitMarkup = `<div class="lead-financial-item ${profitClass}">${profitLabel}: <span class="mono">${profitSign}${Math.round(profit)} ${currencySymbol} (${profitSign}${profitPercent}%)</span></div>`;
            }

            const thumbMarkup = lead.thumbnail
                ? `<img class="watchlist-thumb" src="${escapeHtml(lead.thumbnail)}" alt="" loading="lazy">`
                : `<div class="watchlist-thumb-placeholder">Нет фото</div>`;

            const missingBanner = isMissing
                ? `<div class="watchlist-missing-banner">Объявление снято с продажи</div>`
                : "";
            const missingBadge = isMissing
                ? `<span class="market-badge missing">Пропало</span>`
                : "";

            // Stage 1: New lead - show Confirm and Delete buttons, no Kufar
            // Stage 2: Bought lead - show Close Deal and Revert buttons, Kufar visible
            // Stage 3: Sold lead - show Close Deal button, Kufar visible
            card.innerHTML = `
                ${missingBanner}
                <div class="lead-card-top${isMissing ? " is-missing" : ""}">
                    ${thumbMarkup}
                    <div class="lead-card-body">
                        <div class="lead-card-title-row">
                            <strong class="lead-card-title">${escapeHtml(lead.title)}</strong>
                        </div>
                        <span class="lead-card-price mono">${priceByn ? `${priceByn} ${currencySymbol}` : "без цены"}</span>
                        ${missingBadge}
                        ${profitMarkup}
                    </div>
                </div>
                <div class="lead-card-fields">
                    <label class="lead-field">
                        <div class="lead-field-label-row">
                            <span class="lead-field-label">Купил за</span>
                            <button class="lead-field-chip" data-role="fill-buy-price" type="button" ${!priceByn ? 'disabled style="opacity:0.4;pointer-events:none;"' : ''}>📋 ${priceByn ? priceByn : '—'}</button>
                        </div>
                        <div class="lead-field-wrap">
                            <input data-role="buy-price" type="text" min="0" placeholder="цена покупки">
                            <span class="unit">${currencySymbol}</span>
                        </div>
                    </label>
                    <label class="lead-field">
                        <span class="lead-field-label">Продал за</span>
                        <div class="lead-field-wrap">
                            <input data-role="sold-price" type="text" min="0" placeholder="цена продажи">
                            <span class="unit">${currencySymbol}</span>
                        </div>
                    </label>
                </div>
                <div class="lead-card-actions">
                    ${!isSold ? `
                        <div class="lead-btn-row">
                            <a class="lead-btn lead-btn--kufar" href="${escapeHtml(lead.link)}" target="_blank" rel="noreferrer noopener">Kufar ↗</a>
                        </div>
                        <div class="lead-btn-row">
                            <button class="lead-btn lead-btn--confirm" data-role="confirm" type="button">✓</button>
                            <button class="lead-btn lead-btn--delete" data-role="cancel" type="button">✕</button>
                        </div>
                    ` : ""}
                    ${isSold ? `
                        <div class="lead-btn-row">
                            <button class="lead-btn lead-btn--success" data-role="close-deal" type="button">✓ Готово</button>
                            <button class="lead-btn lead-btn--revert" data-role="revert" type="button">↩ Назад</button>
                        </div>
                    ` : ""}
                </div>
            `;

            // Event listeners
            card.querySelector('[data-role="confirm"]')?.addEventListener("click", () => {
                void actions.confirmLead(lead, card);
            });
            card.querySelector('[data-role="cancel"]')?.addEventListener("click", () => {
                void actions.cancelLead(lead.id);
            });
            card.querySelector('[data-role="close-deal"]')?.addEventListener("click", () => {
                void actions.closeDeal(lead.id);
            });
            card.querySelector('[data-role="revert"]')?.addEventListener("click", () => {
                void actions.revertLeadStage(lead.id, lead.status);
            });
            
            // Buy price input - use text inputmode for better mobile control
            const buyPriceInput = card.querySelector('[data-role="buy-price"]');
            if (buyPriceInput) {
                buyPriceInput.value = lead.buy_price_byn ?? "";
                // Only update on explicit confirm button click, not on every blur
                buyPriceInput.addEventListener("input", (e) => {
                    // Strip non-numeric characters except the decimal point
                    let val = e.target.value.replace(/[^\d]/g, "");
                    if (val !== e.target.value) {
                        e.target.value = val;
                    }
                });
            }

            // Fill buy price from original listing price
            card.querySelector('[data-role="fill-buy-price"]')?.addEventListener("click", () => {
                if (buyPriceInput && priceByn) {
                    buyPriceInput.value = String(priceByn);
                    buyPriceInput.focus();
                }
            });

            // Sold price input - use text inputmode for better mobile control
            const soldPriceInput = card.querySelector('[data-role="sold-price"]');
            if (soldPriceInput) {
                soldPriceInput.value = lead.sold_price_byn ?? "";
                // Only update on explicit confirm button click, not on every blur
                soldPriceInput.addEventListener("input", (e) => {
                    // Strip non-numeric characters except the decimal point
                    let val = e.target.value.replace(/[^\d]/g, "");
                    if (val !== e.target.value) {
                        e.target.value = val;
                    }
                });
            }
            elements.leadInboxList.appendChild(card);
        }
    }

    function renderWatchlist() {
        elements.watchlistList.innerHTML = "";
        renderWatchlistFilters();
        renderMonitoringHeroStats();
        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Отслеживание лотов доступно внутри Telegram Mini App.";
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
            note.textContent = state.watchlist.length
                ? "По текущему фильтру ничего нет. Попробуйте «Все»."
                : "Сохранённых лотов пока нет. Нажмите «В избранное» в карточке объявления.";
            elements.watchlistList.appendChild(note);
            return;
        }

        // Always compute in BYN, convert for display at the end.
        const rate = state.usdRateByn || 1;
        const currencySymbol = state.currency === "USD" ? "$" : "BYN";

        for (const item of filteredWatchlist) {
            const card = document.createElement("article");
            card.className = "watchlist-card";

            const currentPriceByn = item.current_price_byn || item.initial_price_byn;
            const hasValidPrice = currentPriceByn && Number(currentPriceByn) > 0;
            const currentPriceDisplay = hasValidPrice
                ? (state.currency === "USD" ? Math.round(currentPriceByn / rate) : Math.round(currentPriceByn))
                : null;

            const deltaBynRaw = item.price_delta_byn;
            const deltaPercent = item.price_delta_percent;
            const deltaDisplay = deltaBynRaw != null
                ? (state.currency === "USD" ? deltaBynRaw / rate : deltaBynRaw)
                : null;

            let deltaMarkup = "";
            if (deltaDisplay != null && Math.abs(deltaDisplay) > 0.5) {
                const deltaNum = Math.round(deltaDisplay);
                const deltaClass = deltaNum < 0 ? "down" : deltaNum > 0 ? "up" : "neutral";
                const deltaSign = deltaNum > 0 ? "+" : "";
                const arrow = deltaNum < 0 ? "📉" : deltaNum > 0 ? "📈" : "≈";
                const percentText = deltaPercent != null ? ` (${deltaSign}${deltaPercent}%)` : "";
                deltaMarkup = `<span class="watchlist-price-delta ${deltaClass}">${arrow} ${deltaSign}${deltaNum} ${currencySymbol}${percentText}</span>`;
            }

            // Use the item's own stored market median (captured when added to watchlist),
            // not the current search query median.
            const itemMedianByn = item.market_median_byn
                ? Number(item.market_median_byn)
                : null;
            let potentialProfitMarkup = "";
            if (itemMedianByn && currentPriceByn && itemMedianByn > 0) {
                const profitByn = itemMedianByn - Number(currentPriceByn);
                const profitDisplay = state.currency === "USD" ? profitByn / rate : profitByn;
                const profitPercent = currentPriceByn > 0 ? Math.round((profitByn / Number(currentPriceByn)) * 100) : 0;
                const profitClass = profitByn >= 0 ? "profit-positive" : "profit-negative";
                const profitSign = profitByn >= 0 ? "+" : "";
                potentialProfitMarkup = `<span class="watchlist-profit ${profitClass}">Потенциал: ${profitSign}${Math.round(profitDisplay)} ${currencySymbol} (${profitSign}${profitPercent}%)</span>`;
            }

            const isMarketSignal = item.market_status && ["price_drop", "missing", "duplicate"].includes(item.market_status);
            let marketBadgeMarkup = "";
            if (isMarketSignal) {
                let missingAgeText = "";
                if (item.market_status === "missing" && item.missing_since_at) {
                    const missingDate = new Date(item.missing_since_at);
                    const diffMs = Date.now() - missingDate.getTime();
                    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
                    if (diffDays >= 1) {
                        missingAgeText = ` (${diffDays}д)`;
                    }
                }
                marketBadgeMarkup = `<span class="market-badge ${item.market_status}">${marketLabel(item.market_status)}${missingAgeText}</span>`;
            }

            const thumbMarkup = item.thumbnail
                ? `<img class="watchlist-thumb" src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy">`
                : `<div class="watchlist-thumb-placeholder">Нет фото</div>`;

            const isMissing = item.market_status === "missing";
            const missingBanner = isMissing
                ? `<div class="watchlist-missing-banner">Объявление снято с продажи. Будет удалено автоматически через несколько дней.</div>`
                : "";

            card.innerHTML = `
                ${missingBanner}
                <div class="watchlist-card-top${isMissing ? " is-missing" : ""}">
                    ${thumbMarkup}
                    <div class="watchlist-card-body">
                        <strong class="watchlist-card-title">${escapeHtml(item.title)}</strong>
                        <div class="watchlist-card-price-row">
                            <span class="watchlist-card-price mono">${currentPriceDisplay ? `${currentPriceDisplay} ${currencySymbol}` : "—"}</span>
                            ${deltaMarkup}
                        </div>
                        ${potentialProfitMarkup}
                        <div class="watchlist-card-meta">
                            ${marketBadgeMarkup}
                        </div>
                    </div>
                </div>
                <div class="watchlist-card-fields">
                    <label class="wl-field">
                        <span class="wl-field-label">Важность</span>
                        <select class="wl-status-select" data-role="status">
                            <option value="default">Обычное</option>
                            <option value="important">Важное</option>
                            <option value="very_important">Очень важное</option>
                        </select>
                    </label>
                    <label class="wl-field wl-field-wide">
                        <span class="wl-field-label">Заметка</span>
                        <div class="wl-field-input-wrap">
                            <input data-role="notes" type="text" placeholder="заметка к лоту…">
                        </div>
                    </label>
                </div>
                <div class="watchlist-card-actions">
                    <button class="wl-btn wl-btn--detail" data-role="detail" type="button">Подробнее</button>
                    ${!isMissing ? `<button class="wl-btn wl-btn--accent" data-role="lead" type="button">В покупки</button>` : ""}
                    ${!isMissing ? `<a class="wl-btn" href="${escapeHtml(item.link)}" target="_blank" rel="noreferrer noopener">Kufar ↗</a>` : ""}
                    <button class="wl-btn wl-btn--danger" data-role="delete" type="button">Удалить</button>
                </div>
            `;

            const statusSelect = card.querySelector('[data-role="status"]');
            if (statusSelect) {
                statusSelect.value = item.workflow_status || "default";
                statusSelect.addEventListener("change", () => {
                    showToast("Важность обновлена");
                    void actions.updateWatchlistStatus(item.id, statusSelect.value);
                });
            }
            card.querySelector('[data-role="detail"]')?.addEventListener("click", () => {
                void actions.openWatchlistDetail(item);
            });
            card.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
                void actions.promoteWatchlistToLead(item);
            });
            card.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteWatchlistItem(item.id);
            });
            const notesInput = card.querySelector('[data-role="notes"]');
            if (notesInput) {
                notesInput.value = item.notes || "";
                notesInput.addEventListener("change", () => {
                    showToast("Заметка сохранена");
                    void actions.updateWatchlistMeta(item.id, {
                        notes: notesInput.value.trim() || null,
                    });
                });
            }
            elements.watchlistList.appendChild(card);
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
                    <strong class="tracker-query">${tracker.query}</strong>
                    <span class="tracker-meta mono">${trackerMeta.join(" • ")}</span>
                </div>
                <div class="tracker-row-actions">
                    <button class="ghost-btn small" data-role="open" type="button">Открыть</button>
                    <button class="ghost-btn small danger" data-role="delete" type="button">Удалить</button>
                </div>
            `;
            row.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                showToast("Загружаю...");
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
            note.textContent = "Откройте Mini App внутри Telegram, чтобы видеть события.";
            elements.trackerEventsList.appendChild(note);
            return;
        }

        const dropCount = state.trackerEvents.filter((e) => e.event_type === "price_drop").length;
        const newCount = state.trackerEvents.filter((e) => e.event_type === "new_listing").length;
        const totalCount = state.trackerEvents.length;

        if (elements.trackerEventsBadge) {
            elements.trackerEventsBadge.textContent = totalCount > 0 ? `${totalCount} событий` : "чат + Mini App";
        }

        for (const button of elements.trackerEventFilterButtons) {
            const filter = button.dataset.eventFilter;
            const count = filter === "all" ? totalCount : filter === "price_drop" ? dropCount : newCount;
            const label = filter === "all" ? "Все" : filter === "price_drop" ? "Упали в цене" : "Новые лоты";
            button.textContent = count > 0 ? `${label} (${count})` : label;
            button.classList.toggle("active", filter === state.trackerEventFilter);
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
                ? "Событий пока нет. Они появятся после первой проверки планировщика."
                : "По этому фильтру событий пока нет.";
            elements.trackerEventsList.appendChild(note);
            return;
        }

        for (const event of filteredEvents) {
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
            row.innerHTML = `
                <div class="tracker-event-top">
                    <span class="tracker-event-type ${typeClass}">${typeLabel}</span>
                    <span class="tracker-event-time mono">${formatDate(event.created_at)}</span>
                </div>
                <strong class="tracker-event-title">${escapeHtml(event.title)}</strong>
                <div class="tracker-event-meta">${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
                <div class="listing-actions">
                    <button class="listing-btn" data-role="open-query" type="button">Открыть</button>
                    <button class="listing-btn" data-role="lead" type="button">В покупки</button>
                    <a class="listing-btn listing-btn--accent" href="${escapeHtml(event.link)}" target="_blank" rel="noreferrer noopener">Kufar</a>
                </div>
            `;
            row.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
                showToast("Открываю...");
                if (event.query) {
                    elements.searchInput.value = event.query;
                    state.query = event.query;
                }
                state.strictSearch = Boolean(event.strict_mode);
                renderStrictSearch();
                void actions.openListingDetail({
                    ad_id: event.ad_id,
                    title: event.title,
                    link: event.link,
                    price_byn: event.price_byn,
                });
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
                <span class="detail-field-value">${formatPrice(estimate.target_price)} • ${Math.round(estimate.profit_byn)} BYN (${estimate.profit_percent > 0 ? "+" : ""}${escapeHtml(estimate.profit_percent)}%)</span>
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
                <span class="detail-field-value">${Math.round(detail.liquidity.score)} • ${(detail.liquidity.reasons || []).map(String).map(escapeHtml).join(" · ")}</span>
            `;
            elements.detailLiquidity.appendChild(item);
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
            formatDelta(detail.price_vs_median),
        ].filter(Boolean);
        elements.detailMeta.innerHTML = metaItems.map((item) => `<span class="detail-pill">${escapeHtml(item)}</span>`).join("");

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
            button.innerHTML = `<img src="${escapeHtml(image)}" alt="">`;
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

        void actions.loadDetailRisks(detail);

        // Hide "Следить" button if item is already in watchlist
        if (elements.detailAddWatchlistButton) {
            elements.detailAddWatchlistButton.hidden = state.detailFromWatchlist || false;
        }

        elements.detailModal.hidden = false;
    }

    function renderDetailRisks(riskData) {
        elements.detailRisks.innerHTML = "";

        if (!riskData || !riskData.risks || riskData.risks.length === 0) {
            elements.detailRiskBlock.hidden = true;
            return;
        }

        const item = document.createElement("div");
        item.className = "detail-field";
        const overallEmoji = riskData.overall_emoji || "🟢";
        const overallLabel = {
            low: "Низкий риск",
            medium: "Средний риск",
            high: "Высокий риск",
        }[riskData.overall_risk] || riskData.overall_risk;

        const riskBadges = riskData.risks
            .map((risk) => {
                const levelClass = {
                    low: "risk-low",
                    medium: "risk-medium",
                    high: "risk-high",
                }[risk.level] || "";
                return `<span class="risk-badge ${levelClass}">${escapeHtml(risk.message)}</span>`;
            })
            .join("");

        item.innerHTML = `
            <span class="detail-field-label">${escapeHtml(overallEmoji)} ${escapeHtml(overallLabel)}</span>
            <div class="risk-badges-wrap">${riskBadges}</div>
        `;
        elements.detailRisks.appendChild(item);
        elements.detailRiskBlock.hidden = false;
    }

    function closeDetailModal() {
        state.detail = null;
        state.detailImageIndex = 0;
        state.detailFromWatchlist = false;
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

    /* ===== Profit Dashboard ===== */
    function renderProfitDashboard() {
        if (!elements.profitCards) return;
        elements.profitCards.innerHTML = "";

        if (!hasTelegramInitData() || !state.leads.length) {
            elements.profitDashboardSection.hidden = true;
            return;
        }

        elements.profitDashboardSection.hidden = false;

        // Currency conversion setup
        const rate = state.usdRateByn || 1;
        const currencySymbol = state.currency === "USD" ? "$" : "BYN";

        // Only count CLOSED deals in finances - not bought or sold
        const closedLeads = state.leads.filter(
            (l) => l.status === "closed" && l.buy_price_byn && l.sold_price_byn
        );

        let totalInvested = 0;
        let totalSoldRevenue = 0;

        closedLeads.forEach((l) => {
            totalInvested += Number(l.buy_price_byn || 0);
            totalSoldRevenue += Number(l.sold_price_byn || 0);
        });

        // Convert totals for display
        const displayInvested = state.currency === "USD" ? totalInvested / rate : totalInvested;
        const displaySoldRevenue = state.currency === "USD" ? totalSoldRevenue / rate : totalSoldRevenue;
        const displayProfit = displaySoldRevenue - displayInvested;
        const roi = totalInvested > 0 ? (((totalSoldRevenue - totalInvested) / totalInvested) * 100).toFixed(1) : "0";

        const cards = [
            {
                label: "Вложено",
                value: `${Math.round(displayInvested)} ${currencySymbol}`,
                sub: `${closedLeads.length} закрытых сделок`,
                className: "",
            },
            {
                label: "Прибыль",
                value: `${displayProfit >= 0 ? "+" : ""}${Math.round(displayProfit)} ${currencySymbol}`,
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

    }

    /* ===== History Deals ===== */
    function renderHistoryDeals() {
        if (!elements.historyDealsList) return;
        elements.historyDealsList.innerHTML = "";

        // Currency conversion setup
        const rate = state.usdRateByn || 1;
        const currencySymbol = state.currency === "USD" ? "$" : "BYN";

        // Get only closed deals
        const closedLeads = state.leads.filter((l) => l.status === "closed");

        // Update count badge
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

        // Sort by most recent first
        const sortedLeads = [...closedLeads].sort((a, b) => {
            return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
        });

        for (const lead of sortedLeads) {
            const card = document.createElement("div");
            card.className = "history-deal-card";
            card.dataset.leadId = lead.id;

            const buyPriceBynRaw = lead.buy_price_byn ? Number(lead.buy_price_byn) : null;
            const soldPriceBynRaw = lead.sold_price_byn ? Number(lead.sold_price_byn) : null;
            
            // Convert prices for display
            const buyPrice = buyPriceBynRaw ? (state.currency === "USD" ? Math.round(buyPriceBynRaw / rate) : Math.round(buyPriceBynRaw)) : "?";
            const soldPrice = soldPriceBynRaw ? (state.currency === "USD" ? Math.round(soldPriceBynRaw / rate) : Math.round(soldPriceBynRaw)) : "?";
            const profitRaw = soldPriceBynRaw && buyPriceBynRaw ? soldPriceBynRaw - buyPriceBynRaw : null;
            const profit = profitRaw !== null ? (state.currency === "USD" ? profitRaw / rate : profitRaw) : null;
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
                        <span class="history-deal-price">${buyPrice} → ${soldPrice} ${currencySymbol}</span>
                        <span class="history-deal-date">${dateStr}</span>
                    </div>
                </div>
                <div class="history-deal-profit ${profitClass}">
                    ${profit !== null ? `${profitSign}${Math.round(profit)} ${currencySymbol}` : "—"}
                </div>
                <button class="history-deal-delete" data-role="delete-history-deal" type="button" aria-label="Удалить из истории">✕</button>
            `;

            card.querySelector('[data-role="delete-history-deal"]')?.addEventListener("click", () => {
                void actions.deleteHistoryDeal(lead.id);
            });

            elements.historyDealsList.appendChild(card);
        }
    }

    /* ===== Expenses Modal ===== */
    function renderExpensesModal() {
        if (!elements.expensesModal) return;
        elements.expensesList.innerHTML = "";

        if (!state.expenses.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Расходов пока нет.";
            elements.expensesList.appendChild(note);
            return;
        }

        // Currency conversion setup
        const rate = state.usdRateByn || 1;
        const currencySymbol = state.currency === "USD" ? "$" : "BYN";

        let totalExpenses = 0;
        for (const expense of state.expenses) {
            const amount = Number(expense.amount_byn || 0);
            totalExpenses += amount;
            const displayAmount = state.currency === "USD" ? Math.round(amount / rate) : Math.round(amount);
            const row = document.createElement("div");
            row.className = "expense-row";
            const typeLabels = { delivery: "🚚 Доставка", repair: "🔧 Ремонт", other: "📦 Другое" };
            row.innerHTML = `
                <div class="expense-main">
                    <span class="expense-type">${typeLabels[expense.expense_type] || expense.expense_type}</span>
                    <span class="expense-meta">${expense.notes || ""}</span>
                </div>
                <span class="expense-amount mono">-${displayAmount} ${currencySymbol}</span>
                <button class="expense-delete-btn" data-expense-id="${expense.id}" type="button" aria-label="Удалить расход">✕</button>
            `;
            row.querySelector('[data-expense-id]')?.addEventListener("click", () => {
                void actions.deleteExpense(state.currentExpenseLeadId, expense.id);
            });
            elements.expensesList.appendChild(row);
        }

        // Show total
        const displayTotal = state.currency === "USD" ? Math.round(totalExpenses / rate) : Math.round(totalExpenses);
        const totalRow = document.createElement("div");
        totalRow.className = "expense-total";
        totalRow.innerHTML = `
            <span class="expense-total-label">Итого расходов</span>
            <span class="expense-total-value mono">-${displayTotal} ${currencySymbol}</span>
        `;
        elements.expensesList.prepend(totalRow);
    }

    function openExpensesModal(leadId, leadTitle) {
        state.currentExpenseLeadId = leadId;
        state.expenses = [];
        if (elements.expensesSubtitle) {
            elements.expensesSubtitle.textContent = leadTitle;
            elements.expensesSubtitle.hidden = false;
        }
        if (elements.expensesModal) {
            elements.expensesModal.hidden = false;
        }
        void actions.loadExpenses(leadId);
    }

    function closeExpensesModal() {
        if (elements.expensesModal) {
            elements.expensesModal.hidden = true;
        }
        state.currentExpenseLeadId = null;
        state.expenses = [];
        if (elements.expenseTypeSelect) elements.expenseTypeSelect.value = "delivery";
        if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
        if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
    }

    function renderVelocity() {
        // Placeholder — market velocity feature not yet implemented
    }

    function renderAll() {
        renderError();
        renderLoading();
        renderCurrencyButtons();
        renderStrictSearch();
        renderViewTabs();
        renderPanels();
        renderSummary();
        renderHelper();
        renderViews();
        renderTrackingHeroStats();
        renderCheapHeroStats();
        renderMonitoringHeroStats();
        renderDealsHeroStats();
        renderSortButtons();
        renderDiscountButtons();
        renderTrackerEventFilters();
        renderDealInputs();
        renderTrackerInputs();
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
        renderProfitDashboard();
    }

    return {
        showToast,
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
        renderHistoryDeals,
        renderWatchlist,
        renderProfitDashboard,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
        renderDetailRisks,
        renderVelocity,
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
