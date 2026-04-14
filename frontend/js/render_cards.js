/**
 * render_cards.js — buildListingNode, renderListings, renderDeals,
 * opportunity board, lead cards, watchlist cards.
 */

function createRenderCards(context) {
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

    /* ===== Listing card builder ===== */

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
                deltaMarkup = `<span class="listing-badge delta-approx">≈</span>`;
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

            container.innerHTML = "";

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
                container.appendChild(buildListingNode(item));
            }

            if (badge) {
                const apiTotal = totalOverride != null ? totalOverride : filtered.length;
                if (filtered.length < apiTotal) {
                    badge.textContent = `${filtered.length} из ${apiTotal}`;
                } else {
                    badge.textContent = String(apiTotal);
                }
            }
            return true;
        });
    }

    function renderListings() {
        return safeRender('renderListings', () => {
            if (state.loading) return;
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

            container.innerHTML = "";
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
                container.appendChild(buildListingNode(item));
            }

            if (elements.dealsTotalBadge) {
                const apiTotal = state.dealsTotal || filtered.length;
                if (filtered.length < apiTotal) {
                    elements.dealsTotalBadge.textContent = `${filtered.length} из ${apiTotal}`;
                } else {
                    elements.dealsTotalBadge.textContent = String(apiTotal);
                }
            }
            elements.dealsSection.hidden = !state.query;
        });
    }

    /* ===== Opportunity Board ===== */

    function renderOpportunityBoard() {
        return safeRender('renderOpportunityBoard', () => {
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
                if (context._hooks?.showToast) context._hooks.showToast("Загружаю...");
                void actions.openOpportunityQuery(item);
            });
            card.querySelector('[data-role="open-detail"]')?.addEventListener("click", () => {
                if (context._hooks?.showToast) context._hooks.showToast("Открываю...");
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
                    if (context._hooks?.showToast) context._hooks.showToast("Загружаю...");
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
        };
        return order[item.status] ?? 99;
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

        container.innerHTML = "";

        // Call hero stats and profit dashboard via hooks
        if (context._hooks?.renderDealsHeroStats) context._hooks.renderDealsHeroStats();
        if (context._hooks?.renderProfitDashboard) context._hooks.renderProfitDashboard();
        if (context._hooks?.renderHistoryDeals) context._hooks.renderHistoryDeals();

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Сделки доступны внутри Telegram Mini App.";
            container.appendChild(note);
            return;
        }

        const rate = state.usdRateByn || 1;
        const currencySymbol = state.currency === "USD" ? "$" : "BYN";

        const filteredLeads = [...state.leads]
            .filter((l) => l.status !== "closed")
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
            container.appendChild(note);
            return;
        }

        // Build lead card element — extracted for virtual scrolling
        function buildLeadNode(lead) {
            const isSold = lead.status === "sold";
            const isMissing = lead.market_status === "missing";
            const card = document.createElement("article");
            card.className = `lead-card status-${lead.status}`;
            card.dataset.leadId = lead.id;

            const priceBynRaw = lead.price_byn ? Number(lead.price_byn) : null;
            const buyPriceBynRaw = lead.buy_price_byn ? Number(lead.buy_price_byn) : null;
            const soldPriceBynRaw = lead.sold_price_byn ? Number(lead.sold_price_byn) : null;

            const priceByn = priceBynRaw ? (state.currency === "USD" ? Math.round(priceBynRaw / rate) : Math.round(priceBynRaw)) : null;
            const buyPrice = buyPriceBynRaw ? (state.currency === "USD" ? Math.round(buyPriceBynRaw / rate) : Math.round(buyPriceBynRaw)) : null;
            const soldPrice = soldPriceBynRaw ? (state.currency === "USD" ? Math.round(soldPriceBynRaw / rate) : Math.round(soldPriceBynRaw)) : null;

            const thumbMarkup = lead.thumbnail
                ? `<img class="watchlist-thumb" src="${escapeHtml(lead.thumbnail)}" alt="" loading="lazy">`
                : `<div class="watchlist-thumb-placeholder">Нет фото</div>`;

            const missingBanner = isMissing
                ? `<div class="watchlist-missing-banner">Объявление снято с продажи</div>`
                : "";
            const missingBadge = isMissing
                ? `<span class="market-badge missing">Пропало</span>`
                : "";

            let profitMarkup = "";
            const hasBothPrices = buyPriceBynRaw && soldPriceBynRaw;

            if (hasBothPrices) {
                const profitRaw = soldPriceBynRaw - buyPriceBynRaw;
                const profit = state.currency === "USD" ? profitRaw / rate : profitRaw;
                const profitPercent = buyPriceBynRaw > 0 ? ((profitRaw / buyPriceBynRaw) * 100).toFixed(0) : "0";
                const profitSign = profit >= 0 ? "+" : "";
                const profitClass = profit >= 0 ? "profit-positive" : "profit-negative";

                if (isSold) {
                    profitMarkup = `<div class="lead-financial-item ${profitClass}">Прибыль: <span class="mono">${profitSign}${Math.round(profit)} ${currencySymbol} (${profitSign}${profitPercent}%)</span></div>`;
                } else if (lead.status === 'new' || lead.status === 'bought') {
                    profitMarkup = `<div class="lead-financial-item ${profitClass}">Потенциальная прибыль: <span class="mono">${profitSign}${Math.round(profit)} ${currencySymbol} (${profitSign}${profitPercent}%)</span></div>`;
                }
            }

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
                        <span class="lead-field-label">Купил за</span>
                        <div class="lead-field-wrap">
                            <input data-role="buy-price" type="text" min="0" placeholder="цена покупки">
                            <button class="lead-field-chip" data-role="fill-buy-price" type="button" ${!priceByn ? 'disabled style="opacity:0.4;pointer-events:none;"' : ''}>${priceByn ? `${priceByn}` : 'Договорная'}</button>
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

            const buyPriceInput = card.querySelector('[data-role="buy-price"]');
            if (buyPriceInput) {
                buyPriceInput.value = lead.buy_price_byn ?? "";
                buyPriceInput.addEventListener("input", (e) => {
                    let val = e.target.value.replace(/[^\d]/g, "");
                    if (val !== e.target.value) {
                        e.target.value = val;
                    }
                });
            }

            card.querySelector('[data-role="fill-buy-price"]')?.addEventListener("click", () => {
                if (buyPriceInput && priceByn) {
                    buyPriceInput.value = String(priceByn);
                    buyPriceInput.focus();
                }
            });

            const soldPriceInput = card.querySelector('[data-role="sold-price"]');
            if (soldPriceInput) {
                soldPriceInput.value = lead.sold_price_byn ?? "";
                soldPriceInput.addEventListener("input", (e) => {
                    let val = e.target.value.replace(/[^\d]/g, "");
                    if (val !== e.target.value) {
                        e.target.value = val;
                    }
                });
            }
            return card;
        }

        // Virtual scrolling disabled — cards have variable heights that break with fixed-height virtualization
        for (const lead of filteredLeads) {
            container.appendChild(buildLeadNode(lead));
        }
        });
    }

    /* ===== Watchlist ===== */

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

        container.innerHTML = "";

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
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = state.watchlist.length
                ? "По текущему фильтру ничего нет. Попробуйте «Все»."
                : "Сохранённых лотов пока нет. Нажмите «В избранное» в карточке объявления.";
            container.appendChild(note);
            return;
        }

        const rate = state.usdRateByn || 1;
        const currencySymbol = state.currency === "USD" ? "$" : "BYN";

        // Build watchlist card element — extracted for virtual scrolling
        function buildWatchlistNode(item) {
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
                            <span class="watchlist-card-price mono">${currentPriceDisplay ? `${currentPriceDisplay} ${currencySymbol}` : "Договорная"}</span>
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
                    if (context._hooks?.showToast) context._hooks.showToast("Важность обновлена");
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
                    if (context._hooks?.showToast) context._hooks.showToast("Заметка сохранена");
                    void actions.updateWatchlistMeta(item.id, {
                        notes: notesInput.value.trim() || null,
                    });
                });
            }
            return card;
        }

        // Virtual scrolling disabled for watchlist — cards have variable heights
        // due to notes, metadata, and dynamic content
        // Re-enable only when cards have consistent fixed heights
        for (const item of filteredWatchlist) {
            container.appendChild(buildWatchlistNode(item));
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
