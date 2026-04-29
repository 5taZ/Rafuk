/**
 * render_views.js — Hero stats, Stats, segments, geography,
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
        const trackerCount = state.trackers.items.length;
        const eventCount = state.trackers.events.length;
        clearChildren(elements.trackingHeroStats);
        appendHeroStat(elements.trackingHeroStats, String(trackerCount), "трекеров");
        appendHeroStat(elements.trackingHeroStats, String(eventCount), "событий");
    }

    function renderDealsHeroStats() {
        if (!elements.dealsHeroStats) return;
        const activeLeads = state.leads.items.filter((l) => l.status !== "closed");
        clearChildren(elements.dealsHeroStats);
        appendHeroStat(elements.dealsHeroStats, String(activeLeads.length), "сделок");

        // Pipeline progress indicator — show status distribution
        if (activeLeads.length > 0) {
            const statusOrder = ["new", "researching", "bought", "sold"];
            const statusLabels = { new: "Новые", researching: "В работе", bought: "Куплено", sold: "Продано" };
            const pipeline = document.createElement("div");
            pipeline.className = "pipeline-bar";
            pipeline.setAttribute("role", "meter");
            pipeline.setAttribute("aria-label", "Прогресс сделок");
            for (const status of statusOrder) {
                const count = activeLeads.filter((l) => l.status === status).length;
                if (count > 0) {
                    const seg = document.createElement("span");
                    seg.className = `pipeline-seg pipeline-seg--${status}`;
                    seg.style.width = `${(count / activeLeads.length) * 100}%`;
                    seg.textContent = `${statusLabels[status] || status} ${count}`;
                    pipeline.appendChild(seg);
                }
            }
            elements.dealsHeroStats.appendChild(pipeline);
        }
    }

    /* ===== Sort / Discount / Filter buttons ===== */

    function renderFilterDropdown() {
        return safeRender('renderFilterDropdown', () => {
            if (!elements.filterDropdown || !elements.filterCategories) return;

            // Toggle visibility
            elements.filterDropdown.hidden = !state.filters.filterDropdownOpen;

            // Hide category group when strict search is on
            const categoryGroup = elements.filterCategories?.closest(".filter-group");
            if (categoryGroup) {
                categoryGroup.hidden = !!state.search.strictSearch;
            }

            // Render category chips
            clearChildren(elements.filterCategories);
            if (!state.filters.categories.length || state.filters.categories.length <= 1) {
                const empty = document.createElement("span");
                empty.className = "filter-empty";
                empty.textContent = "Нет категорий";
                elements.filterCategories.appendChild(empty);
            } else {
                // Use total_results from stats instead of sum of categories
                // because not all ads have category data
                const totalResults = Number(state.misc.stats?.total_results || 0);
                const totalCount = state.filters.categories.reduce((sum, cat) => sum + cat.count, 0);
                // Show the larger of: total results from Kufar, or sum of categories
                const displayTotal = Math.max(totalResults, totalCount);
                
                // Use pendingCategory for display, fall back to applied category
                const displayCategory = state.filters.pendingCategory !== undefined ? state.filters.pendingCategory : state.filters.category;
                
                const allButton = document.createElement("button");
                allButton.className = `filter-chip ${displayCategory == null ? 'active' : ''}`;
                allButton.dataset.category = "";
                allButton.type = "button";
                allButton.textContent = `Все (${displayTotal})`;
                elements.filterCategories.appendChild(allButton);
                for (const cat of state.filters.categories) {
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
                elements.filterMinPrice.value = state.filters.pendingMinPrice != null ? state.filters.pendingMinPrice : "";
            }
            if (elements.filterMaxPrice) {
                elements.filterMaxPrice.value = state.filters.pendingMaxPrice != null ? state.filters.pendingMaxPrice : "";
            }

            // Update region dropdown with PENDING value
            if (elements.filterRegion) {
                elements.filterRegion.value = state.filters.pendingRegionName || "";
            }

            // Update condition/seller chip states with PENDING values
            if (elements.filterConditions) {
                for (const chip of elements.filterConditions.querySelectorAll("[data-condition]")) {
                    chip.classList.toggle("active", chip.dataset.condition === state.filters.pendingCondition);
                }
            }
            if (elements.filterSellers) {
                for (const chip of elements.filterSellers.querySelectorAll("[data-seller]")) {
                    chip.classList.toggle("active", chip.dataset.seller === state.filters.pendingSellerType);
                }
            }

            // Update filter count badge on the filter button
            const badge = document.getElementById("filter-active-badge");
            if (badge) {
                let count = 0;
                if (state.filters.category != null) count++;
                if (state.filters.condition) count++;
                if (state.filters.sellerType) count++;
                if (state.filters.minPrice != null) count++;
                if (state.filters.maxPrice != null) count++;
                if (state.filters.regionName) count++;
                badge.textContent = count;
                badge.hidden = count === 0;
            }
        });
    }

    function renderSortButtons() {
        for (const button of elements.sortButtons) {
            button.classList.toggle("active", button.dataset.sort === state.search.sort);
        }
    }

    function renderDiscountButtons() {
        for (const button of elements.discountButtons) {
            const from = Number(button.dataset.discountFrom);
            const to = Number(button.dataset.discountTo);
            button.classList.toggle(
                "active",
                from === state.filters.discountFromPercent && to === state.filters.discountToPercent
            );
        }
    }

    function renderHistoryRangeButtons() {
        if (elements.historyBadge) {
            elements.historyBadge.textContent = `${state.misc.historyDays} дней`;
        }
        for (const button of elements.historyRangeButtons) {
            button.classList.toggle(
                "active",
                Number(button.dataset.historyDays) === state.misc.historyDays
            );
        }
    }

    /* ===== Inputs ===== */

    function renderDealInputs() {
        if (elements.dealFromInput) {
            elements.dealFromInput.value = String(state.filters.discountFromPercent);
        }
        if (elements.dealToInput) {
            elements.dealToInput.value = String(state.filters.discountToPercent);
        }
    }

    function renderTrackerInputs() {
        if (elements.trackerMinDiscountInput) {
            elements.trackerMinDiscountInput.value = String(state.trackers.minDiscountPercent ?? 10);
        }
        if (elements.trackerMaxPriceInput) {
            elements.trackerMaxPriceInput.value = state.trackers.maxPriceByn ?? "";
        }
        if (elements.trackerExcludeDuplicatesToggle) {
            elements.trackerExcludeDuplicatesToggle.checked = Boolean(state.trackers.excludeDuplicates);
        }
        if (elements.trackerSellerSelect) {
            elements.trackerSellerSelect.value = state.trackers.sellerType || "";
        }
        if (elements.trackerConditionSelect) {
            elements.trackerConditionSelect.value = state.trackers.condition || "";
        }
        if (elements.trackerRegionSelect) {
            elements.trackerRegionSelect.value = state.trackers.regionName || "";
        }
        if (elements.trackerConfigInput) {
            elements.trackerConfigInput.value = state.trackers.configKeyword || "";
        }
    }

    /* ===== Stats ===== */

    function renderStats() {
        return safeRender('renderStats', () => {
            if (!state.misc.stats) {
            elements.statsSection.hidden = true;
            elements.chartSection.hidden = true;
            if (context._hooks?.destroyChart) context._hooks.destroyChart();
            return;
        }

        elements.stats.median.textContent = formatPrice(state.misc.stats.median);
        elements.stats.mean.textContent = formatPrice(state.misc.stats.mean);
        elements.stats.min.textContent = formatPrice(state.misc.stats.min);
        elements.stats.max.textContent = formatPrice(state.misc.stats.max);
        if (state.misc.stats.fair_price_from != null && state.misc.stats.fair_price_to != null) {
            elements.stats.fairRange.replaceChildren(
                domEl("span", { className: "stat-range-item", text: formatPrice(state.misc.stats.fair_price_from) }),
                domEl("span", { className: "stat-range-sep", text: "-" }),
                domEl("span", { className: "stat-range-item", text: formatPrice(state.misc.stats.fair_price_to) }),
            );
        } else {
            elements.stats.fairRange.textContent = "—";
        }
        const totalResults = Number(state.misc.stats.total_results || 0);
        const analyzedCount = Number(state.misc.stats.analyzed_count || state.misc.stats.count || 0);
        elements.stats.coverage.textContent =
            `${analyzedCount} с ценой / ${totalResults}`;
        // Show total with priced count in badge for better clarity
        elements.marketTotalBadge.textContent = `${totalResults} (${analyzedCount} с ценой)`;
        elements.statsSection.hidden = false;
        elements.chartSection.hidden = state.misc.stats.count <= 0;
        if (elements.chartSection.hidden || !state.panels.distribution) {
            if (context._hooks?.destroyChart) context._hooks.destroyChart();
        }
        });
    }

    /* ===== Segments ===== */

    function renderSegments() {
        return safeRender('renderSegments', () => {
        clearChildren(elements.segmentsGrid);
        if (!state.misc.segments) {
            elements.segmentsSection.hidden = true;
            return;
        }

        const segments = [
            {
                type: "new",
                typeLabel: "Новый",
                sellerLabel: "Частное лицо",
                data: state.misc.segments.new_private,
            },
            {
                type: "new",
                typeLabel: "Новый",
                sellerLabel: "Магазин",
                data: state.misc.segments.new_shop,
            },
            {
                type: "used",
                typeLabel: "Б/у",
                sellerLabel: "Частное лицо",
                data: state.misc.segments.used_private,
            },
            {
                type: "used",
                typeLabel: "Б/у",
                sellerLabel: "Магазин",
                data: state.misc.segments.used_shop,
            },
        ].filter((segment) => segment.data && segment.data.count > 0);

        if (segments.length === 0) {
            elements.segmentsSection.hidden = true;
            return;
        }

        for (const segment of segments) {
            const totalAnalyzed = Number(state.misc.stats?.analyzed_count || state.misc.stats?.count || 0);
            const share = totalAnalyzed
                ? Math.round((Number(segment.data.count || 0) / totalAnalyzed) * 100)
                : 0;
            const marketMedian = Number(state.misc.stats?.median || 0);
            const segmentMedian = Number(segment.data.median || 0);
            const delta = marketMedian && segmentMedian
                ? ((segmentMedian - marketMedian) / marketMedian) * 100
                : 0;
            const deltaClassName = Math.abs(delta) < 0.5 ? "neutral" : (delta >= 0 ? "over" : "under");
            const deltaText = Math.abs(delta) < 0.5
                ? "≈ рынок"
                : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}% к рынку`;
            const card = domEl(
                "div",
                { className: "seg-card" },
                domEl(
                    "div",
                    { className: "seg-head" },
                    domEl("span", { className: `seg-pill ${segment.type}`, text: segment.typeLabel }),
                    domEl("span", { className: "seg-seller", text: segment.sellerLabel }),
                ),
                domEl("span", { className: "seg-price mono", text: formatPrice(segment.data.median) }),
                domEl(
                    "div",
                    { className: "seg-meta-row" },
                    domEl("span", { className: "seg-count", text: `${segment.data.count} с ценой` }),
                    domEl("span", { className: "seg-share", text: `${share}% выборки` }),
                ),
                domEl("span", { className: `seg-delta ${deltaClassName}`, text: deltaText }),
            );
            elements.segmentsGrid.appendChild(card);
        }

        elements.segmentsSection.hidden = false;
        });
    }

    /* ===== Geography ===== */

    function renderGeography() {
        return safeRender('renderGeography', () => {
        clearChildren(elements.geographyGrid);
        if (!state.misc.geography.length) {
            elements.geographySection.hidden = true;
            return;
        }

        for (const region of state.misc.geography) {
            const card = domEl(
                "div",
                { className: "geo-card" },
                domEl(
                    "div",
                    { className: "geo-head" },
                    domEl("strong", { className: "geo-name", text: region.region_name }),
                    domEl("span", { className: "geo-share", text: `${region.share_percent}% выборки` }),
                ),
                domEl("span", { className: "geo-price mono", text: formatPrice(region.median) }),
                domEl(
                    "div",
                    { className: "geo-meta" },
                    domEl("span", { text: `${region.count} с ценой` }),
                    domEl("span", { text: `ср. ${formatPrice(region.mean)}` }),
                ),
            );
            elements.geographyGrid.appendChild(card);
        }

        elements.geographySection.hidden = false;
        });
    }

    /* ===== Recent Searches ===== */

    function renderRecentSearches() {
        return safeRender('renderRecentSearches', () => {
            if (!elements.recentSection || !elements.recentList) return;
            const searches = state.search.recentSearches || [];
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
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderHistoryRangeButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderFilterDropdown,
    };
}
