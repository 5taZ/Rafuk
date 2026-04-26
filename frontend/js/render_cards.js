/**
 * render_cards.js — buildListingNode, renderListings, renderDeals,
 * opportunity board, lead cards, watchlist cards.
 */

function createRenderCards(context) {
    const {
        state,
        elements,
        actions,
        hasTelegramInitData,
        safeRender: safeRender,
    } = context;
    const builders = createRenderCardBuilders(context);
    const {
        buildListingNode,
        buildOpportunityCard,
        buildSignalRow,
        buildLeadNode,
        buildWatchlistNode,
    } = builders;

    /* ===== Shared helpers (from original file) ===== */

    function verdictClassName(verdict) {
        if (!verdict) return "neutral";
        if (verdict.includes("Хорошая")) return "zabirat";
        if (verdict.includes("Ниже")) return "smotret";
        if (verdict.includes("Средняя")) return "norm";
        if (verdict.includes("Выше")) return "mimo";
        return "neutral";
    }

    function matchesFilters(item) {
        // Price range filter
        const itemPrice = item.price ? Number(item.price) : null;
        const hasPriceRange = state.minPrice != null || state.maxPrice != null;
        // Exclude "Договорная" (price 0/null) when a price range is set
        if (hasPriceRange && (!itemPrice || itemPrice <= 0)) return false;
        if (state.minPrice != null && itemPrice != null && itemPrice < state.minPrice) return false;
        if (state.maxPrice != null && itemPrice != null && itemPrice > state.maxPrice) return false;

        // Condition filter - handle various formats from API
        if (state.condition) {
            const itemCondition = item.condition || "";
            // Normalize condition values for comparison
            const normalizedItemCondition = itemCondition.toLowerCase();
            const normalizedStateCondition = state.condition.toLowerCase();
            
            // Map state condition to possible API values
            const conditionMap = {
                "new": ["new", "новый", "2"],
                "used": ["used", "б/у", "1"],
            };
            
            const validValues = conditionMap[normalizedStateCondition] || [normalizedStateCondition];
            if (!validValues.includes(normalizedItemCondition)) return false;
        }
        
        // Seller type filter
        if (state.sellerType) {
            const isShop = item.company_ad || item.seller_type === "Магазин" || item.seller_type === "shop" || item.seller_type?.toLowerCase() === "shop";
            if (state.sellerType === "private" && isShop) return false;
            if (state.sellerType === "shop" && !isShop) return false;
        }
        
        // Region filter — matches region_name or area_name exactly
        if (state.regionName) {
            const filterRegion = state.regionName.toLowerCase().trim();
            if (!filterRegion) return true;
            const itemRegion = (item.region_name || "").toLowerCase().trim();
            const itemArea = (item.area_name || "").toLowerCase().trim();
            if (!itemRegion && !itemArea) return false;
            if (itemRegion === filterRegion || itemArea === filterRegion) return true;
            return false;
        }
        
        return true;
    }

    function applyFilters(items) {
        if (!state.condition && !state.sellerType && state.minPrice == null && state.maxPrice == null && !state.regionName) return items;
        return items.filter(matchesFilters);
    }

    /* ===== Collections ===== */

    /**
     * Renders a collection of items into a container, using virtual scrolling
     * for large datasets (>50 items) to maintain performance.
     *
     * @param {Array} items - The data items to render
     * @param {HTMLElement} container - The DOM container to render into
     * @param {HTMLElement} badge - Optional badge element for item count
     * @param {string} emptyText - Text to show when no items
     * @param {number|null} totalOverride - Override for total count display
     * @param {number} [itemHeight=180] - Fixed item height for virtual list
     * @returns {boolean} True if content was rendered, false if empty
     */
    function renderListingsCollection(items, container, badge, emptyText, totalOverride = null, itemHeight = 180) {
        return safeRender('renderListingsCollection', () => {
            if (!container) return false;
            // Destroy existing virtual list if present and reset container
            if (container._virtualList) {
                container._virtualList.destroy();
                container._virtualList = null;
            }
            // Reset container styles that virtual list may have set
            container.style.overflowY = "";
            container.style.maxHeight = "";

            domClear(container);

            const filtered = applyFilters(items);
            if (!filtered.length) {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = emptyText;
                container.appendChild(note);
                if (badge) {
                    badge.textContent = "0";
                }
                return false;
            }

            // Cards use CSS content-visibility: auto for off-screen rendering skip
            for (const item of filtered) {
                const node = buildListingNode(item, verdictClassName);
                container.appendChild(node);
            }

            if (badge) {
                // Match kufar.by header behavior: the pill shows the
                // total number of matching ads on Kufar, not how many
                // we've actually rendered on the page (we cap at 200
                // for performance). Falls back to the rendered count
                // only if no API total is available.
                const apiTotal = totalOverride != null ? totalOverride : filtered.length;
                badge.textContent = `${apiTotal} объявлений`;
            }
            return true;
        });
    }

    function renderListings() {
        return safeRender('renderListings', () => {
            if (state.loading) return;
            // Keep skeletons while listings request is in flight
            if (state._listingsPending) return;
            const hasData = state.listings.length > 0 || state.listingsTotal > 0;
            const hasContent = renderListingsCollection(
                state.listings,
                elements.listingsList,
                elements.listingsTotalBadge,
                "По этому запросу пока нечего показать.",
                state.listingsTotal || null
            );
            // Keep section visible if data exists but was filtered out —
            // the empty message inside the container tells the user why.
            elements.listingsSection.hidden = !hasContent && !hasData;
        });
    }

    function renderDeals() {
        return safeRender('renderDeals', () => {
            if (state.loading) return;
            const rangeLabel = `${state.discountFromPercent}-${state.discountToPercent}`;
            const container = elements.dealsList;

            // Destroy existing virtual list if present and reset container
            if (container._virtualList) {
                container._virtualList.destroy();
                container._virtualList = null;
            }
            container.style.overflowY = "";
            container.style.maxHeight = "";

            domClear(container);
            if (!state.dealListings.length) {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = `Нет лотов в диапазоне ${rangeLabel}% ниже медианы. Попробуйте расширить диапазон или другой запрос.`;
                container.appendChild(note);
                if (elements.dealsTotalBadge) {
                    elements.dealsTotalBadge.textContent = "0";
                }
                elements.dealsSection.hidden = !state.query;
                if (!state.query) return;
                elements.dealsSection.hidden = false;
                return;
            }

            const hasData = state.dealListings.length > 0 || state.dealsTotal > 0;
            // Virtual scrolling disabled — cards have variable heights
            const filtered = applyFilters(state.dealListings);
            if (!filtered.length) {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                if (!hasData) {
                    note.textContent = `Нет лотов в диапазоне ${rangeLabel}% ниже медианы. Попробуйте расширить диапазон или другой запрос.`;
                } else {
                    note.textContent = `Фильтры скрыли все лоты. Попробуйте изменить фильтр.`;
                }
                container.appendChild(note);
                if (elements.dealsTotalBadge) {
                    elements.dealsTotalBadge.textContent = "0";
                }
                elements.dealsSection.hidden = !state.query;
                return;
            }
            for (const item of filtered) {
                container.appendChild(buildListingNode(item, verdictClassName));
            }

            if (elements.dealsTotalBadge) {
                // Same rule as the listings pill — show Kufar's total
                // count, not the on-page rendered count.
                const apiTotal = state.dealsTotal || filtered.length;
                elements.dealsTotalBadge.textContent = `${apiTotal} объявлений`;
            }
            elements.dealsSection.hidden = !state.query;
        });
    }

    /* ===== Opportunity Board ===== */

    function renderOpportunityBoard() {
        return safeRender('renderOpportunityBoard', () => {
        domClear(elements.opportunityBoardList);
        domClear(elements.opportunityBoardDrops);
        domClear(elements.opportunityBoardRare);
        domClear(elements.opportunityBoardSignals);
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

        for (const item of state.opportunityBoard.items || []) {
            elements.opportunityBoardList.appendChild(buildOpportunityCard(item, "", verdictClassName));
        }

        if (!(state.opportunityBoard.rare_opportunities || []).length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Редких офферов пока нет.";
            elements.opportunityBoardRare.appendChild(note);
        } else {
            for (const item of state.opportunityBoard.rare_opportunities || []) {
                elements.opportunityBoardRare.appendChild(buildOpportunityCard(item, "Редкий оффер", verdictClassName));
            }
        }

        if (!(state.opportunityBoard.top_price_drops || []).length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Пока нет свежих падений цены.";
            elements.opportunityBoardDrops.appendChild(note);
        } else {
            for (const signal of state.opportunityBoard.top_price_drops || []) {
                elements.opportunityBoardDrops.appendChild(buildSignalRow(signal, true));
            }
        }

        if (!(state.opportunityBoard.market_signals || []).length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Сигналы рынка появятся, когда накопится больше сохранённых поисков и истории.";
            elements.opportunityBoardSignals.appendChild(note);
        } else {
            for (const signal of state.opportunityBoard.market_signals || []) {
                elements.opportunityBoardSignals.appendChild(buildSignalRow(signal, false));
            }
        }
        });
    }

    /* ===== Leads (Deals) ===== */

    function workflowLabel(value) {
        const labels = {
            new: "Новый",
            reviewing: "Смотреть",
            in_progress: "В работе",
            negotiating: "Торг",
            bought: "Купил",
            reselling: "В продаже",
            sold: "Продано",
            closed: "Закрыто",
            skipped: "Пропустить",
            deferred: "Позже",
            watching: "Слежу",
            default: "Обычное",
            important: "Важное",
            very_important: "Очень важное",
        };
        return labels[value] || value || "Обычное";
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
            closed: 9,
        };
        return order[item.status] ?? 99;
    }

    // closed/skipped leads are "done" — they live in the collapsible
    // "История сделок" block below the list. Everything else is an
    // active purchase visible in the "Покупки" tab.
    function isActiveLead(lead) {
        return lead.status !== "closed" && lead.status !== "skipped";
    }

    function emptyMessageForFilter(filter) {
        switch (filter) {
            case "watching":
                return "Здесь будут объявления, за которыми ты следишь. Добавь лот через поиск → «В избранное».";
            case "purchases":
            default:
                return "Нет сделок в работе. Добавь лот через поиск → «В покупки».";
        }
    }

    function buildItemsEmpty(filter) {
        const buildEmpty = context.buildEmptyState;
        if (typeof buildEmpty !== "function") {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = emptyMessageForFilter(filter);
            return note;
        }
        if (filter === "watching") {
            return buildEmpty({
                icon: "watchlist",
                title: "Здесь будут отслеживаемые лоты",
                hint: "Сохрани объявление через «В избранное», и сюда придут уведомления о смене цены и снятии с продажи.",
            });
        }
        return buildEmpty({
            icon: "leads",
            title: "Нет сделок в работе",
            hint: "Найди лот через поиск и нажми «В покупки», чтобы вести его до продажи и считать прибыль.",
        });
    }

    function renderLeads() {
        return safeRender('renderLeads', () => {
        const container = elements.leadInboxList;
        if (!container) return;

        // Destroy existing virtual list if present and reset container
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        container.style.overflowY = "";
        container.style.maxHeight = "";

        domClear(container);

        // Call hero stats and profit dashboard via hooks
        if (context._hooks?.renderDealsHeroStats) context._hooks.renderDealsHeroStats();
        if (context._hooks?.renderProfitDashboard) context._hooks.renderProfitDashboard();
        if (context._hooks?.renderHistoryDeals) context._hooks.renderHistoryDeals();

        // Update tab counts and visibility of the "Очистить" buttons.
        const activeLeads = (state.leads || []).filter(isActiveLead);
        const counts = {
            watching: (state.watchlist || []).length,
            purchases: activeLeads.length,
        };

        if (elements.itemsCountBadges) {
            for (const [key, badge] of Object.entries(elements.itemsCountBadges)) {
                if (!badge) continue;
                const value = counts[key] || 0;
                badge.textContent = String(value);
                badge.hidden = value === 0;
            }
        }
        const filter = state.itemsFilter === "watching" ? "watching" : "purchases";
        for (const button of elements.itemsFilterButtons || []) {
            const isActive = button.dataset.itemsFilter === filter;
            button.classList.toggle("is-active", isActive);
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-selected", String(isActive));
        }

        if (elements.clearAllLeadsButton) {
            elements.clearAllLeadsButton.hidden = filter === "watching";
        }
        if (elements.deleteAllWatchlistButton) {
            elements.deleteAllWatchlistButton.hidden = filter !== "watching";
        }

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Раздел «Мои объявления» доступен внутри Telegram Mini App.";
            container.appendChild(note);
            return;
        }

        // Pick the right collection for the active tab. closed/skipped
        // leads are intentionally not rendered here — they're available
        // in "История сделок" below the list.
        let entries;
        if (filter === "watching") {
            entries = (state.watchlist || []).map((item) => ({
                kind: "watch",
                data: item,
                updatedAt: item.updated_at || item.last_seen_at || item.created_at || "",
            }));
            entries.sort((a, b) =>
                String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""))
            );
        } else {
            entries = activeLeads
                .map((lead) => ({
                    kind: "lead",
                    data: lead,
                    updatedAt: lead.updated_at || lead.created_at || "",
                }))
                .sort((a, b) => {
                    const statusDelta = leadSortValue(a.data) - leadSortValue(b.data);
                    if (statusDelta !== 0) return statusDelta;
                    return String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""));
                });
        }

        if (!entries.length) {
            container.appendChild(buildItemsEmpty(filter));
            return;
        }

        const watchlistMarketLabel = (val) => marketLabel(val);
        for (const entry of entries) {
            if (entry.kind === "lead") {
                container.appendChild(buildLeadNode(entry.data));
            } else {
                container.appendChild(buildWatchlistNode(entry.data, watchlistMarketLabel));
            }
        }
        });
    }

    /* ===== Watchlist ===== */

    function marketLabel(value) {
        const labels = {
            active: "На рынке",
            price_drop: "Падение цены",
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

    function watchlistSortValue(item) {
        const marketOrder = {
            price_drop: 0,
            missing: 1,
            active: 2,
        };
        const workflowOrder = {
            in_progress: 0,
            reviewing: 1,
            watching: 2,
            skipped: 3,
        };
        return `${marketOrder[item.market_status] ?? 9}:${workflowOrder[item.workflow_status] ?? 9}`;
    }

    function renderWatchlist() {
        return safeRender('renderWatchlist', () => {
        const container = elements.watchlistList;
        if (!container) return;

        // Destroy existing virtual list if present and reset container
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        container.style.overflowY = "";
        container.style.maxHeight = "";

        domClear(container);

        if (context._hooks?.renderWatchlistFilters) context._hooks.renderWatchlistFilters();
        if (context._hooks?.renderMonitoringHeroStats) context._hooks.renderMonitoringHeroStats();

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Отслеживание лотов доступно внутри Telegram Mini App.";
            container.appendChild(note);
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
            const buildEmpty = context.buildEmptyState;
            if (typeof buildEmpty === "function") {
                if (state.watchlist.length) {
                    container.appendChild(
                        buildEmpty({
                            icon: "watchlist",
                            title: "По этому фильтру ничего нет",
                            hint: "Попробуйте переключиться на «Все», чтобы увидеть весь список.",
                        })
                    );
                } else {
                    container.appendChild(
                        buildEmpty({
                            icon: "watchlist",
                            title: "Здесь будут ваши избранные лоты",
                            hint: "Нажмите «В избранное» в карточке объявления, чтобы следить за ценой и снятием с продажи.",
                        })
                    );
                }
            } else {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = state.watchlist.length
                    ? "По текущему фильтру ничего нет. Попробуйте «Все»."
                    : "Сохранённых лотов пока нет. Нажмите «В избранное» в карточке объявления.";
                container.appendChild(note);
            }
            return;
        }

        // Virtual scrolling disabled for watchlist — cards have variable heights
        // due to notes, metadata, and dynamic content
        // Re-enable only when cards have consistent fixed heights
        for (const item of filteredWatchlist) {
            container.appendChild(buildWatchlistNode(item, marketLabel));
        }
        });
    }

    return {
        buildListingNode,
        renderListingsCollection,
        renderListings,
        renderDeals,
        renderOpportunityBoard,
        renderLeads,
        renderWatchlist,
    };
}
