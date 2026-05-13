/**
 * render_cards.js — buildListingNode, renderListings,
 * lead cards, watchlist cards.
 */

function createRenderCards(context) {
    const {
        state,
        elements,
        actions,
        hasTelegramInitData,
        safeRender: safeRender,
        // OPUS-13 wave 73: ``createVirtualList`` is injected via
        // context because after the split this file runs as a lazy
        // <script> that doesn't share scope with the bundle IIFE.
        // ``createRenderCardBuilders`` ships in the same lazy chunk,
        // so global-scope resolution still works for it.
        createVirtualList,
    } = context;
    const builders = createRenderCardBuilders(context);
    const {
        buildListingNode,
        buildLeadNode,
        buildWatchlistNode,
    } = builders;

    /* ===== Shared helpers (from original file) ===== */

    function _resetContainer(container) {
        if (!container) return;
        if (container._abortController) {
            container._abortController.abort();
            container._abortController = null;
        }
        container.style.overflowY = "";
        container.style.maxHeight = "";
        domClear(container);
    }

    function _getSignal(container) {
        if (!container._abortController) {
            container._abortController = new AbortController();
        }
        return container._abortController.signal;
    }

    function _buildSkeletonCard() {
        const skel = domEl("div", {
            className: "skeleton-card",
            attrs: { "aria-hidden": "true" },
        });
        skel.appendChild(domEl("div", { className: "skel-bar", attrs: { style: "width:60%" } }));
        skel.appendChild(domEl("div", { className: "skel-bar", attrs: { style: "width:40%" } }));
        skel.appendChild(domEl("div", { className: "skel-bar", attrs: { style: "width:30%" } }));
        return skel;
    }

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
        const itemPrice = item.price != null ? Number(item.price) : null;
        const hasPriceRange = state.filters.minPrice != null || state.filters.maxPrice != null;
        // Exclude "Договорная" (price null) when a price range is set
        if (hasPriceRange && itemPrice == null) return false;
        if (state.filters.minPrice != null && itemPrice != null && itemPrice < state.filters.minPrice) return false;
        if (state.filters.maxPrice != null && itemPrice != null && itemPrice > state.filters.maxPrice) return false;

        // Condition filter - handle various formats from API
        if (state.filters.condition) {
            const itemCondition = item.condition || "";
            // Normalize condition values for comparison
            const normalizedItemCondition = itemCondition.toLowerCase();
            const normalizedStateCondition = state.filters.condition.toLowerCase();
            
            // Map state condition to possible API values
            const conditionMap = {
                "new": ["new", "новый", "2"],
                "used": ["used", "б/у", "1"],
            };
            
            const validValues = conditionMap[normalizedStateCondition] || [normalizedStateCondition];
            if (!validValues.includes(normalizedItemCondition)) return false;
        }
        
        // Seller type filter
        if (state.filters.sellerType) {
            const isShop = item.company_ad || item.seller_type === "Магазин" || item.seller_type === "shop" || item.seller_type?.toLowerCase() === "shop";
            if (state.filters.sellerType === "private" && isShop) return false;
            if (state.filters.sellerType === "shop" && !isShop) return false;
        }
        
        // Region filter — matches region_name or area_name exactly
        if (state.filters.regionName) {
            const filterRegion = state.filters.regionName.toLowerCase().trim();
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
        if (!state.filters.condition && !state.filters.sellerType && state.filters.minPrice == null && state.filters.maxPrice == null && !state.filters.regionName) return items;
        return items.filter(matchesFilters);
    }

    /* ===== Collections ===== */

    function _delegateListingClick(container) {
        if (container._listingDelegated) return;
        container._listingDelegated = true;
        container.addEventListener("click", (event) => {
            const card = event.target.closest(".listing");
            if (!card || !card._item) return;
            const item = card._item;
            const actionBtn = event.target.closest('[data-role="lead"], [data-role="watch"]');
            if (actionBtn) {
                event.stopPropagation();
                if (actionBtn.dataset.role === "lead") {
                    void actions.addLeadFromListing(item);
                } else {
                    void actions.addWatchlistFromListing(item);
                }
                return;
            }
            if (event.target.closest(".listing-top")) {
                void actions.openListingDetail(item);
            }
        });
        container.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            const card = event.target.closest(".listing");
            if (!card || !card._item) return;
            if (event.target !== card) return;
            event.preventDefault();
            void actions.openListingDetail(card._item);
        });
    }

    /**
     * Renders a collection of items into a container.
     *
     * @param {Array} items - The data items to render
     * @param {HTMLElement} container - The DOM container to render into
     * @param {HTMLElement} badge - Optional badge element for item count
     * @param {string} emptyText - Text to show when no items
     * @param {number|null} totalOverride - Override for total count display
     * @returns {boolean} True if content was rendered, false if empty
     */
    function renderListingsCollection(items, container, badge, emptyText, totalOverride = null) {
        return safeRender('renderListingsCollection', () => {
            if (!container) return false;
            _delegateListingClick(container);
            _resetContainer(container);

            const filtered = applyFilters(items);
            if (!filtered.length) {
                const buildEmpty = context.buildEmptyState;
                if (typeof buildEmpty === "function") {
                    container.appendChild(
                        buildEmpty({
                            title: "Ничего не найдено",
                            hint: emptyText || "Попробуйте изменить запрос или снять фильтры.",
                        })
                    );
                } else {
                    const note = document.createElement("p");
                    note.className = "tracker-empty";
                    note.textContent = emptyText;
                    container.appendChild(note);
                }
                if (badge) {
                    badge.textContent = "0";
                }
                return false;
            }

            const fragment = document.createDocumentFragment();
            for (const item of filtered) {
                fragment.appendChild(buildListingNode(item, verdictClassName));
            }
            container.appendChild(fragment);

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

    /**
     * Append a pagination sentinel to the bottom of a list container.
     *
     * Two roles: the IntersectionObserver-watched element that
     * triggers ``onLoadMore`` when scrolled into view, AND the
     * visible "Загрузить ещё / Показано N из M" UI so users can
     * tap to fetch the next page if scroll-driven loading misses.
     */
    function _appendPaginationSentinel(container, options) {
        if (!container) return;
        // Disconnect any previous observer on this container to prevent
        // orphaned IntersectionObserver callbacks from stale renders.
        const oldSentinels = container.querySelectorAll(".list-pagination-sentinel");
        for (const old of oldSentinels) {
            if (old._paginationObserver) {
                old._paginationObserver.disconnect();
            }
        }
        const {
            renderedCount,
            totalCount,
            hasMore,
            isLoadingMore,
            onLoadMore,
            allLoadedText = "Все объявления загружены.",
        } = options;
        const sentinel = document.createElement("div");
        sentinel.className = "list-pagination-sentinel";
        if (hasMore) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "list-pagination-button";
            button.disabled = Boolean(isLoadingMore);
            button.textContent = isLoadingMore
                ? "Загружаю..."
                : `Загрузить ещё (${renderedCount} из ${totalCount || "—"})`;
            button.addEventListener("click", () => {
                if (typeof onLoadMore === "function") {
                    onLoadMore();
                }
            });
            sentinel.appendChild(button);
            // Auto-trigger on visibility — once the user scrolls
            // close enough to the sentinel, kick off the next page
            // without waiting for a tap. IntersectionObserver isn't
            // supported in really old WebViews; the button stays as
            // a manual fallback.
            if (typeof IntersectionObserver !== "undefined") {
                const observer = new IntersectionObserver(
                    (entries) => {
                        for (const entry of entries) {
                            if (entry.isIntersecting && typeof onLoadMore === "function") {
                                observer.disconnect();
                                onLoadMore();
                                break;
                            }
                        }
                    },
                    // Trigger the next page well before the user
                    // hits the bottom — 1200px ≈ 6-7 cards of
                    // headroom on phone screens, so by the time the
                    // sentinel actually scrolls into view the next
                    // batch is usually already rendered.
                    { rootMargin: "1200px 0px" },
                );
                observer.observe(sentinel);
                sentinel._paginationObserver = observer;
            }
        } else if (totalCount && renderedCount > 0) {
            const note = document.createElement("p");
            note.className = "list-pagination-note";
            note.textContent = allLoadedText;
            sentinel.appendChild(note);
        }
        if (sentinel.childNodes.length > 0) {
            container.appendChild(sentinel);
        }
    }

    function renderListings() {
        return safeRender('renderListings', () => {
            if (state.ui.loading) return;
            // Keep skeletons while listings request is in flight
            if (state.listings._pending) return;

            // Show skeleton cards while listings are loading (e.g. sort change)
            if (state.listings.loading && !state.listings.items.length) {
                if (elements.listingsList) {
                    _resetContainer(elements.listingsList);
                    for (let i = 0; i < 3; i++) {
                        elements.listingsList.appendChild(_buildSkeletonCard());
                    }
                    elements.listingsSection.hidden = false;
                }
                if (elements.listingsTotalBadge) {
                    elements.listingsTotalBadge.textContent = "";
                }
                return;
            }

            const hasData = state.listings.items.length > 0 || state.listings.total > 0;
            const hasContent = renderListingsCollection(
                state.listings.items,
                elements.listingsList,
                elements.listingsTotalBadge,
                "По этому запросу пока нечего показать.",
                state.listings.total || null
            );
            if (elements.listingsFallbackBadge) {
                elements.listingsFallbackBadge.hidden = !state.listings.fallbackUsed;
            }
            if (hasContent && elements.listingsList) {
                _appendPaginationSentinel(elements.listingsList, {
                    renderedCount: state.listings.items.length,
                    totalCount: state.listings.total,
                    hasMore: state.listings.hasMore,
                    isLoadingMore: state.listings.loadingMore,
                    onLoadMore: () => {
                        if (typeof actions.loadMoreListings === "function") {
                            void actions.loadMoreListings();
                        }
                    },
                });
            }
            // Keep section visible if data exists but was filtered out —
            // the empty message inside the container tells the user why.
            elements.listingsSection.hidden = !hasContent && !hasData;
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
                title: "Здесь будут отслеживаемые лоты",
                hint: "Сохрани объявление через «В избранное», и сюда придут уведомления о смене цены и снятии с продажи.",
            });
        }
        return buildEmpty({
            title: "Нет сделок в работе",
            hint: "Найди лот через поиск и нажми «В покупки», чтобы вести его до продажи и считать прибыль.",
        });
    }

    function renderLeads() {
        return safeRender('renderLeads', () => {
        const container = elements.leadInboxList;
        if (!container) return;

        // Destroy existing virtual list before reset
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        _resetContainer(container);

        // Call hero stats and profit dashboard via hooks
        if (context._hooks?.renderDealsHeroStats) context._hooks.renderDealsHeroStats();
        if (context._hooks?.renderProfitDashboard) context._hooks.renderProfitDashboard();
        if (context._hooks?.renderHistoryDeals) context._hooks.renderHistoryDeals();

        // Update tab counts and visibility of the "Очистить" buttons.
        const activeLeads = (state.leads.items || []).filter(isActiveLead);
        const counts = {
            watching: (state.watchlist.items || []).length,
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
        const filter = state.leads.itemsFilter === "watching" ? "watching" : "purchases";
        for (const button of elements.itemsFilterButtons || []) {
            const isActive = button.dataset.itemsFilter === filter;
            button.classList.toggle("is-active", isActive);
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-selected", String(isActive));
            button.tabIndex = isActive ? 0 : -1;
        }
        container.setAttribute("aria-labelledby", `items-tab-${filter}`);

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
            entries = (state.watchlist.items || []).map((item) => ({
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

        if (filter === "watching" && state.watchlist._loading && !entries.length) {
            for (let i = 0; i < 3; i++) container.appendChild(_buildSkeletonCard());
            return;
        }

        if (!entries.length) {
            container.appendChild(buildItemsEmpty(filter));
            return;
        }

        const watchlistMarketLabel = (val) => marketLabel(val);
        const WATCHLIST_ITEM_HEIGHT = 220;
        const VIRTUAL_LIST_THRESHOLD = 30;

        if (filter === "watching" && entries.length > VIRTUAL_LIST_THRESHOLD) {
            container._virtualList = createVirtualList(container, {
                itemHeight: WATCHLIST_ITEM_HEIGHT,
                fixedHeight: WATCHLIST_ITEM_HEIGHT,
                bufferSize: 5,
                renderFn: (entry, index) => buildWatchlistNode(entry.data, watchlistMarketLabel),
            });
            container._virtualList.setItems(entries);
        } else {
            const signal = _getSignal(container);
            for (const entry of entries) {
                if (entry.kind === "lead") {
                    container.appendChild(buildLeadNode(entry.data, signal));
                } else {
                    container.appendChild(buildWatchlistNode(entry.data, watchlistMarketLabel, signal));
                }
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
        if (state.watchlist.filter === "all") {
            return true;
        }
        return item.workflow_status === state.watchlist.filter;
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

        // Destroy existing virtual list before reset
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        _resetContainer(container);

        if (context._hooks?.renderWatchlistFilters) context._hooks.renderWatchlistFilters();

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Отслеживание лотов доступно внутри Telegram Mini App.";
            container.appendChild(note);
            return;
        }

        // Show skeleton cards while loading
        if (state.watchlist._loading) {
                for (let i = 0; i < 3; i++) container.appendChild(_buildSkeletonCard());
                return;
            }

        const filteredWatchlist = [...state.watchlist.items]
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
                if (state.watchlist.items.length) {
                    container.appendChild(
                        buildEmpty({
                            title: "По этому фильтру ничего нет",
                            hint: "Попробуйте переключиться на «Все», чтобы увидеть весь список.",
                        })
                    );
                } else {
                    container.appendChild(
                        buildEmpty({
                            title: "Здесь будут ваши избранные лоты",
                            hint: "Нажмите «В избранное» в карточке объявления, чтобы следить за ценой и снятием с продажи.",
                            actionLabel: "Найти объявления",
                            onAction: () => {
                                if (typeof context.setActiveView === "function") {
                                    context.setActiveView("overview");
                                }
                                const searchInput = document.querySelector(".search-input");
                                if (searchInput) searchInput.focus();
                            },
                        })
                    );
                }
            } else {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = state.watchlist.items.length
                    ? "По текущему фильтру ничего нет. Попробуйте «Все»."
                    : "Сохранённых лотов пока нет. Нажмите «В избранное» в карточке объявления.";
                container.appendChild(note);
            }
            return;
        }

        // Re-enable virtual scrolling for watchlist — cards have consistent layout
        const WATCHLIST_ITEM_HEIGHT = 220;
        const VIRTUAL_LIST_THRESHOLD = 30;
        if (filteredWatchlist.length > VIRTUAL_LIST_THRESHOLD) {
            container._virtualList = createVirtualList(container, {
                itemHeight: WATCHLIST_ITEM_HEIGHT,
                fixedHeight: WATCHLIST_ITEM_HEIGHT,
                bufferSize: 5,
                renderFn: (item, index) => buildWatchlistNode(item, marketLabel),
            });
            container._virtualList.setItems(filteredWatchlist);
        } else {
            const signal = _getSignal(container);
            for (const item of filteredWatchlist) {
                container.appendChild(buildWatchlistNode(item, marketLabel, signal));
            }
        }
        });
    }

    return {
        buildListingNode,
        renderListingsCollection,
        renderListings,
        renderLeads,
        renderWatchlist,
    };
}

// OPUS-13 wave 73: lazy-load registration; stub in the bundle
// proxies into window.App._realCreateRenderCards once this script
// (and its dependencies render_card_builders + virtual_list) have
// loaded together.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateRenderCards = createRenderCards;
}
