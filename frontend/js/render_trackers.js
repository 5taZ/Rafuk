/**
 * Tracker cards and listing events.
 */

function createRenderTrackers(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatPriceNumber,
        formatCondition,
        formatSeller,
        formatDate,
        hasTelegramInitData,
        safeKufarUrl,
        safeImageUrl,
        optimizedImage,
        safeRender,
        domEl,
        domClear,
        // FR-18: shared lead / ad identity rules (dom_helpers.js).
        ACTIVE_LEAD_STATUSES,
        sameAdId,
    } = context;

    let eventDetailObserver = null;

    // FE-08 / Wave 29: build SVG icons via createElementNS instead of
    // ``innerHTML = "<svg...>"``. The previous code worked because
    // every SVG payload was a hardcoded source-code constant, but a
    // future refactor that interpolates ANY runtime value into one
    // of these strings would have re-introduced an XSS vector. The
    // descriptor form below is forced to go through DOM APIs that
    // can never execute injected script regardless of input.
    const SVG_NS = "http://www.w3.org/2000/svg";

    function buildSvgIcon(viewBox, attrs, children) {
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("viewBox", viewBox);
        svg.setAttribute("aria-hidden", "true");
        for (const [k, v] of Object.entries(attrs || {})) svg.setAttribute(k, String(v));
        for (const child of children) {
            const el = document.createElementNS(SVG_NS, child.tag);
            for (const [k, v] of Object.entries(child)) {
                if (k === "tag") continue;
                el.setAttribute(k, String(v));
            }
            svg.appendChild(el);
        }
        return svg;
    }

    // Common attribute presets for the action icons.
    const _STROKE_ATTRS = {
        fill: "none",
        stroke: "currentColor",
        "stroke-width": "2",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
    };

    function iconOpen() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, ..._STROKE_ATTRS },
            [
                { tag: "path", d: "M5 12h14M12 5l7 7-7 7" },
            ]);
    }
    function iconListingPlaceholder() {
        return buildSvgIcon("0 0 24 24",
            { width: 24, height: 24, ..._STROKE_ATTRS },
            [
                { tag: "rect", x: "4", y: "5", width: "16", height: "14", rx: "2" },
                { tag: "path", d: "M8 9h8" },
                { tag: "path", d: "M8 13h5" },
                { tag: "path", d: "M15 17h1" },
            ]);
    }

    function _isActiveLeadAd(adId) {
        return (state.leads?.items || []).some(
            (lead) => sameAdId(lead.ad_id, adId) && ACTIVE_LEAD_STATUSES.has(lead.status),
        );
    }

    function _isActiveWatchAd(adId) {
        return (state.watchlist?.items || []).some(
            (w) => sameAdId(w.ad_id, adId),
        );
    }

    // RV-UI-02: ids tying an event card's button to its description.
    let _eventCardSeq = 0;

    function relativeTime(isoDate) {
        if (!isoDate) return "";
        const timestamp = Date.parse(isoDate);
        if (!Number.isFinite(timestamp)) return "";
        const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
        if (minutes === 0) return "только что";
        if (minutes < 60) return `${minutes} мин назад`;
        if (minutes < 1440) return `${Math.floor(minutes / 60)} ч назад`;
        return `${Math.floor(minutes / 1440)} дн. назад`;
    }

    function trackerFilters(tracker) {
        return [
            tracker.max_price_byn != null ? `до ${formatPriceNumber(tracker.max_price_byn)} BYN` : "",
            tracker.region_name,
            formatCondition(tracker.condition) || tracker.condition,
            formatSeller(tracker.seller_type) || tracker.seller_type,
            tracker.min_discount_percent > 0 ? `скидка от ${tracker.min_discount_percent.toLocaleString("ru-RU")}%` : "",
            tracker.category_id ? tracker.category_label || `категория ${tracker.category_id}` : "",
            tracker.strict_mode ? "Точное совпадение" : "",
        ].filter(Boolean);
    }

    function renderTrackerSourceFilter() {
        const select = elements.trackerEventTrackerSelect;
        if (!select) return;
        const sources = new Map(state.trackers.items.map((tracker) =>
            [tracker.id, [tracker.query, ...trackerFilters(tracker)].join(" · ")]));
        for (const [id, label] of state.trackers.eventSources || []) {
            if (!sources.has(id)) sources.set(id, label);
        }
        const selected = state.trackers.eventFilterTrackerId;
        if (selected != null && !sources.has(selected)) sources.set(selected, `Трекер №${selected}`);
        const sourceKey = JSON.stringify([...sources]);
        if (sourceKey !== select._sourceKey) {
            select.replaceChildren(domEl("option", { value: "", text: "Все трекеры" }),
                ...Array.from(sources, ([id, label]) => domEl("option", { value: id, text: label })));
            select._sourceKey = sourceKey;
        }
        select.value = String(selected ?? "");
    }

    function trackerStat(label, value) {
        return domEl("div", { className: "tracker-stat" },
            domEl("dt", { text: label }),
            domEl("dd", { text: value, className: "mono" }));
    }

    function buildTrackerCard(tracker) {
        const filters = trackerFilters(tracker);
        const checked = relativeTime(tracker.last_checked_at);
        const lastEvent = relativeTime(tracker.last_event_at);
        const pauseState = tracker.paused ? "На паузе" : "Активен";
        const eventsButton = domEl("button", {
            className: "ghost-btn tracker-action-primary",
            type: "button",
            text: "События",
            dataset: { role: "view-events" },
            attrs: { "aria-label": `События трекера «${tracker.query}»` },
        }, iconOpen());
        eventsButton.addEventListener("click", () => actions.showTrackerEvents(tracker.id));
        const editButton = domEl("button", {
            className: "ghost-btn",
            type: "button",
            text: "Настроить",
            dataset: { role: "edit" },
            attrs: { "aria-label": `Настроить трекер «${tracker.query}»`, "aria-haspopup": "dialog" },
        });
        editButton.addEventListener("click", () => actions.openEditTracker(tracker.id));

        return domEl("article", {
            className: "tracker-card-enhanced",
            attrs: { role: "listitem" },
            dataset: { trackerId: tracker.id },
        },
            domEl("div", { className: "tracker-card-top" },
                domEl("h3", { className: "tracker-query-title", text: tracker.query }),
                domEl("span", {
                    className: `tracker-state${tracker.paused ? " tracker-state--paused" : ""}`,
                    text: pauseState,
                })),
            domEl("p", { className: "tracker-check-info" },
                domEl("span", { text: `Каждые ${tracker.interval_min} мин` }),
                checked ? domEl("time", {
                    text: `Проверен ${checked}`,
                    attrs: { datetime: tracker.last_checked_at, title: formatDate(tracker.last_checked_at) },
                }) : domEl("span", { text: "Первой проверки ещё не было" })),
            filters.length ? domEl("div", { className: "tracker-filters", attrs: { "aria-label": "Фильтры" } },
                ...filters.map((text) => domEl("span", { className: "tracker-filter-tag", text })))
                : domEl("p", { className: "tracker-note tracker-no-filters", text: "Без дополнительных фильтров" }),
            domEl("dl", { className: "tracker-stats" },
                trackerStat("События", (tracker.event_count || 0).toLocaleString("ru-RU")),
                trackerStat("В день", (tracker.avg_events_per_day || 0).toLocaleString("ru-RU")),
                trackerStat("Последнее", lastEvent || "—")),
            domEl("div", { className: "tracker-card-actions" }, eventsButton, editButton));
    }

    function renderTrackers() {
        return safeRender("renderTrackers", () => {
            const { trackersList, trackQueryButton, trackingTabs, trackingCount } = elements;
            if (!trackersList) return;
            const tracking = state.trackers;
            const authenticated = hasTelegramInitData();
            if (trackQueryButton) trackQueryButton.hidden = !authenticated;
            if (trackingTabs) trackingTabs.hidden = !authenticated;
            if (trackingCount) {
                trackingCount.hidden = !tracking.items.length;
                trackingCount.textContent = String(tracking.items.length);
            }
            renderTrackerSourceFilter();

            const focusedButton = document.activeElement?.closest?.(".tracker-card-enhanced button");
            const focusedCard = focusedButton?.closest("[data-tracker-id]");
            domClear(trackersList);
            const buildEmpty = context.buildEmptyState;
            if (!authenticated) {
                trackersList.appendChild(buildEmpty({
                    title: "Откройте Rafuk в Telegram",
                    hint: "В мини-приложении можно создавать трекеры и получать уведомления в чате с ботом.",
                }));
                return;
            }
            if (tracking._loading && !tracking.items.length) {
                trackersList.appendChild(domEl("p", { className: "tracker-note tracker-loading", text: "Загружаем трекеры…", attrs: { role: "status" } }));
                return;
            }
            if (tracking.loadError) {
                trackersList.appendChild(buildEmpty({
                    title: tracking.loadError,
                    hint: tracking.items.length ? "Показан последний загруженный список." : "Попробуйте загрузить список ещё раз.",
                    actionLabel: "Повторить",
                    onAction: () => { void actions.loadTrackers(); },
                    tone: "error",
                }));
            }
            if (tracking.items.length) {
                trackersList.appendChild(domEl("div", { className: "tracker-cards", attrs: { role: "list" } },
                    ...tracking.items.map(buildTrackerCard)));
                if (focusedCard) {
                    const selector = `[data-tracker-id="${focusedCard.dataset.trackerId}"] [data-role="${focusedButton.dataset.role}"]`;
                    trackersList.querySelector(selector)?.focus({ preventScroll: true });
                }
            } else if (!tracking.loadError) {
                trackersList.appendChild(buildEmpty({
                    title: "У вас пока нет трекеров",
                    hint: "Нажмите «Новый трекер» и введите название вещи. При необходимости укажите бюджет или город.",
                }));
            }
        });
    }

    function renderTrackerEvents() {
        return safeRender("renderTrackerEvents", () => {
            const container = elements.trackerEventsList;
            if (!container) return;
            eventDetailObserver?.disconnect();
            eventDetailObserver = null;
            domClear(container);

            const tracking = state.trackers;
            const authenticated = hasTelegramInitData();
            if (elements.trackerFeedTools) elements.trackerFeedTools.hidden = !authenticated;
            if (!authenticated) return;

            renderTrackerSourceFilter();
            if (elements.trackerEventTypeSelect) elements.trackerEventTypeSelect.value = tracking.eventFilter;
            if (elements.refreshTrackerEventsButton) elements.refreshTrackerEventsButton.disabled = tracking.eventsLoading || tracking.clearingEvents;
            const filtered = tracking.eventFilterTrackerId != null || tracking.eventFilter !== "all";
            const filteredEvents = tracking.events.filter((event) => event?.ad_id != null && event.event_type !== "trend_reversal"
                && (tracking.eventFilterTrackerId == null || event.tracker_id === tracking.eventFilterTrackerId)
                && (tracking.eventFilter === "all" || event.event_type === tracking.eventFilter));
            const count = filteredEvents.length;
            if (elements.clearEventsButton) {
                elements.clearEventsButton.hidden = filtered || count === 0;
                elements.clearEventsButton.disabled = tracking.clearingEvents || tracking.eventsLoading || Boolean(tracking.eventsError);
            }
            if (elements.trackerFeedNote) {
                let text = count ? `${count} в ленте` : "";
                if (count >= tracking.eventLimit) text = `Последние ${count} событий`;
                if (tracking.eventsError && count) text = "Не удалось обновить. Показаны ранее загруженные события.";
                if (tracking.eventsLoading) text = "Обновляем ленту…";
                if (tracking.clearingEvents) text = "Очищаем ленту…";
                if (elements.trackerFeedNote.textContent !== text) elements.trackerFeedNote.textContent = text;
            }

            if (!count) {
                if (tracking.eventsLoading || tracking.clearingEvents) return;
                const buildEmpty = context.buildEmptyState;
                if (tracking.eventsError) {
                    container.appendChild(buildEmpty({
                        title: tracking.eventsError,
                        hint: "Повторите загрузку, чтобы увидеть события.",
                        actionLabel: "Повторить",
                        onAction: () => { void actions.refreshTrackerEvents(); },
                        tone: "error",
                    }));
                } else if (filtered) {
                    container.appendChild(buildEmpty({
                        title: "Событий по этим фильтрам пока нет",
                        hint: "Выберите другой трекер или тип события.",
                        actionLabel: "Сбросить фильтры",
                        onAction: () => actions.showTrackerEvents(),
                    }));
                } else {
                    container.appendChild(buildEmpty({
                        title: "Лента пока пуста",
                        hint: "Здесь будут новые объявления и изменения цены, которые найдёт автопоиск.",
                    }));
                }
                return;
            }

            // Warm only cards close to the viewport.
            if (typeof IntersectionObserver === "function" && actions.preloadListingDetail) {
                eventDetailObserver = new IntersectionObserver((entries) => {
                    for (const entry of entries) {
                        if (!entry.isIntersecting) continue;
                        entry.target._preloadDetail?.();
                        eventDetailObserver?.unobserve(entry.target);
                    }
                }, { rootMargin: "160px" });
            }
            function buildEventNode(event) {
                const isNewListing = event.event_type === "new_listing";
                const isPriceDrop = event.event_type === "price_drop";
                const publishedTime = isNewListing ? formatDate(event.list_time) : "";
                const displayTime = publishedTime || formatDate(event.created_at);
                const timeNode = displayTime ? domEl("time", {
                    className: "event-time",
                    text: isNewListing && !publishedTime ? `Найдено ${displayTime}` : displayTime,
                    attrs: {
                        datetime: publishedTime ? event.list_time : event.created_at,
                        title: publishedTime ? "Опубликовано на Kufar" : "Обнаружено автопоиском",
                    },
                }) : null;
                const thumbSrc = (() => {
                    const validated = safeImageUrl(event.thumbnail);
                    if (!validated) return "";
                    return typeof optimizedImage === "function"
                        ? optimizedImage(validated, { width: 200 })
                        : validated;
                })();
                const thumbnailNode = thumbSrc
                    ? domEl("img", {
                        className: "event-thumbnail",
                        // RV-UI-02: the title is the next node; the alt would
                        // only repeat it.
                        attrs: { src: thumbSrc, alt: "", loading: "lazy" },
                    })
                    // FE-05: aria-label exposes "Нет фото" while the
                    // decorative SVG stays hidden from screen readers.
                    : (() => {
                        const node = domEl("div", {
                            className: "event-thumbnail-placeholder",
                            attrs: { "aria-label": "Нет фото", role: "img" },
                        });
                        node.appendChild(iconListingPlaceholder());
                        return node;
                    })();
                const metaText = [event.region_name, event.seller_type].filter(Boolean).join(" · ");
                const eventMeta = metaText ? domEl("div", { className: "event-meta", text: metaText }) : null;
                const priceRow = domEl(
                    "div",
                    { className: "event-price-row" },
                    domEl("span", { className: "event-price mono", text: formatPrice(event.price_byn, event.parameters?.price_type) }),
                );
                if (event.delta_byn && event.event_type === "price_drop") {
                    priceRow.appendChild(domEl("span", { className: "event-delta", text: `-${formatPrice(event.delta_byn)}` }));
                }
                if (event.event_type === "price_threshold_alert" && event.parameters?.threshold != null) {
                    priceRow.appendChild(domEl("span", { className: "event-delta event-delta--alert", text: `порог: ${formatPrice(event.parameters.threshold)}` }));
                }
                if (event.event_type === "discount_alert" && event.parameters?.discount_percent) {
                    priceRow.appendChild(domEl("span", { className: "event-delta event-delta--alert", text: `−${Math.round(event.parameters.discount_percent)}% от медианы` }));
                }

                let kindLabel = "";
                let cardModifier = "";
                if (isPriceDrop) {
                    kindLabel = "Цена снизилась";
                    cardModifier = " price-drop";
                } else if (isNewListing) {
                    kindLabel = "Новое объявление";
                    cardModifier = " new-listing";
                } else if (event.event_type === "price_threshold_alert") {
                    kindLabel = "Достигнут порог цены";
                    cardModifier = " threshold-alert";
                } else if (event.event_type === "discount_alert") {
                    kindLabel = "Скидка от медианы";
                    cardModifier = " discount-alert";
                }
                const inLeads = _isActiveLeadAd(event.ad_id);
                const inWatchlist = _isActiveWatchAd(event.ad_id);
                const leadText = inLeads ? "В покупках" : "В покупки";
                const watchText = inWatchlist ? "В избранном" : "В избранное";
                const watchDisabled = inLeads || inWatchlist;
                const eventTitle = event.title || "объявление";
                const listingUrl = safeKufarUrl(event.link);

                // RV-UI-02: a list item whose title button is the primary
                // action (hit area stretched over the header in CSS) instead
                // of role=button around three more controls; the event type,
                // price and place are announced as the button's description.
                const cardId = `tracker-event-${++_eventCardSeq}`;
                const kindNode = kindLabel ? domEl("span", {
                    className: "event-kind",
                    text: kindLabel,
                    attrs: { id: `${cardId}-type` },
                }) : null;
                priceRow.id = `${cardId}-price`;
                if (eventMeta) eventMeta.id = `${cardId}-meta`;
                const describedBy = [kindNode, priceRow, eventMeta]
                    .filter(Boolean)
                    .map((node) => node.id)
                    .join(" ");

                const card = domEl(
                    "div",
                    { className: `tracker-event-card${cardModifier}`, attrs: { role: "listitem" } },
                    domEl(
                        "div",
                        { className: "event-header" },
                        thumbnailNode,
                        domEl(
                            "div",
                            { className: "event-body" },
                            kindNode || timeNode ? domEl(
                                "div",
                                { className: "event-top-row" },
                                kindNode,
                                timeNode,
                            ) : null,
                            domEl(
                                "h3",
                                { className: "card-heading event-heading" },
                                domEl(
                                    "button",
                                    {
                                        className: "card-open",
                                        type: "button",
                                        attrs: { "aria-describedby": describedBy || null },
                                    },
                                    domEl("span", { className: "event-title", text: event.title || "Объявление" }),
                                ),
                            ),
                            priceRow,
                            eventMeta,
                            domEl("span", { className: "event-tracker-source" },
                                domEl("span", { className: "event-tracker-label", text: "Трекер" }),
                                domEl("span", { text: event.query })),
                        ),
                    ),
                    domEl(
                        "div",
                        { className: "event-actions" },
                        domEl("button", {
                            className: "listing-btn",
                            type: "button",
                            dataset: { role: "lead" },
                            text: leadText,
                            attrs: inLeads
                                ? { disabled: true, "aria-disabled": "true", "aria-label": `«${eventTitle}» уже в покупках` }
                                : { "aria-label": `Добавить «${eventTitle}» в покупки` },
                        }),
                        domEl("button", {
                            className: "listing-btn",
                            type: "button",
                            dataset: { role: "watch" },
                            text: watchText,
                            attrs: watchDisabled
                                ? {
                                    disabled: true,
                                    "aria-disabled": "true",
                                    "aria-label": inLeads
                                        ? `«${eventTitle}» уже в покупках`
                                        : `«${eventTitle}» уже в избранном`,
                                }
                                : { "aria-label": `Добавить «${eventTitle}» в избранное` },
                        }),
                        listingUrl ? domEl("a", {
                            className: "listing-btn listing-btn--kufar",
                            text: "Kufar →",
                            attrs: { href: listingUrl, target: "_blank", rel: "noreferrer noopener" },
                        }) : null,
                    ),
                );

                card.dataset.adId = String(event.ad_id);

                const detailOptions = () => {
                    const tracker = state.trackers.items.find((item) => item.id === event.tracker_id);
                    return {
                        strictSearch: event.strict_mode,
                        category: tracker?.category_id ?? null,
                        referenceContext: "current",
                    };
                };
                const openButton = card.querySelector(".card-open");
                card._preloadDetail = () => actions.preloadListingDetail?.(event, detailOptions());
                eventDetailObserver?.observe(card);
                openButton?.addEventListener("pointerenter", card._preloadDetail);
                openButton?.addEventListener("focus", card._preloadDetail);
                openButton?.addEventListener("pointerdown", card._preloadDetail);
                openButton?.addEventListener("click", () => {
                    void actions.openListingDetail(event, detailOptions());
                });

                card.querySelector('[data-role="lead"]')?.addEventListener("click", (clickEvent) => {
                    clickEvent.stopPropagation();
                    const button = clickEvent.currentTarget;
                    if (button.disabled) return;
                    void Promise.resolve(actions.addLeadFromListing(
                        {
                            ad_id: event.ad_id,
                            title: event.title,
                            link: event.link,
                            price_byn: event.price_byn,
                            thumbnail: event.thumbnail || null,
                            flip_estimates: [],
                        },
                        "tracker_event",
                        event.query
                    )).then((changed) => {
                        if (!changed) return;
                        button.textContent = "В покупках";
                        button.disabled = true;
                        button.setAttribute("aria-disabled", "true");
                        button.setAttribute("aria-label", `«${eventTitle}» уже в покупках`);
                        const watchButton = card.querySelector('[data-role="watch"]');
                        if (watchButton) {
                            watchButton.textContent = "В избранное";
                            watchButton.disabled = true;
                            watchButton.setAttribute("aria-disabled", "true");
                            watchButton.setAttribute("aria-label", `«${eventTitle}» уже в покупках`);
                        }
                    });
                });

                card.querySelector('[data-role="watch"]')?.addEventListener("click", (clickEvent) => {
                    clickEvent.stopPropagation();
                    const button = clickEvent.currentTarget;
                    if (button.disabled) return;
                    void Promise.resolve(actions.addWatchlistFromListing(
                        { ad_id: event.ad_id, title: event.title, link: event.link, price_byn: event.price_byn, thumbnail: event.thumbnail || null },
                        event.query
                    )).then((changed) => {
                        if (!changed) return;
                        button.textContent = "В избранном";
                        button.disabled = true;
                        button.setAttribute("aria-disabled", "true");
                        button.setAttribute("aria-label", `«${eventTitle}» уже в избранном`);
                        // Mutual exclusion: if watchlist accepted, lead button stays enabled
                        // (addWatchlistFromListing already rejects if in leads).
                    });
                });

                return card;
            }

            const list = domEl("div", { className: "tracker-feed-list", attrs: { role: "list" } });
            for (const event of filteredEvents) list.appendChild(buildEventNode(event));
            container.appendChild(list);
        });
    }

    return { renderTrackers, renderTrackerEvents };
}

// OPUS-13: lazy-load registration. The bundle ships a stub for
// createRenderTrackers that delegates through
// window.App._realCreateRenderTrackers once this script lands.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateRenderTrackers = createRenderTrackers;
}
