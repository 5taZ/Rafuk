/**
 * render_views.js — Hero stats, Stats, segments, geography, comparison,
 * sort/discount buttons, tracker event filters, history range, deal/tracker inputs.
 */

function createRenderViews(context) {
    const {
        state,
        elements,
        formatPrice,
        escapeHtml: escapeHtml,
        safeRender: safeRender,
    } = context;

    function clearChildren(node) {
        if (node) node.replaceChildren();
    }

    function appendHeroStat(container, value, label) {
        const stat = document.createElement("span");
        stat.className = "hero-stat";
        const valueEl = document.createElement("span");
        valueEl.className = "hero-stat-val mono";
        valueEl.textContent = value;
        stat.appendChild(valueEl);
        stat.append(` ${label}`);
        container.appendChild(stat);
    }

    /* ===== Hero Stats ===== */

    function renderTrackingHeroStats() {
        if (!elements.trackingHeroStats) return;
        const trackerCount = state.trackers.length;
        const eventCount = state.trackerEvents.length;
        clearChildren(elements.trackingHeroStats);
        appendHeroStat(elements.trackingHeroStats, String(trackerCount), "трекеров");
        appendHeroStat(elements.trackingHeroStats, String(eventCount), "событий");
    }

    function renderCheapHeroStats() {
        if (!elements.cheapHeroStats) return;
        const cheapCount = state.dealListings.length;
        const range = state.discountFromPercent === state.discountToPercent
            ? `${state.discountFromPercent}%`
            : `${state.discountFromPercent}-${state.discountToPercent}%`;
        clearChildren(elements.cheapHeroStats);
        appendHeroStat(elements.cheapHeroStats, String(cheapCount), "лотов дешевле рынка");
        const rangeStat = document.createElement("span");
        rangeStat.className = "hero-stat";
        rangeStat.append("диапазон ");
        const rangeValue = document.createElement("span");
        rangeValue.className = "hero-stat-val mono";
        rangeValue.textContent = range;
        rangeStat.appendChild(rangeValue);
        elements.cheapHeroStats.appendChild(rangeStat);
    }

    function renderMonitoringHeroStats() {
        if (!elements.monitoringHeroStats) return;
        const watchCount = state.watchlist.length;
        clearChildren(elements.monitoringHeroStats);
        appendHeroStat(elements.monitoringHeroStats, String(watchCount), "объявлений");
    }

    function renderDealsHeroStats() {
        if (!elements.dealsHeroStats) return;
        const activeLeads = state.leads.filter((l) => l.status !== "closed");
        clearChildren(elements.dealsHeroStats);
        appendHeroStat(elements.dealsHeroStats, String(activeLeads.length), "сделок");
    }

    /* ===== Sort / Discount / Filter buttons ===== */

    function renderFilterDropdown() {
        return safeRender('renderFilterDropdown', () => {
            if (!elements.filterDropdown || !elements.filterCategories) return;

            // Toggle visibility
            elements.filterDropdown.hidden = !state.filterDropdownOpen;

            // Hide category group when strict search is on
            const categoryGroup = elements.filterCategories?.closest(".filter-group");
            if (categoryGroup) {
                categoryGroup.hidden = !!state.strictSearch;
            }

            // Render category chips
            clearChildren(elements.filterCategories);
            if (!state.categories.length || state.categories.length <= 1) {
                const empty = document.createElement("span");
                empty.className = "filter-empty";
                empty.textContent = "Нет категорий";
                elements.filterCategories.appendChild(empty);
            } else {
                // Use total_results from stats instead of sum of categories
                // because not all ads have category data
                const totalResults = Number(state.stats?.total_results || 0);
                const totalCount = state.categories.reduce((sum, cat) => sum + cat.count, 0);
                // Show the larger of: total results from Kufar, or sum of categories
                const displayTotal = Math.max(totalResults, totalCount);
                
                // Use pendingCategory for display, fall back to applied category
                const displayCategory = state.pendingCategory !== undefined ? state.pendingCategory : state.category;
                
                const allButton = document.createElement("button");
                allButton.className = `filter-chip ${displayCategory == null ? 'active' : ''}`;
                allButton.dataset.category = "";
                allButton.type = "button";
                allButton.textContent = `Все (${displayTotal})`;
                elements.filterCategories.appendChild(allButton);
                for (const cat of state.categories) {
                    const button = document.createElement("button");
                    button.className = `filter-chip ${displayCategory === cat.id ? 'active' : ''}`;
                    button.dataset.category = String(cat.id);
                    button.type = "button";
                    button.textContent = `${cat.label} (${cat.count})`;
                    elements.filterCategories.appendChild(button);
                }
            }

            // Update price range inputs with PENDING values
            if (elements.filterMinPrice) {
                elements.filterMinPrice.value = state.pendingMinPrice != null ? state.pendingMinPrice : "";
            }
            if (elements.filterMaxPrice) {
                elements.filterMaxPrice.value = state.pendingMaxPrice != null ? state.pendingMaxPrice : "";
            }

            // Update region dropdown with PENDING value
            if (elements.filterRegion) {
                elements.filterRegion.value = state.pendingRegionName || "";
            }

            // Update condition/seller chip states with PENDING values
            if (elements.filterConditions) {
                for (const chip of elements.filterConditions.querySelectorAll("[data-condition]")) {
                    chip.classList.toggle("active", chip.dataset.condition === state.pendingCondition);
                }
            }
            if (elements.filterSellers) {
                for (const chip of elements.filterSellers.querySelectorAll("[data-seller]")) {
                    chip.classList.toggle("active", chip.dataset.seller === state.pendingSellerType);
                }
            }
        });
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

    /* ===== Inputs ===== */

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
        if (elements.trackerRegionSelect) {
            elements.trackerRegionSelect.value = state.trackerRegionName || "";
        }
        if (elements.trackerConfigInput) {
            elements.trackerConfigInput.value = state.trackerConfigKeyword || "";
        }
    }

    /* ===== Comparison ===== */

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
        return safeRender('renderComparison', () => {
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
        });
    }

    /* ===== Stats ===== */

    function renderStats() {
        return safeRender('renderStats', () => {
            if (!state.stats) {
            elements.statsSection.hidden = true;
            elements.chartSection.hidden = true;
            if (context._hooks?.destroyChart) context._hooks.destroyChart();
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
        const totalResults = Number(state.stats.total_results || 0);
        const analyzedCount = Number(state.stats.analyzed_count || state.stats.count || 0);
        elements.stats.coverage.textContent =
            `${analyzedCount} с ценой / ${totalResults}`;
        // Show total with priced count in badge for better clarity
        elements.marketTotalBadge.textContent = `${totalResults} (${analyzedCount} с ценой)`;
        elements.statsSection.hidden = false;
        elements.chartSection.hidden = state.stats.count <= 0;
        if (elements.chartSection.hidden || !state.panels.distribution) {
            if (context._hooks?.destroyChart) context._hooks.destroyChart();
        }
        });
    }

    /* ===== Segments ===== */

    function renderSegments() {
        return safeRender('renderSegments', () => {
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
        });
    }

    /* ===== Geography ===== */

    function renderGeography() {
        return safeRender('renderGeography', () => {
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
        });
    }

    /* ===== Recent Searches ===== */

    function renderRecentSearches() {
        return safeRender('renderRecentSearches', () => {
            if (!elements.recentSection || !elements.recentList) return;
            const searches = state.recentSearches || [];
            if (!searches.length) {
                elements.recentSection.hidden = true;
                clearChildren(elements.recentList);
                return;
            }
            elements.recentSection.hidden = false;
            clearChildren(elements.recentList);
            const fragment = document.createDocumentFragment();
            for (const query of searches) {
                const button = document.createElement("button");
                button.className = "recent-chip";
                button.type = "button";
                button.dataset.recentQuery = query;
                button.textContent = query;
                fragment.appendChild(button);
            }
            elements.recentList.appendChild(fragment);
        });
    }

    return {
        renderTrackingHeroStats,
        renderCheapHeroStats,
        renderMonitoringHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderHistoryRangeButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderComparison,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderFilterDropdown,
    };
}
