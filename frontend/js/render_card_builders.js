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
        optimizedImage,
    } = context;

    function buildMediaNode(imageClass, placeholderClass, placeholderText, url, altText) {
        const validated = safeUrl(url);
        if (validated) {
            // Route Kufar JPEG thumbnails through the WebP/AVIF
            // proxy. Non-Kufar URLs pass through untouched.
            const src = typeof optimizedImage === "function"
                ? optimizedImage(validated, { width: 320 })
                : validated;
            return domEl("img", {
                className: imageClass,
                attrs: { src, alt: altText || "", loading: "lazy", decoding: "async" },
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

        // The backend always computes price_vs_median against the right
        // reference (per-category if the category has ≥3 ads, otherwise the
        // whole query). The fallback below only fires when we have no
        // backend-supplied delta — and falls back to the query-level median
        // exposed in state.stats, which is the same reference type as the
        // backend's "query" scope, so the values stay comparable.
        let delta = item.price_vs_median;
        if (delta == null && item.price && state.stats?.median && Number(state.stats.median) > 0) {
            delta = Math.round(((Number(item.price) - Number(state.stats.median)) / Number(state.stats.median)) * 100 * 100) / 100;
        }
        if (delta != null) {
            const absDelta = Math.abs(delta);
            if (absDelta < 0.5) {
                badges.push(domEl("span", {
                    className: "listing-badge delta-approx",
                    text: "≈",
                    attrs: { title: item.price_reference_label
                        ? `По рынку (${item.price_reference_label})`
                        : "По рынку" },
                }));
            } else {
                // Show the reference label as a hover tooltip so the user can
                // confirm whether the % is vs category or vs the whole query.
                const tooltipPrefix = delta > 0 ? "Выше" : "Ниже";
                const refLabel = item.price_reference_scope === "category"
                    ? (item.price_reference_label || "категории")
                    : "среднего по запросу";
                badges.push(domEl("span", {
                    className: `listing-badge ${deltaClass(delta)}`.trim(),
                    text: formatDelta(delta),
                    attrs: { title: `${tooltipPrefix} ${refLabel}` },
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
                    buildMediaNode(
                        "listing-thumb",
                        "listing-thumb placeholder",
                        "Нет фото",
                        item.thumbnail,
                        item.title || item.subject || "",
                    ),
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

        // Long-press the card to invoke a quick-action menu — same
        // actions as the inline buttons plus a direct "Открыть на
        // Kufar" shortcut, surfaced via a bottom sheet so cluttered
        // search results stay scannable. Helper falls back to a no-op
        // on desktop / when touch events never fire.
        if (typeof attachLongPress === "function") {
            attachLongPress(listing, () => [
                {
                    label: "Подробнее",
                    icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
                    onSelect: () => actions.openListingDetail(item),
                },
                {
                    label: "В покупки",
                    tone: "accent",
                    icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-4"/><polyline points="9 11 12 8 15 11"/><line x1="12" y1="2" x2="12" y2="14"/></svg>',
                    onSelect: () => actions.addLeadFromListing(item),
                },
                {
                    label: "В избранное",
                    icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
                    onSelect: () => actions.addWatchlistFromListing(item),
                },
                {
                    label: "Открыть на Kufar",
                    icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
                    onSelect: () => {
                        const link = safeUrl(item.link);
                        if (!link) return;
                        if (window.Telegram?.WebApp?.openLink) {
                            window.Telegram.WebApp.openLink(link);
                        } else {
                            window.open(link, "_blank", "noopener,noreferrer");
                        }
                    },
                },
            ]);
        }
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

    /* ─────────────────────────────────────────────────────────────────
     * Unified item card — watchlist (`mode='watching'`) and lead
     * (`mode='lead'`) share the same lead_items row after the
     * 20260427_0001 merge, so they share a single builder. The two
     * surfaces diverge in three places only:
     *   1. price source (lead.price_byn vs item.current_price_byn)
     *   2. middle "fields" section (buy/sold inputs vs note input)
     *   3. action row (confirm/cancel/close-deal vs detail/promote/delete)
     * Everything else — outer wrapper, media + title, missing banner,
     * profit/potential block, Kufar link — is shared via small helpers.
     * ─────────────────────────────────────────────────────────────────
     */

    /** Hash a numeric or null value into rounded int or null. */
    function _roundOrNull(value) {
        const n = value != null ? Number(value) : null;
        return n && n > 0 ? Math.round(n) : null;
    }

    /** Build a shared "missing" banner with a mode-appropriate message. */
    function _buildMissingBanner(mode) {
        const text =
            mode === "watching"
                ? "Объявление снято с продажи. Будет удалено автоматически через несколько дней."
                : "Объявление снято с продажи";
        return domEl("div", { className: "watchlist-missing-banner", text });
    }

    /** Compute and render the price-delta pill ("📉 -120 BYN (-8%)"). */
    function _buildPriceDeltaNode(item) {
        if (item.price_delta_byn == null || Math.abs(item.price_delta_byn) <= 0.5) {
            return null;
        }
        const deltaNum = Math.round(item.price_delta_byn);
        const className = deltaNum < 0 ? "down" : deltaNum > 0 ? "up" : "neutral";
        const sign = deltaNum > 0 ? "+" : "";
        const arrow = deltaNum < 0 ? "📉" : deltaNum > 0 ? "📈" : "≈";
        const percent =
            item.price_delta_percent != null ? ` (${sign}${item.price_delta_percent}%)` : "";
        return domEl("span", {
            className: `watchlist-price-delta ${className}`,
            text: `${arrow} ${sign}${deltaNum} BYN${percent}`,
        });
    }

    /** Build an inline SVG sparkline from a watchlist item's
     *  price_history. Returns null when the series is too short to be
     *  meaningful (≤1 data point). The line is coloured by the
     *  net direction of the trend so a green line = price went down
     *  (good for a buyer) and a red line = price went up.
     *
     *  Pure-SVG, no dependency on Chart.js — keeps the watchlist
     *  render path off the lazy chart library entirely. */
    function _buildPriceSparkline(item) {
        const history = Array.isArray(item.price_history) ? item.price_history : [];
        if (history.length < 2) return null;

        const prices = history
            .map((p) => Number(p.price_byn))
            .filter((n) => Number.isFinite(n) && n > 0);
        if (prices.length < 2) return null;

        const min = Math.min(...prices);
        const max = Math.max(...prices);
        const span = Math.max(1, max - min);
        const width = 88;
        const height = 28;
        const padX = 1;
        const padY = 2;

        const points = prices.map((p, i) => {
            const x = padX + (i / (prices.length - 1)) * (width - padX * 2);
            // Invert Y so higher prices sit higher in the SVG (origin
            // top-left, but visually we want UP = more expensive).
            const y = padY + (1 - (p - min) / span) * (height - padY * 2);
            return `${x.toFixed(2)},${y.toFixed(2)}`;
        });

        // Direction: net change from first to last. Buyer-friendly:
        // down = green ("price dropped, deal warming up"), up = red.
        const first = prices[0];
        const last = prices[prices.length - 1];
        const direction =
            last < first - 0.5 ? "down" : last > first + 0.5 ? "up" : "flat";

        // Build a polyline + area-fill underneath.
        const linePath = `M ${points.join(" L ")}`;
        const areaPath =
            `M ${padX},${height - padY} ` +
            `L ${points.join(" L ")} ` +
            `L ${width - padX},${height - padY} Z`;

        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", `wl-sparkline wl-sparkline--${direction}`);
        svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
        svg.setAttribute("width", String(width));
        svg.setAttribute("height", String(height));
        svg.setAttribute("aria-hidden", "true");
        svg.setAttribute("role", "presentation");

        const area = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "path",
        );
        area.setAttribute("class", "wl-sparkline-area");
        area.setAttribute("d", areaPath);
        svg.appendChild(area);

        const line = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "path",
        );
        line.setAttribute("class", "wl-sparkline-line");
        line.setAttribute("d", linePath);
        line.setAttribute("fill", "none");
        svg.appendChild(line);

        // Last-point dot for emphasis ("here's where you are now").
        const lastPoint = points[points.length - 1].split(",");
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("class", "wl-sparkline-dot");
        dot.setAttribute("cx", lastPoint[0]);
        dot.setAttribute("cy", lastPoint[1]);
        dot.setAttribute("r", "2");
        svg.appendChild(dot);

        const wrap = domEl("div", {
            className: "wl-sparkline-wrap",
            attrs: {
                title:
                    direction === "down"
                        ? `Цена снижается (${prices.length} точек)`
                        : direction === "up"
                          ? `Цена растёт (${prices.length} точек)`
                          : `Цена стабильна (${prices.length} точек)`,
            },
        });
        wrap.appendChild(svg);
        return wrap;
    }

    /** Watchlist potential profit (median - current) — null in lead mode. */
    function _buildPotentialProfitNode(item) {
        const current = item.current_price_byn || item.initial_price_byn;
        if (!item.market_median_byn || !current || Number(item.market_median_byn) <= 0) {
            return null;
        }
        const profitByn = Number(item.market_median_byn) - Number(current);
        const profitPercent = current > 0 ? Math.round((profitByn / Number(current)) * 100) : 0;
        const className = profitByn >= 0 ? "profit-positive" : "profit-negative";
        const sign = profitByn >= 0 ? "+" : "";
        return domEl("span", {
            className: `watchlist-profit ${className}`,
            text: `Потенциал: ${sign}${Math.round(profitByn)} BYN (${sign}${profitPercent}%)`,
        });
    }

    /** Lead profit/potential ("Результат" if sold, "Потенциал" if active). */
    function _buildLeadProfitNode(lead) {
        const buy = _roundOrNull(lead.buy_price_byn);
        const sold = _roundOrNull(lead.sold_price_byn);
        if (!buy || !sold) return null;
        const profitRaw = sold - buy;
        const percent = buy > 0 ? ((profitRaw / buy) * 100).toFixed(0) : "0";
        const sign = profitRaw >= 0 ? "+" : "";
        const cls = profitRaw >= 0 ? "profit-positive" : "profit-negative";
        const isSold = lead.status === "sold";
        const showPotential = !isSold && (lead.status === "new" || lead.status === "bought");
        if (!isSold && !showPotential) return null;
        const label = isSold ? "Результат" : "Потенциал";
        return domEl("div", {
            className: `lead-financial-item ${cls}`,
            text: `${label}: ${sign}${Math.round(profitRaw)} BYN (${sign}${percent}%)`,
        });
    }

    /** Market badge ("Падение цены", "Пропало (3д)") — watchlist only. */
    function _buildMarketBadge(item, marketLabel) {
        if (!item.market_status || !["price_drop", "missing"].includes(item.market_status)) {
            return null;
        }
        let missingAgeText = "";
        if (item.market_status === "missing" && item.missing_since_at) {
            const missingDate = new Date(item.missing_since_at);
            const diffMs = Date.now() - missingDate.getTime();
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
            if (diffDays >= 1) missingAgeText = ` (${diffDays}д)`;
        }
        return domEl("span", {
            className: `market-badge ${item.market_status}`,
            text: `${marketLabel(item.market_status)}${missingAgeText}`,
        });
    }

    /** Lead-specific buy/sold price input fields. */
    function _buildLeadFields(lead) {
        const priceByn = _roundOrNull(lead.price_byn);
        const buyInput = domEl("input", {
            type: "text",
            attrs: { min: "0", placeholder: "цена покупки" },
            dataset: { role: "buy-price" },
        });
        buyInput.value = lead.buy_price_byn ?? "";
        buyInput.addEventListener("input", (e) => {
            const next = e.target.value.replace(/[^\d]/g, "");
            if (next !== e.target.value) e.target.value = next;
        });

        const soldInput = domEl("input", {
            type: "text",
            attrs: { min: "0", placeholder: "цена продажи" },
            dataset: { role: "sold-price" },
        });
        soldInput.value = lead.sold_price_byn ?? "";
        soldInput.addEventListener("input", (e) => {
            const next = e.target.value.replace(/[^\d]/g, "");
            if (next !== e.target.value) e.target.value = next;
        });

        const fillBuy = domEl("button", {
            className: "lead-field-chip",
            type: "button",
            dataset: { role: "fill-buy-price" },
            text: priceByn ? `${priceByn}` : "Договорная",
            attrs: !priceByn ? { disabled: true } : {},
        });
        fillBuy.addEventListener("click", () => {
            if (priceByn) {
                buyInput.value = String(priceByn);
                buyInput.focus();
            }
        });

        return domEl(
            "div",
            { className: "lead-card-fields" },
            domEl(
                "label",
                { className: "lead-field" },
                domEl("span", { className: "lead-field-label", text: "Купил за" }),
                domEl(
                    "div",
                    { className: "lead-field-wrap" },
                    buyInput,
                    fillBuy,
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
                    soldInput,
                    domEl("span", { className: "unit", text: "BYN" }),
                ),
            ),
        );
    }

    /** Watchlist-specific note input. */
    function _buildWatchlistFields(item) {
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
        return domEl(
            "div",
            { className: "watchlist-card-fields" },
            domEl(
                "label",
                { className: "wl-field wl-field-wide" },
                domEl("span", { className: "wl-field-label", text: "Заметка" }),
                domEl("div", { className: "wl-field-input-wrap" }, notesInput),
            ),
        );
    }

    /** Lead action row — depends on `status`. */
    function _buildLeadActions(lead) {
        const isSold = lead.status === "sold";
        return domEl(
            "div",
            { className: "lead-card-actions" },
            !isSold
                ? domEl(
                    "div",
                    { className: "lead-btn-row" },
                    domEl("a", {
                        className: "lead-btn lead-btn--kufar",
                        text: "Kufar ↗",
                        attrs: {
                            href: safeUrl(lead.link),
                            target: "_blank",
                            rel: "noreferrer noopener",
                        },
                    }),
                )
                : null,
            !isSold
                ? domEl(
                    "div",
                    { className: "lead-btn-row" },
                    domEl("button", {
                        className: "lead-btn lead-btn--confirm",
                        type: "button",
                        dataset: { role: "confirm" },
                        text: "✓",
                    }),
                    domEl("button", {
                        className: "lead-btn lead-btn--delete",
                        type: "button",
                        dataset: { role: "cancel" },
                        text: "✕",
                    }),
                )
                : null,
            isSold
                ? domEl(
                    "div",
                    { className: "lead-btn-row" },
                    domEl("button", {
                        className: "lead-btn lead-btn--success",
                        type: "button",
                        dataset: { role: "close-deal" },
                        text: "✓ Готово",
                    }),
                    domEl("button", {
                        className: "lead-btn lead-btn--revert",
                        type: "button",
                        dataset: { role: "revert" },
                        text: "↩ Назад",
                    }),
                )
                : null,
        );
    }

    /** Watchlist action row — Kufar link is hidden when the listing is missing. */
    function _buildWatchlistActions(item, isMissing) {
        return domEl(
            "div",
            { className: "watchlist-card-actions" },
            domEl("button", {
                className: "wl-btn wl-btn--detail",
                type: "button",
                dataset: { role: "detail" },
                text: "Подробнее",
            }),
            !isMissing
                ? domEl("button", {
                    className: "wl-btn wl-btn--accent",
                    type: "button",
                    dataset: { role: "lead" },
                    text: "В покупки",
                })
                : null,
            !isMissing
                ? domEl("a", {
                    className: "wl-btn",
                    text: "Kufar ↗",
                    attrs: {
                        href: safeUrl(item.link),
                        target: "_blank",
                        rel: "noreferrer noopener",
                    },
                })
                : null,
            domEl("button", {
                className: "wl-btn wl-btn--danger",
                type: "button",
                dataset: { role: "delete" },
                text: "Удалить",
            }),
        );
    }

    /** Wire DOM-event handlers based on the card's mode. */
    function _wireCardHandlers(card, item, mode) {
        if (mode === "lead") {
            card.querySelector('[data-role="confirm"]')?.addEventListener("click", () => {
                void actions.confirmLead(item, card);
            });
            card.querySelector('[data-role="cancel"]')?.addEventListener("click", () => {
                void actions.cancelLead(item.id);
            });
            card.querySelector('[data-role="close-deal"]')?.addEventListener("click", () => {
                void actions.closeDeal(item.id);
            });
            card.querySelector('[data-role="revert"]')?.addEventListener("click", () => {
                void actions.revertLeadStage(item.id, item.status);
            });
        } else if (mode === "watching") {
            card.querySelector('[data-role="detail"]')?.addEventListener("click", () => {
                void actions.openWatchlistDetail(item);
            });
            card.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
                void actions.promoteWatchlistToLead(item);
            });
            card.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteWatchlistItem(item.id);
            });
        }
    }

    /**
     * Single source of truth for both "Покупки" (lead) and "Избранное"
     * (watching) cards. Pass `mode='lead'` or `mode='watching'`.
     *
     * @param {object} item LeadItem-shaped row from /api/v1/{leads,watchlist}.
     * @param {object} options { mode, marketLabel }
     */
    function buildItemCard(item, options = {}) {
        const { mode = "lead", marketLabel } = options;
        const isWatching = mode === "watching";
        const isLead = mode === "lead";
        const isMissing = item.market_status === "missing";

        const outerClass = isLead
            ? `lead-card status-${item.status}`
            : "watchlist-card";
        const card = domEl("article", {
            className: outerClass,
            attrs: isLead ? { "data-lead-id": item.id } : { "data-watchlist-id": item.id },
        });

        // Price for the header. Lead reads price_byn directly; watchlist
        // prefers the live current_price_byn, falling back to the price
        // captured at watchlist-add time.
        const priceSource = isLead
            ? item.price_byn
            : item.current_price_byn || item.initial_price_byn;
        const priceDisplay = _roundOrNull(priceSource);

        const titleNode = isLead
            ? domEl(
                "div",
                { className: "lead-card-title-row" },
                domEl("strong", { className: "lead-card-title", text: item.title }),
            )
            : domEl("strong", { className: "watchlist-card-title", text: item.title });

        const priceRow = isLead
            ? domEl("span", {
                className: "lead-card-price mono",
                text: priceDisplay ? `${priceDisplay} BYN` : "без цены",
            })
            : domEl(
                "div",
                { className: "watchlist-card-price-row" },
                domEl("span", {
                    className: "watchlist-card-price mono",
                    text: priceDisplay ? `${priceDisplay} BYN` : "Договорная",
                }),
                _buildPriceDeltaNode(item),
                // 30-day price-trend sparkline next to the current price.
                // Returns null until the row has ≥2 history points so a
                // freshly-added watchlist item shows the price + delta
                // alone for the first day or two.
                _buildPriceSparkline(item),
            );

        // Market badge + missing pill (lead shows a single pill, watching
        // shows the full marketLabel(...) text).
        const missingMiniBadge = isLead && isMissing
            ? domEl("span", { className: "market-badge missing", text: "Пропало" })
            : null;
        const watchingMarketBadge = isWatching
            ? domEl("div", { className: "watchlist-card-meta" }, _buildMarketBadge(item, marketLabel))
            : null;

        const profitNode = isLead ? _buildLeadProfitNode(item) : _buildPotentialProfitNode(item);

        const bodyClass = isLead ? "lead-card-body" : "watchlist-card-body";
        const topClass = isLead ? "lead-card-top" : "watchlist-card-top";

        const fields = isLead ? _buildLeadFields(item) : _buildWatchlistFields(item);
        const actionsRow = isLead
            ? _buildLeadActions(item)
            : _buildWatchlistActions(item, isMissing);

        card.appendChild(
            domFragment(
                isMissing ? _buildMissingBanner(mode) : null,
                domEl(
                    "div",
                    { className: `${topClass}${isMissing ? " is-missing" : ""}` },
                    buildMediaNode(
                        "watchlist-thumb",
                        "watchlist-thumb-placeholder",
                        "Нет фото",
                        item.thumbnail,
                        item.title || "",
                    ),
                    domEl(
                        "div",
                        { className: bodyClass },
                        titleNode,
                        priceRow,
                        missingMiniBadge,
                        profitNode,
                        watchingMarketBadge,
                    ),
                ),
                fields,
                actionsRow,
            )
        );

        _wireCardHandlers(card, item, mode);

        // Watching cards support swipe gestures: left → delete, right →
        // promote to "Покупки". Lead cards stay tap-only — they have
        // editable inputs in the middle that would conflict with horizontal
        // pans. makeSwipeable returns the card unchanged under
        // prefers-reduced-motion, so the buttons in the action row remain
        // the canonical interaction in either case.
        if (isWatching && !isMissing) {
            return makeSwipeable(card, {
                onSwipeLeft: {
                    label: "Удалить",
                    className: "swipe-bg--danger",
                    action: () => actions.deleteWatchlistItem(item.id),
                },
                onSwipeRight: {
                    label: "В покупки",
                    className: "swipe-bg--accent",
                    action: () => actions.promoteWatchlistToLead(item),
                },
            });
        }
        return card;
    }

    // Backwards-compat thin wrappers — callers in render_cards.js still
    // import these names. Keeping them lets us land the unification
    // without touching every render path.
    const buildLeadNode = (lead) => buildItemCard(lead, { mode: "lead" });
    const buildWatchlistNode = (item, marketLabel) =>
        buildItemCard(item, { mode: "watching", marketLabel });

    return {
        buildListingNode,
        buildOpportunityCard,
        buildSignalRow,
        buildItemCard,
        buildLeadNode,
        buildWatchlistNode,
    };
}
