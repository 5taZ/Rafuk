function createRenderCardBuilders(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        hasTelegramInitData,
        safeUrl: safeUrl,
    } = context;

    function buildMediaNode(imageClass, placeholderClass, placeholderText, url) {
        if (url) {
            return domEl("img", {
                className: imageClass,
                attrs: { src: safeUrl(url), alt: "", loading: "lazy" },
            });
        }
        return domEl("div", { className: placeholderClass, text: placeholderText });
    }

    function buildListingNode(item, verdictClassName) {
        const listing = domEl("article", { className: "listing" });
        const resolveVerdictClassName =
            typeof verdictClassName === "function" ? verdictClassName : function () { return "neutral"; };

        const badges = [];
        if (item.list_time) {
            const hours = (Date.now() - new Date(item.list_time).getTime()) / 3600000;
            if (hours <= 3) {
                badges.push(domEl("span", { className: "listing-badge fresh-hot", text: "Новое" }));
            } else if (hours <= 24) {
                badges.push(domEl("span", { className: "listing-badge fresh-warm", text: "Сегодня" }));
            }
        }
        if (item.deal_verdict) {
            badges.push(domEl("span", {
                className: `listing-badge verdict-${resolveVerdictClassName(item.deal_verdict)}`,
                text: item.deal_verdict,
            }));
        }

        let delta = item.price_vs_median;
        if (delta == null && item.price && state.stats?.median && Number(state.stats.median) > 0) {
            delta = Math.round(((Number(item.price) - Number(state.stats.median)) / Number(state.stats.median)) * 100 * 100) / 100;
        }
        if (delta != null) {
            const absDelta = Math.abs(delta);
            if (absDelta < 0.5) {
                badges.push(domEl("span", { className: "listing-badge delta-approx", text: "≈" }));
            } else {
                badges.push(domEl("span", {
                    className: `listing-badge ${deltaClass(delta)}`.trim(),
                    text: formatDelta(delta),
                }));
            }
        }

        const tags = domEl("div", { className: "listing-tags" });
        if (item.condition) tags.appendChild(domEl("span", { className: "tag", text: formatCondition(item.condition) }));
        if (item.seller_type) tags.appendChild(domEl("span", { className: "tag", text: formatSeller(item.seller_type) }));

        listing.appendChild(
            domFragment(
                domEl(
                    "div",
                    { className: "listing-top" },
                    buildMediaNode("listing-thumb", "listing-thumb placeholder", "Нет фото", item.thumbnail),
                    domEl(
                        "div",
                        { className: "listing-body" },
                        domEl("span", { className: "listing-name", text: item.title }),
                        tags,
                        domEl("span", { className: "listing-price mono", text: formatPrice(item.price) }),
                    ),
                ),
                badges.length ? domEl("div", { className: "listing-badges" }, badges) : null,
                domEl(
                    "div",
                    { className: "listing-actions" },
                    domEl("button", { className: "listing-btn", type: "button", text: "Подробнее" }),
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "lead" }, text: "В покупки" }),
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "watch" }, text: "В избранное" }),
                    domEl("a", {
                        className: "listing-btn listing-btn--accent",
                        text: "Kufar",
                        attrs: { href: safeUrl(item.link), target: "_blank", rel: "noreferrer noopener" },
                    }),
                ),
            )
        );

        listing.querySelector("button")?.addEventListener("click", () => {
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

    function buildOpportunityCard(item, extraBadge, verdictClassName) {
        const reasons = (item.listing.deal_reasons || []).join(" · ");
        const badge = item.signal_label || extraBadge;
        const meta = domEl(
            "div",
            { className: "opportunity-meta" },
            domEl("span", { className: "mono", text: formatPrice(item.listing.price) }),
            domEl("span", { text: item.listing.region_name || "Без региона" }),
        );
        if (badge) meta.appendChild(domEl("span", { className: "board-inline-flag", text: badge }));

        const card = domEl(
            "article",
            { className: "opportunity-card" },
            domEl(
                "div",
                { className: "opportunity-top" },
                domEl("span", { className: "badge", text: item.saved_search_name }),
                domEl("span", {
                    className: `listing-signal verdict verdict-${verdictClassName(item.listing.deal_verdict || "Смотреть")}`,
                    text: `${item.listing.deal_verdict || "Смотреть"} · ${Math.round(item.listing.deal_score || 0)}`,
                }),
            ),
            domEl("strong", { className: "opportunity-title", text: item.listing.title }),
            meta,
            reasons ? domEl("p", { className: "opportunity-copy", text: reasons }) : null,
            domEl(
                "div",
                { className: "listing-actions" },
                domEl("button", { className: "ghost-btn small", type: "button", dataset: { role: "open-query" }, text: "Открыть запрос" }),
                domEl("button", { className: "ghost-btn small", type: "button", dataset: { role: "open-detail" }, text: "Подробнее" }),
                domEl("button", { className: "ghost-btn small", type: "button", dataset: { role: "lead" }, text: "В покупки" }),
                domEl("button", { className: "ghost-btn small", type: "button", dataset: { role: "watch" }, text: "В избранное" }),
                domEl("a", {
                    className: "primary-link small",
                    text: "Kufar",
                    attrs: { href: safeUrl(item.listing.link), target: "_blank", rel: "noreferrer noopener" },
                }),
            ),
        );
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

    function buildSignalRow(signal, clickable) {
        const row = domEl(
            "div",
            { className: "tracker-row signal-row" },
            domEl(
                "div",
                { className: "tracker-row-main" },
                domEl("strong", { className: "tracker-query", text: signal.title }),
                domEl("span", { className: "tracker-meta mono", text: `${signal.subtitle} • ${signal.metric}` }),
            ),
            clickable
                ? domEl(
                    "div",
                    { className: "tracker-row-actions" },
                    domEl("button", { className: "ghost-btn small", type: "button", text: "Открыть" }),
                )
                : null,
        );
        if (clickable) {
            row.querySelector("button")?.addEventListener("click", () => {
                if (context._hooks?.showToast) context._hooks.showToast("Загружаю...");
                elements.searchInput.value = signal.query;
                state.query = signal.query;
                void actions.search("overview");
            });
        }
        return row;
    }

    function buildLeadNode(lead) {
        const isSold = lead.status === "sold";
        const isMissing = lead.market_status === "missing";
        const card = domEl("article", {
            className: `lead-card status-${lead.status}`,
            attrs: { "data-lead-id": lead.id },
        });

        const priceBynRaw = lead.price_byn ? Number(lead.price_byn) : null;
        const buyPriceBynRaw = lead.buy_price_byn ? Number(lead.buy_price_byn) : null;
        const soldPriceBynRaw = lead.sold_price_byn ? Number(lead.sold_price_byn) : null;
        const priceByn = priceBynRaw ? Math.round(priceBynRaw) : null;

        let profitNode = null;
        if (buyPriceBynRaw && soldPriceBynRaw) {
            const profitRaw = soldPriceBynRaw - buyPriceBynRaw;
            const profitPercent = buyPriceBynRaw > 0 ? ((profitRaw / buyPriceBynRaw) * 100).toFixed(0) : "0";
            const profitSign = profitRaw >= 0 ? "+" : "";
            const profitClass = profitRaw >= 0 ? "profit-positive" : "profit-negative";
            if (isSold) {
                profitNode = domEl("div", {
                    className: `lead-financial-item ${profitClass}`,
                    text: `Результат: ${profitSign}${Math.round(profitRaw)} BYN (${profitSign}${profitPercent}%)`,
                });
            } else if (lead.status === "new" || lead.status === "bought") {
                profitNode = domEl("div", {
                    className: `lead-financial-item ${profitClass}`,
                    text: `Потенциал: ${profitSign}${Math.round(profitRaw)} BYN (${profitSign}${profitPercent}%)`,
                });
            }
        }

        const buyPriceInput = domEl("input", {
            type: "text",
            attrs: { min: "0", placeholder: "цена покупки" },
            dataset: { role: "buy-price" },
        });
        buyPriceInput.value = lead.buy_price_byn ?? "";
        buyPriceInput.addEventListener("input", (e) => {
            const nextValue = e.target.value.replace(/[^\d]/g, "");
            if (nextValue !== e.target.value) e.target.value = nextValue;
        });

        const soldPriceInput = domEl("input", {
            type: "text",
            attrs: { min: "0", placeholder: "цена продажи" },
            dataset: { role: "sold-price" },
        });
        soldPriceInput.value = lead.sold_price_byn ?? "";
        soldPriceInput.addEventListener("input", (e) => {
            const nextValue = e.target.value.replace(/[^\d]/g, "");
            if (nextValue !== e.target.value) e.target.value = nextValue;
        });

        const fillBuyPriceButton = domEl("button", {
            className: "lead-field-chip",
            type: "button",
            dataset: { role: "fill-buy-price" },
            text: priceByn ? `${priceByn}` : "Договорная",
            attrs: !priceByn ? { disabled: true, style: "opacity:0.4;pointer-events:none;" } : {},
        });
        fillBuyPriceButton.addEventListener("click", () => {
            if (priceByn) {
                buyPriceInput.value = String(priceByn);
                buyPriceInput.focus();
            }
        });

        card.appendChild(
            domFragment(
                isMissing ? domEl("div", { className: "watchlist-missing-banner", text: "Объявление снято с продажи" }) : null,
                domEl(
                    "div",
                    { className: `lead-card-top${isMissing ? " is-missing" : ""}` },
                    buildMediaNode("watchlist-thumb", "watchlist-thumb-placeholder", "Нет фото", lead.thumbnail),
                    domEl(
                        "div",
                        { className: "lead-card-body" },
                        domEl(
                            "div",
                            { className: "lead-card-title-row" },
                            domEl("strong", { className: "lead-card-title", text: lead.title }),
                        ),
                        domEl("span", { className: "lead-card-price mono", text: priceByn ? `${priceByn} BYN` : "без цены" }),
                        isMissing ? domEl("span", { className: "market-badge missing", text: "Пропало" }) : null,
                        profitNode,
                    ),
                ),
                domEl(
                    "div",
                    { className: "lead-card-fields" },
                    domEl(
                        "label",
                        { className: "lead-field" },
                        domEl("span", { className: "lead-field-label", text: "Купил за" }),
                        domEl(
                            "div",
                            { className: "lead-field-wrap" },
                            buyPriceInput,
                            fillBuyPriceButton,
                            domEl("span", { className: "unit", text: "BYN" }),
                        ),
                    ),
                    domEl(
                        "label",
                        { className: "lead-field" },
                        domEl("span", { className: "lead-field-label", text: "Продал за" }),
                        domEl(
                            "div",
                            { className: "lead-field-wrap" },
                            soldPriceInput,
                            domEl("span", { className: "unit", text: "BYN" }),
                        ),
                    ),
                ),
                domEl(
                    "div",
                    { className: "lead-card-actions" },
                    !isSold
                        ? domEl(
                            "div",
                            { className: "lead-btn-row" },
                            domEl("a", {
                                className: "lead-btn lead-btn--kufar",
                                text: "Kufar ↗",
                                attrs: { href: safeUrl(lead.link), target: "_blank", rel: "noreferrer noopener" },
                            }),
                        )
                        : null,
                    !isSold
                        ? domEl(
                            "div",
                            { className: "lead-btn-row" },
                            domEl("button", { className: "lead-btn lead-btn--confirm", type: "button", dataset: { role: "confirm" }, text: "✓" }),
                            domEl("button", { className: "lead-btn lead-btn--delete", type: "button", dataset: { role: "cancel" }, text: "✕" }),
                        )
                        : null,
                    isSold
                        ? domEl(
                            "div",
                            { className: "lead-btn-row" },
                            domEl("button", { className: "lead-btn lead-btn--success", type: "button", dataset: { role: "close-deal" }, text: "✓ Готово" }),
                            domEl("button", { className: "lead-btn lead-btn--revert", type: "button", dataset: { role: "revert" }, text: "↩ Назад" }),
                        )
                        : null,
                ),
            )
        );

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
        return card;
    }

    function buildWatchlistNode(item, marketLabel) {
        const card = domEl("article", { className: "watchlist-card" });

        const currentPriceByn = item.current_price_byn || item.initial_price_byn;
        const hasValidPrice = currentPriceByn && Number(currentPriceByn) > 0;
        const currentPriceDisplay = hasValidPrice ? Math.round(currentPriceByn) : null;
        const isMissing = item.market_status === "missing";
        const isMarketSignal = item.market_status && ["price_drop", "missing"].includes(item.market_status);

        let deltaNode = null;
        if (item.price_delta_byn != null && Math.abs(item.price_delta_byn) > 0.5) {
            const deltaNum = Math.round(item.price_delta_byn);
            const deltaClassName = deltaNum < 0 ? "down" : deltaNum > 0 ? "up" : "neutral";
            const deltaSign = deltaNum > 0 ? "+" : "";
            const arrow = deltaNum < 0 ? "📉" : deltaNum > 0 ? "📈" : "≈";
            const percentText = item.price_delta_percent != null ? ` (${deltaSign}${item.price_delta_percent}%)` : "";
            deltaNode = domEl("span", {
                className: `watchlist-price-delta ${deltaClassName}`,
                text: `${arrow} ${deltaSign}${deltaNum} BYN${percentText}`,
            });
        }

        let potentialProfitNode = null;
        if (item.market_median_byn && currentPriceByn && Number(item.market_median_byn) > 0) {
            const profitByn = Number(item.market_median_byn) - Number(currentPriceByn);
            const profitPercent = currentPriceByn > 0 ? Math.round((profitByn / Number(currentPriceByn)) * 100) : 0;
            const profitClass = profitByn >= 0 ? "profit-positive" : "profit-negative";
            const profitSign = profitByn >= 0 ? "+" : "";
            potentialProfitNode = domEl("span", {
                className: `watchlist-profit ${profitClass}`,
                text: `Потенциал: ${profitSign}${Math.round(profitByn)} BYN (${profitSign}${profitPercent}%)`,
            });
        }

        let marketBadgeNode = null;
        if (isMarketSignal) {
            let missingAgeText = "";
            if (item.market_status === "missing" && item.missing_since_at) {
                const missingDate = new Date(item.missing_since_at);
                const diffMs = Date.now() - missingDate.getTime();
                const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
                if (diffDays >= 1) missingAgeText = ` (${diffDays}д)`;
            }
            marketBadgeNode = domEl("span", {
                className: `market-badge ${item.market_status}`,
                text: `${marketLabel(item.market_status)}${missingAgeText}`,
            });
        }

        const statusSelect = domEl(
            "select",
            { className: "wl-status-select", dataset: { role: "status" } },
            domEl("option", { value: "default", text: "Обычное" }),
            domEl("option", { value: "important", text: "Важное" }),
            domEl("option", { value: "very_important", text: "Очень важное" }),
        );
        statusSelect.value = item.workflow_status || "default";
        statusSelect.addEventListener("change", () => {
            if (context._hooks?.showToast) context._hooks.showToast("Важность обновлена");
            void actions.updateWatchlistStatus(item.id, statusSelect.value);
        });

        const notesInput = domEl("input", {
            type: "text",
            attrs: { placeholder: "заметка к лоту…" },
            dataset: { role: "notes" },
        });
        notesInput.value = item.notes || "";
        notesInput.addEventListener("change", () => {
            if (context._hooks?.showToast) context._hooks.showToast("Заметка сохранена");
            void actions.updateWatchlistMeta(item.id, {
                notes: notesInput.value.trim() || null,
            });
        });

        card.appendChild(
            domFragment(
                isMissing
                    ? domEl("div", {
                        className: "watchlist-missing-banner",
                        text: "Объявление снято с продажи. Будет удалено автоматически через несколько дней.",
                    })
                    : null,
                domEl(
                    "div",
                    { className: `watchlist-card-top${isMissing ? " is-missing" : ""}` },
                    buildMediaNode("watchlist-thumb", "watchlist-thumb-placeholder", "Нет фото", item.thumbnail),
                    domEl(
                        "div",
                        { className: "watchlist-card-body" },
                        domEl("strong", { className: "watchlist-card-title", text: item.title }),
                        domEl(
                            "div",
                            { className: "watchlist-card-price-row" },
                            domEl("span", { className: "watchlist-card-price mono", text: currentPriceDisplay ? `${currentPriceDisplay} BYN` : "Договорная" }),
                            deltaNode,
                        ),
                        potentialProfitNode,
                        domEl("div", { className: "watchlist-card-meta" }, marketBadgeNode),
                    ),
                ),
                domEl(
                    "div",
                    { className: "watchlist-card-fields" },
                    domEl(
                        "label",
                        { className: "wl-field" },
                        domEl("span", { className: "wl-field-label", text: "Важность" }),
                        statusSelect,
                    ),
                    domEl(
                        "label",
                        { className: "wl-field wl-field-wide" },
                        domEl("span", { className: "wl-field-label", text: "Заметка" }),
                        domEl("div", { className: "wl-field-input-wrap" }, notesInput),
                    ),
                ),
                domEl(
                    "div",
                    { className: "watchlist-card-actions" },
                    domEl("button", { className: "wl-btn wl-btn--detail", type: "button", dataset: { role: "detail" }, text: "Подробнее" }),
                    !isMissing ? domEl("button", { className: "wl-btn wl-btn--accent", type: "button", dataset: { role: "lead" }, text: "В покупки" }) : null,
                    !isMissing ? domEl("a", {
                        className: "wl-btn",
                        text: "Kufar ↗",
                        attrs: { href: safeUrl(item.link), target: "_blank", rel: "noreferrer noopener" },
                    }) : null,
                    domEl("button", { className: "wl-btn wl-btn--danger", type: "button", dataset: { role: "delete" }, text: "Удалить" }),
                ),
            )
        );

        card.querySelector('[data-role="detail"]')?.addEventListener("click", () => {
            void actions.openWatchlistDetail(item);
        });
        card.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
            void actions.promoteWatchlistToLead(item);
        });
        card.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
            void actions.deleteWatchlistItem(item.id);
        });
        return card;
    }

    return {
        buildListingNode,
        buildOpportunityCard,
        buildSignalRow,
        buildLeadNode,
        buildWatchlistNode,
    };
}
