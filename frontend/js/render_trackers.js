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

            const card = document.createElement("div");
            card.className = `tracker-card-enhanced${tracker.paused ? " paused" : ""}`;
            card.innerHTML = `
                <div class="tracker-header">
                    <div class="tracker-icon">🔍</div>
                    <div class="tracker-title-wrap">
                        <h4 class="tracker-query-title">${escapeHtml(tracker.query)}</h4>
                    </div>
                </div>
                <div class="tracker-filters">
                    <span class="tracker-filter-tag">каждые ${tracker.interval_min} мин</span>
                    ${tracker.strict_mode ? '<span class="tracker-filter-tag">строгий</span>' : ""}
                    ${tracker.min_discount_percent ? `<span class="tracker-filter-tag">от -${Math.round(tracker.min_discount_percent)}%</span>` : ""}
                    ${tracker.max_price_byn ? `<span class="tracker-filter-tag">до ${Math.round(tracker.max_price_byn)} BYN</span>` : ""}
                    ${tracker.seller_type === "Частное лицо" ? '<span class="tracker-filter-tag">частники</span>' : ""}
                    ${tracker.condition ? `<span class="tracker-filter-tag">${escapeHtml(tracker.condition)}</span>` : ""}
                    ${tracker.region_name ? `<span class="tracker-filter-tag">${escapeHtml(tracker.region_name)}</span>` : ""}
                </div>
                <div class="tracker-stats">
                    <div class="tracker-stat">
                        <span class="tracker-stat-label">События</span>
                        <span class="tracker-stat-value highlight">${tracker.event_count || 0}</span>
                    </div>
                    <div class="tracker-stat">
                        <span class="tracker-stat-label">В среднем</span>
                        <span class="tracker-stat-value">${tracker.avg_events_per_day || 0}/день</span>
                    </div>
                    <div class="tracker-stat">
                        <span class="tracker-stat-label">Посл. событие</span>
                        <span class="tracker-stat-value">${formatLastEventTime(tracker.last_event_at)}</span>
                    </div>
                </div>
                ${lastCheckedLabel ? `<div class="tracker-last-checked">🕐 ${escapeHtml(lastCheckedLabel)}</div>` : ""}
                <div class="tracker-card-actions">
                    <button class="ghost-btn small" data-role="${tracker.paused ? "resume" : "pause"}" type="button">
                        ${tracker.paused ? "▶ Возобновить" : "⏸ Пауза"}
                    </button>
                    <button class="ghost-btn small" data-role="edit" type="button">
                        ✏️ Изменить
                    </button>
                    <button class="ghost-btn small danger" data-role="delete" type="button">
                        🗑 Удалить
                    </button>
                </div>
            `;

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

        container.innerHTML = "";

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
            let html = '<option value="">Все трекеры</option>';
            for (const [id, query] of uniqueTrackers) {
                const selected = String(id) === prevValue ? " selected" : "";
                html += `<option value="${id}"${selected}>${escapeHtml(query)}</option>`;
            }
            select.innerHTML = html;
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
            const card = document.createElement("article");
            const isPriceDrop = event.event_type === "price_drop";
            card.className = `tracker-event-card${isPriceDrop ? " price-drop" : ""}`;
            card.innerHTML = `
                <div class="event-header">
                    ${event.thumbnail
                        ? `<img class="event-thumbnail" src="${escapeHtml(event.thumbnail)}" alt="" loading="lazy">`
                        : `<div class="event-thumbnail-placeholder">📱</div>`
                    }
                    <div class="event-body">
                        <div class="event-top-row">
                            <span class="event-type-badge ${isPriceDrop ? "drop" : "new"}">
                                ${isPriceDrop ? "🔽 Падение цены" : "🆕 Новый лот"}
                            </span>
                            <span class="event-time">${formatDate(event.created_at)}</span>
                        </div>
                        <strong class="event-title">${escapeHtml(event.title)}</strong>
                        <div class="event-price-row">
                            <span class="event-price mono">${event.price_byn ? `${Math.round(event.price_byn)} р.` : "без цены"}</span>
                            ${event.delta_byn ? `<span class="event-delta">-${Math.round(event.delta_byn)} р.</span>` : ""}
                        </div>
                        <div class="event-meta">
                            ${event.region_name ? `<span class="event-meta-item">📍 ${escapeHtml(event.region_name)}</span>` : ""}
                            ${event.seller_type ? `<span class="event-meta-item">👤 ${escapeHtml(event.seller_type)}</span>` : ""}
                        </div>
                        <span class="event-tracker-source">🔍 ${escapeHtml(event.query)}</span>
                    </div>
                </div>
                <div class="event-actions">
                    <button class="listing-btn" data-role="open-query" type="button">Открыть</button>
                    <button class="listing-btn" data-role="lead" type="button">В покупки</button>
                    <a class="listing-btn listing-btn--accent" href="${escapeHtml(event.link)}" target="_blank" rel="noreferrer noopener">Kufar →</a>
                </div>
            `;

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
