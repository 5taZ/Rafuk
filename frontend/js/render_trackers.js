/**
 * render_trackers.js — Tracker status, tracker events, tracker filters,
 * tracker management UI, watchlist filters.
 */

function createRenderTrackers(context) {
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
        safeUrl: safeUrl,
        safeRender: safeRender,
    } = context;

    /* ===== Tracker Status ===== */

    function renderTrackerStatus() {
        return safeRender('renderTrackerStatus', () => {
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
        });
    }

    /* ===== Watchlist Filters ===== */

    function renderWatchlistFilters() {
        for (const button of elements.watchlistFilterButtons || []) {
            button.classList.toggle("active", button.dataset.watchFilter === state.watchlistFilter);
        }
    }

    /* ===== Tracker Cards ===== */

    /**
     * Format a relative time from an ISO date string for display.
     */
    function formatLastEventTime(isoDate) {
        if (!isoDate) return "нет";
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return "нет";
        const diffMs = Math.max(0, Date.now() - date.getTime());
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return "только что";
        if (diffMin < 60) return `${diffMin} мин`;
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) return `${diffHr} ч`;
        const diffDays = Math.floor(diffHr / 24);
        return `${diffDays} д`;
    }

    function renderTrackers() {
        return safeRender('renderTrackers', () => {
        domClear(elements.trackersList);

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
            let lastCheckedLabel = "";
            if (tracker.last_checked_at) {
                const checkedDate = new Date(tracker.last_checked_at);
                if (!isNaN(checkedDate.getTime())) {
                    const diffMs = Math.max(0, Date.now() - checkedDate.getTime());
                    const diffMin = Math.floor(diffMs / 60000);
                    if (diffMin < 1) {
                        lastCheckedLabel = "проверено только что";
                    } else if (diffMin < 60) {
                        lastCheckedLabel = `${diffMin} мин назад`;
                    } else {
                        const diffHr = Math.floor(diffMin / 60);
                        lastCheckedLabel = `${diffHr} ч назад`;
                    }
                }
            }

            const trackerFilters = domEl(
                "div",
                { className: "tracker-filters" },
                domEl("span", { className: "tracker-filter-tag", text: `каждые ${tracker.interval_min} мин` }),
            );
            if (tracker.strict_mode) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: "строгий" }));
            if (tracker.min_discount_percent) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: `от -${Math.round(tracker.min_discount_percent)}%` }));
            if (tracker.max_price_byn) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: `до ${Math.round(tracker.max_price_byn)} BYN` }));
            if (tracker.seller_type === "Частное лицо") trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: "частники" }));
            if (tracker.condition) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: tracker.condition }));
            if (tracker.region_name) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: tracker.region_name }));

            const buildStat = (label, value, className) => domEl(
                "div",
                { className: "tracker-stat" },
                domEl("span", { className: "tracker-stat-label", text: label }),
                domEl("span", { className: `tracker-stat-value${className ? ` ${className}` : ""}`.trim(), text: value }),
            );

            const actionRole = tracker.paused ? "resume" : "pause";
            const actionText = tracker.paused ? "▶ Возобновить" : "⏸ Пауза";
            const card = domEl(
                "div",
                { className: `tracker-card-enhanced${tracker.paused ? " paused" : ""}` },
                domEl(
                    "div",
                    { className: "tracker-header" },
                    domEl("div", { className: "tracker-icon", text: "🔍" }),
                    domEl(
                        "div",
                        { className: "tracker-title-wrap" },
                        domEl("h4", { className: "tracker-query-title", text: tracker.query }),
                    ),
                ),
                trackerFilters,
                domEl(
                    "div",
                    { className: "tracker-stats" },
                    buildStat("События", tracker.event_count || 0, "highlight"),
                    buildStat("В среднем", `${tracker.avg_events_per_day || 0}/день`),
                    buildStat("Посл. событие", formatLastEventTime(tracker.last_event_at)),
                ),
                lastCheckedLabel ? domEl("div", { className: "tracker-last-checked", text: `🕐 ${lastCheckedLabel}` }) : null,
                domEl(
                    "div",
                    { className: "tracker-card-actions" },
                    domEl("button", { className: "ghost-btn small", type: "button", dataset: { role: actionRole }, text: actionText }),
                    domEl("button", { className: "ghost-btn small", type: "button", dataset: { role: "edit" }, text: "✏️ Изменить" }),
                    domEl("button", { className: "ghost-btn small danger", type: "button", dataset: { role: "delete" }, text: "🗑 Удалить" }),
                ),
            );

            card.querySelector('[data-role="pause"]')?.addEventListener("click", () => {
                void actions.pauseTracker(tracker.id);
            });
            card.querySelector('[data-role="resume"]')?.addEventListener("click", () => {
                void actions.resumeTracker(tracker.id);
            });
            card.querySelector('[data-role="edit"]')?.addEventListener("click", () => {
                actions.openEditTracker(tracker.id);
            });
            card.querySelector('[data-role="view-events"]')?.addEventListener("click", () => {
                state.trackerEvents = state.trackerEvents || [];
                state.trackerEventFilterTrackerId = tracker.id;
                if (context._hooks?.renderTrackerEvents) context._hooks.renderTrackerEvents();
            });
            card.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteTracker(tracker.id);
            });
            card.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                if (context._hooks?.showToast) context._hooks.showToast("Загружаю...");
                elements.searchInput.value = tracker.query;
                state.query = tracker.query;
                state.strictSearch = Boolean(tracker.strict_mode);
                state.trackerMinDiscountPercent = Math.round(tracker.min_discount_percent || 10);
                state.trackerMaxPriceByn = tracker.max_price_byn ?? null;
                state.trackerSellerType = tracker.seller_type || "";
                state.trackerCondition = tracker.condition || "";
                state.trackerRegionName = tracker.region_name || "";
                state.trackerConfigKeyword = tracker.config_keyword || "";
                if (context._hooks?.renderStrictSearch) context._hooks.renderStrictSearch();
                if (context._hooks?.renderTrackerInputs) context._hooks.renderTrackerInputs();
                if (context._hooks?.renderLoading) context._hooks.renderLoading();
                void actions.search();
            });
            elements.trackersList.appendChild(card);
        }
        });
    }

    /* ===== Tracker Events ===== */

    function renderTrackerEvents() {
        return safeRender('renderTrackerEvents', () => {
        const container = elements.trackerEventsList;
        if (!container) return;

        // Destroy existing virtual list if present and reset container
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        container.style.overflowY = "";
        container.style.maxHeight = "";

        domClear(container);

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-event-empty";
            note.textContent = "Откройте Mini App внутри Telegram, чтобы видеть события.";
            container.appendChild(note);
            return;
        }

        const trackerScopedEvents = state.trackerEventFilterTrackerId
            ? state.trackerEvents.filter((event) => event.tracker_id === state.trackerEventFilterTrackerId)
            : state.trackerEvents.slice();

        const dropCount = trackerScopedEvents.filter((e) => e.event_type === "price_drop").length;
        const newCount = trackerScopedEvents.filter((e) => e.event_type === "new_listing").length;
        const totalCount = trackerScopedEvents.length;

        if (elements.trackerEventsBadge) {
            elements.trackerEventsBadge.textContent = totalCount > 0 ? `${totalCount} событий` : "чат + Mini App";
        }

        // Update filter button labels and active states
        for (const button of elements.trackerEventFilterButtons) {
            const filter = button.dataset.eventFilter;
            const count = filter === "all" ? totalCount : filter === "price_drop" ? dropCount : newCount;
            const label = filter === "all" ? "Все" : filter === "price_drop" ? "Упали в цене" : "Новые лоты";
            button.textContent = count > 0 ? `${label} (${count})` : label;
            button.classList.toggle("active", filter === state.trackerEventFilter);
        }

        // Populate tracker dropdown filter
        if (elements.trackerEventTrackerSelect) {
            const select = elements.trackerEventTrackerSelect;
            const prevValue = select.value;
            const uniqueTrackers = new Map();
            for (const evt of state.trackerEvents) {
                if (!uniqueTrackers.has(evt.tracker_id)) {
                    uniqueTrackers.set(evt.tracker_id, evt.query);
                }
            }
            const options = [domEl("option", { value: "", text: "Все трекеры" })];
            for (const [id, query] of uniqueTrackers) {
                const option = domEl("option", { value: id, text: query });
                if (String(id) === prevValue) option.selected = true;
                options.push(option);
            }
            select.replaceChildren(...options);
            // Restore selection from state
            select.value = state.trackerEventFilterTrackerId || "";
        }

        // Filter events by selected tracker first, then by event type
        let filteredEvents = trackerScopedEvents.filter((event) => {
            if (state.trackerEventFilter === "all") {
                return true;
            }
            return event.event_type === state.trackerEventFilter;
        });

        if (!filteredEvents.length) {
            const note = document.createElement("p");
            note.className = "tracker-event-empty";
            if (state.trackerEventFilterTrackerId) {
                const tracker = state.trackers.find((t) => t.id === state.trackerEventFilterTrackerId);
                note.textContent = tracker
                    ? `Нет событий для "${tracker.query}".`
                    : "По этому фильтру событий пока нет.";
            } else if (state.trackerEventFilter === "all") {
                note.textContent = "Событий пока нет. Они появятся после первой проверки планировщика.";
            } else {
                note.textContent = "По этому фильтру событий пока нет.";
            }
            container.appendChild(note);
            return;
        }

        // Build event card element — extracted for virtual scrolling
        function buildEventNode(event) {
            const isPriceDrop = event.event_type === "price_drop";
            const thumbnailNode = event.thumbnail
                ? domEl("img", {
                    className: "event-thumbnail",
                    attrs: { src: safeUrl(event.thumbnail), alt: "", loading: "lazy" },
                })
                : domEl("div", { className: "event-thumbnail-placeholder", text: "📱" });
            const eventMeta = domEl("div", { className: "event-meta" });
            if (event.region_name) eventMeta.appendChild(domEl("span", { className: "event-meta-item", text: `📍 ${event.region_name}` }));
            if (event.seller_type) eventMeta.appendChild(domEl("span", { className: "event-meta-item", text: `👤 ${event.seller_type}` }));
            const priceRow = domEl(
                "div",
                { className: "event-price-row" },
                domEl("span", { className: "event-price mono", text: event.price_byn ? `${Math.round(event.price_byn)} р.` : "без цены" }),
            );
            if (event.delta_byn) priceRow.appendChild(domEl("span", { className: "event-delta", text: `-${Math.round(event.delta_byn)} р.` }));
            const card = domEl(
                "article",
                { className: `tracker-event-card${isPriceDrop ? " price-drop" : ""}` },
                domEl(
                    "div",
                    { className: "event-header" },
                    thumbnailNode,
                    domEl(
                        "div",
                        { className: "event-body" },
                        domEl(
                            "div",
                            { className: "event-top-row" },
                            domEl(
                                "span",
                                {
                                    className: `event-type-badge ${isPriceDrop ? "drop" : "new"}`,
                                    text: isPriceDrop ? "🔽 Падение цены" : "🆕 Новый лот",
                                },
                            ),
                            domEl("span", { className: "event-time", text: formatDate(event.created_at) }),
                        ),
                        domEl("strong", { className: "event-title", text: event.title }),
                        priceRow,
                        eventMeta,
                        domEl("span", { className: "event-tracker-source", text: `🔍 ${event.query}` }),
                    ),
                ),
                domEl(
                    "div",
                    { className: "event-actions" },
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "open-query" }, text: "Открыть" }),
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "lead" }, text: "В покупки" }),
                    domEl("a", {
                        className: "listing-btn listing-btn--accent",
                        text: "Kufar →",
                        attrs: { href: safeUrl(event.link), target: "_blank", rel: "noreferrer noopener" },
                    }),
                ),
            );

            card.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
                if (event.query) {
                    elements.searchInput.value = event.query;
                    state.query = event.query;
                }
                state.strictSearch = Boolean(event.strict_mode);
                if (context._hooks?.renderStrictSearch) context._hooks.renderStrictSearch();
                void actions.openListingDetail({
                    ad_id: event.ad_id,
                    title: event.title,
                    link: event.link,
                    price_byn: event.price_byn,
                });
            });
            card.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
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
            return card;
        }

        const VIRTUAL_LIST_THRESHOLD = 50;
        if (filteredEvents.length > VIRTUAL_LIST_THRESHOLD) {
            container._virtualList = createVirtualList(container, {
                itemHeight: 160,
                bufferSize: 5,
                renderFn: (event, index) => buildEventNode(event),
            });
            container._virtualList.setItems(filteredEvents);
        } else {
            for (const event of filteredEvents) {
                container.appendChild(buildEventNode(event));
            }
        }
        });
    }

    /* ===== Tracker Event Filter Buttons (standalone call) ===== */

    function renderTrackerEventFilters() {
        for (const button of elements.trackerEventFilterButtons) {
            button.classList.toggle("active", button.dataset.eventFilter === state.trackerEventFilter);
        }
    }

    return {
        renderTrackerStatus,
        renderWatchlistFilters,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
    };
}
