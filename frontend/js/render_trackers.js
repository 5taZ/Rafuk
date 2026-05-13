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
        optimizedImage,
        safeRender: safeRender,
        // OPUS-13 wave 73: ``createVirtualList`` is injected through
        // context by app_renderers.js so this lazy-loaded module
        // doesn't have to reach outside its scope for it. Before the
        // fix it relied on a bundle-IIFE-local symbol that the lazy
        // <script> couldn't see — ReferenceError on first tracker
        // list render past the virtual-list threshold.
        createVirtualList,
    } = context;

    // FE-05 / Wave 29: emoji-prefixed labels need a tiny shim so screen
    // readers don't pronounce the emoji glyph (Unicode names like
    // "BACKHAND INDEX POINTING DOWN" are unreadable as price-drop
    // markers). Wrap the emoji in an aria-hidden span and let the
    // following text node carry the meaningful label.
    function emojiSpan(emoji) {
        const span = document.createElement("span");
        span.setAttribute("aria-hidden", "true");
        span.textContent = emoji;
        return span;
    }

    function emojiLabel(className, emoji, text) {
        // Returns a span whose visible content is "📍 Минск" but whose
        // accessible name reads as plain "Минск". Used for chip-style
        // labels in tracker event cards.
        return domEl("span", { className }, emojiSpan(emoji), ` ${text}`);
    }

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

    // Common attribute presets for the action / search icons.
    const _STROKE_ATTRS = {
        fill: "none",
        stroke: "currentColor",
        "stroke-width": "2",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
    };

    function iconPlay() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, fill: "currentColor" },
            [{ tag: "path", d: "M8 5v14l11-7z" }]);
    }
    function iconPause() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, fill: "currentColor" },
            [{ tag: "path", d: "M6 4h4v16H6zM14 4h4v16h-4z" }]);
    }
    function iconEdit() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, ..._STROKE_ATTRS },
            [
                { tag: "path", d: "M12 20h9" },
                { tag: "path", d: "M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4z" },
            ]);
    }
    function iconDelete() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, ..._STROKE_ATTRS },
            [
                { tag: "polyline", points: "3 6 5 6 21 6" },
                { tag: "path", d: "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" },
                { tag: "path", d: "M10 11v6M14 11v6" },
                { tag: "path", d: "M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" },
            ]);
    }
    function iconSearch() {
        return buildSvgIcon("0 0 24 24",
            { width: 16, height: 16, ..._STROKE_ATTRS },
            [
                { tag: "circle", cx: "11", cy: "11", r: "7" },
                { tag: "line", x1: "21", y1: "21", x2: "16.65", y2: "16.65" },
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

    /* ===== Tracker Status ===== */

    function renderTrackerStatus() {
        return safeRender('renderTrackerStatus', () => {
            const message = typeof state.trackers.status === "string" ? state.trackers.status.trim() : "";
        if (!message) {
            elements.trackerStatus.hidden = true;
            elements.trackerStatus.textContent = "";
            elements.trackerStatus.className = "tracker-status";
            return;
        }

        elements.trackerStatus.textContent = message;
        elements.trackerStatus.className = `tracker-status is-visible ${state.trackers.statusKind}`;
        elements.trackerStatus.hidden = false;
        });
    }

    /* ===== Watchlist Filters ===== */

    function renderWatchlistFilters() {
        for (const button of elements.watchlistFilterButtons || []) {
            button.classList.toggle("active", button.dataset.watchFilter === state.watchlist.filter);
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

        const buildEmpty = context.buildEmptyState;
        if (!hasTelegramInitData()) {
            if (typeof buildEmpty === "function") {
                elements.trackersList.appendChild(
                    buildEmpty({
                        title: "Открывайте Mini App в Telegram",
                        hint: "Автопоиск работает с Telegram-аккаунтом — только так трекер сможет прислать уведомление о новых лотах.",
                    })
                );
            } else {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = "Откройте Mini App внутри Telegram, чтобы управлять трекерами.";
                elements.trackersList.appendChild(note);
            }
            return;
        }

        // Show skeleton cards while loading
        if (state.trackers._loading) {
            for (let i = 0; i < 3; i++) {
                const skel = document.createElement("div");
                skel.className = "skeleton-card";
                skel.setAttribute("aria-hidden", "true");
                // FE-08: build children via DOM API instead of innerHTML.
                for (const width of ["60%", "40%", "30%"]) {
                    const bar = document.createElement("div");
                    bar.className = "skel-bar";
                    bar.style.width = width;
                    skel.appendChild(bar);
                }
                elements.trackersList.appendChild(skel);
            }
            return;
        }

        if (!state.trackers.items.length) {
            if (typeof buildEmpty === "function") {
                elements.trackersList.appendChild(
                    buildEmpty({
                        title: "Создайте первый автопоиск",
                        hint: "Сохраните любой запрос как трекер — и Telegram пришлёт уведомление, когда появятся новые объявления или цена пойдёт вниз.",
                        actionLabel: "Создать автопоиск",
                        onAction: () => {
                            const input = document.querySelector(".tracker-input");
                            if (input) input.focus();
                        },
                    })
                );
            } else {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = "Активных трекеров пока нет.";
                elements.trackersList.appendChild(note);
            }
            return;
        }

        for (const tracker of state.trackers.items) {
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
            if (tracker.category_id) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: tracker.category_label || `категория ${tracker.category_id}` }));
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
            const actionLabel = tracker.paused ? "Возобновить" : "Пауза";
            // FE-08: SVG icons constructed via createElementNS (see
            // helpers at top of module). Picked at render time so the
            // button gets a clean monochrome glyph instead of a
            // platform-specific emoji.
            const actionIconNode = tracker.paused ? iconPlay() : iconPause();

            const buildIconButton = (className, role, label, iconNode) => {
                const btn = domEl(
                    "button",
                    { className, type: "button", dataset: { role } },
                );
                const icon = document.createElement("span");
                icon.className = "tracker-action-icon";
                icon.appendChild(iconNode);
                btn.append(icon, document.createTextNode(label));
                return btn;
            };
            const card = domEl(
                "div",
                { className: `tracker-card-enhanced${tracker.paused ? " paused" : ""}` },
                domEl(
                    "div",
                    { className: "tracker-header" },
                    (() => {
                        const iconWrap = document.createElement("div");
                        iconWrap.className = "tracker-icon";
                        // FE-08: see iconSearch() at top of module.
                        iconWrap.appendChild(iconSearch());
                        return iconWrap;
                    })(),
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
                lastCheckedLabel ? emojiLabel("tracker-last-checked", "🕐", lastCheckedLabel) : null,
                domEl(
                    "div",
                    { className: "tracker-card-actions" },
                    buildIconButton("ghost-btn small tracker-action-btn", "open", "Открыть", iconSearch()),
                    buildIconButton("ghost-btn small tracker-action-btn", actionRole, actionLabel, actionIconNode),
                    buildIconButton("ghost-btn small tracker-action-btn", "edit", "Изменить", iconEdit()),
                    buildIconButton("ghost-btn small tracker-action-btn danger", "delete", "Удалить", iconDelete()),
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
                state.trackers.events = state.trackers.events || [];
                state.trackers.eventFilterTrackerId = tracker.id;
                if (context._hooks?.renderTrackerEvents) context._hooks.renderTrackerEvents();
            });
            card.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteTracker(tracker.id);
            });
            card.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                elements.searchInput.value = tracker.query;
                state.search.query = tracker.query;
                state.search.strictSearch = Boolean(tracker.strict_mode);
                state.filters.category = tracker.category_id ?? null;
                state.filters.pendingCategory = tracker.category_id ?? null;
                if (tracker.category_id && tracker.category_label && !state.filters.categories.some((cat) => Number(cat.id) === Number(tracker.category_id))) {
                    state.filters.categories = [
                        { id: tracker.category_id, label: tracker.category_label, count: 0 },
                        ...state.filters.categories,
                    ];
                }
                state.trackers.minDiscountPercent = Math.round(tracker.min_discount_percent || 10);
                state.trackers.maxPriceByn = tracker.max_price_byn ?? null;
                state.trackers.sellerType = tracker.seller_type || "";
                state.trackers.condition = tracker.condition || "";
                state.trackers.regionName = tracker.region_name || "";
                state.trackers.configKeyword = tracker.config_keyword || "";
                if (context._hooks?.renderStrictSearch) context._hooks.renderStrictSearch();
                if (context._hooks?.renderFilterDropdown) context._hooks.renderFilterDropdown();
                if (context._hooks?.renderTrackerInputs) context._hooks.renderTrackerInputs();
                if (context._hooks?.renderLoading) context._hooks.renderLoading();
                void actions.search("overview", { keepFilters: true });
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

        const trackerScopedEvents = state.trackers.eventFilterTrackerId
            ? state.trackers.events.filter((event) => event.tracker_id === state.trackers.eventFilterTrackerId)
            : state.trackers.events.slice();

        let dropCount = 0, newCount = 0, thresholdCount = 0, discountAlertCount = 0;
        for (const e of trackerScopedEvents) {
            if (e.event_type === "price_drop") dropCount++;
            else if (e.event_type === "new_listing") newCount++;
            else if (e.event_type === "price_threshold_alert") thresholdCount++;
            else if (e.event_type === "discount_alert") discountAlertCount++;
        }
        const totalCount = trackerScopedEvents.length;

        if (elements.trackerEventsBadge) {
            elements.trackerEventsBadge.textContent = totalCount > 0 ? `${totalCount} событий` : "чат + Mini App";
        }

        const FILTER_LABELS = {
            all: "Все",
            price_drop: "Упали в цене",
            new_listing: "Новые лоты",
            price_threshold_alert: "Порог цены",
            discount_alert: "Скидка",
        };
        const FILTER_COUNTS = {
            all: totalCount,
            price_drop: dropCount,
            new_listing: newCount,
            price_threshold_alert: thresholdCount,
            discount_alert: discountAlertCount,
        };
        for (const button of elements.trackerEventFilterButtons) {
            const filter = button.dataset.eventFilter;
            const count = FILTER_COUNTS[filter] ?? 0;
            const label = FILTER_LABELS[filter] ?? filter;
            button.textContent = count > 0 ? `${label} (${count})` : label;
            button.classList.toggle("active", filter === state.trackers.eventFilter);
        }

        // Populate tracker dropdown filter
        if (elements.trackerEventTrackerSelect) {
            const select = elements.trackerEventTrackerSelect;
            const prevValue = select.value;
            const uniqueTrackers = new Map();
            for (const evt of state.trackers.events) {
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
            select.value = state.trackers.eventFilterTrackerId || "";
        }

        // Filter events by selected tracker first, then by event type
        let filteredEvents = trackerScopedEvents.filter((event) => {
            if (state.trackers.eventFilter === "all") {
                return true;
            }
            return event.event_type === state.trackers.eventFilter;
        });

        if (!filteredEvents.length) {
            const buildEmpty = context.buildEmptyState;
            let title;
            let hint;
            if (state.trackers.eventFilterTrackerId) {
                const tracker = state.trackers.items.find((t) => t.id === state.trackers.eventFilterTrackerId);
                title = tracker ? `Тихо по запросу "${tracker.query}"` : "Тихо по этому трекеру";
                hint = "Дайте трекеру несколько часов — Kufar обновляется неравномерно.";
            } else if (state.trackers.eventFilter === "all") {
                title = "Событий пока нет";
                hint = "Они появятся после первой проверки планировщика. Свежие лоты и падения цен прилетят в этот раздел и в чат бота.";
            } else {
                title = "По этому фильтру пусто";
                hint = "Переключитесь на «Все», чтобы увидеть остальные сигналы.";
            }
            if (typeof buildEmpty === "function") {
                container.appendChild(buildEmpty({ title, hint }));
            } else {
                const note = document.createElement("p");
                note.className = "tracker-event-empty";
                note.textContent = `${title}. ${hint}`;
                container.appendChild(note);
            }
            return;
        }

        // Build event card element — extracted for virtual scrolling
        function buildEventNode(event) {
            const isPriceDrop = event.event_type === "price_drop";
            const thumbSrc = (() => {
                const validated = safeUrl(event.thumbnail);
                if (!validated) return "";
                return typeof optimizedImage === "function"
                    ? optimizedImage(validated, { width: 200 })
                    : validated;
            })();
            const thumbnailNode = thumbSrc
                ? domEl("img", {
                    className: "event-thumbnail",
                    attrs: { src: thumbSrc, alt: event.title || "Объявление", loading: "lazy" },
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
            const eventMeta = domEl("div", { className: "event-meta" });
            if (event.region_name) eventMeta.appendChild(emojiLabel("event-meta-item", "📍", event.region_name));
            if (event.seller_type) eventMeta.appendChild(emojiLabel("event-meta-item", "👤", event.seller_type));
            const priceRow = domEl(
                "div",
                { className: "event-price-row" },
                domEl("span", { className: "event-price mono", text: event.price_byn ? `${Math.round(event.price_byn)} р.` : "без цены" }),
            );
            if (event.delta_byn && event.event_type === "price_drop") {
                priceRow.appendChild(domEl("span", { className: "event-delta", text: `-${Math.round(event.delta_byn)} р.` }));
            }
            if (event.event_type === "price_threshold_alert" && event.parameters?.threshold) {
                priceRow.appendChild(domEl("span", { className: "event-delta event-delta--alert", text: `порог: ${Math.round(event.parameters.threshold)} р.` }));
            }
            if (event.event_type === "discount_alert" && event.parameters?.discount_percent) {
                priceRow.appendChild(domEl("span", { className: "event-delta event-delta--alert", text: `-${Math.round(event.parameters.discount_percent)}% от медианы` }));
            }

            // FE-05: badges carry both an emoji and a text label; the
            // emoji is decorative duplication of the label, so it goes
            // into an aria-hidden span and screen readers announce
            // only the meaningful suffix (e.g. "Новый лот").
            let badgeClass = "new";
            let badgeEmoji = "🆕";
            let badgeLabel = "Новый лот";
            let cardModifier = "";
            if (isPriceDrop) {
                badgeClass = "drop";
                badgeEmoji = "🔽";
                badgeLabel = "Падение цены";
                cardModifier = " price-drop";
            }
            if (event.event_type === "price_threshold_alert") {
                badgeClass = "alert";
                badgeEmoji = "🎯";
                badgeLabel = "Порог цены";
                cardModifier = " threshold-alert";
            }
            if (event.event_type === "discount_alert") {
                badgeClass = "alert";
                badgeEmoji = "📉";
                badgeLabel = "Скидка от медианы";
                cardModifier = " discount-alert";
            }

            const card = domEl(
                "article",
                { className: `tracker-event-card${cardModifier}` },
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
                            emojiLabel(`event-type-badge ${badgeClass}`, badgeEmoji, badgeLabel),
                            domEl("span", { className: "event-time", text: formatDate(event.created_at) }),
                        ),
                        domEl("strong", { className: "event-title", text: event.title }),
                        priceRow,
                        eventMeta,
                        domEl("span", { className: "event-tracker-source" },
                            domEl("span", { attrs: { "aria-hidden": "true" }, text: "🔍 " }),
                            event.query,
                        ),
                    ),
                ),
                domEl(
                    "div",
                    { className: "event-actions" },
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "open-query" }, text: "Открыть" }),
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "lead" }, text: "В покупки" }),
                    domEl("a", {
                        className: "listing-btn listing-btn--kufar",
                        text: "Kufar →",
                        attrs: { href: safeUrl(event.link), target: "_blank", rel: "noreferrer noopener" },
                    }),
                ),
            );

            card.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
                if (event.query) {
                    elements.searchInput.value = event.query;
                    state.search.query = event.query;
                }
                state.search.strictSearch = Boolean(event.strict_mode);
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
        // Tally events by event_type so each filter chip can show how
        // many alerts it represents — gives the user a sense of where
        // the action is before they tap. "all" mirrors the total.
        const events = state.trackers.events || [];
        const total = events.length;
        let priceDrops = 0;
        let newListings = 0;
        let thresholdAlerts = 0;
        let discountAlerts = 0;
        for (const event of events) {
            if (event?.event_type === "price_drop") priceDrops += 1;
            else if (event?.event_type === "new_listing") newListings += 1;
            else if (event?.event_type === "price_threshold_alert") thresholdAlerts += 1;
            else if (event?.event_type === "discount_alert") discountAlerts += 1;
        }
        const counts = {
            all: total,
            price_drop: priceDrops,
            new_listing: newListings,
            price_threshold_alert: thresholdAlerts,
            discount_alert: discountAlerts,
        };

        for (const button of elements.trackerEventFilterButtons) {
            button.classList.toggle("active", button.dataset.eventFilter === state.trackers.eventFilter);
        }
        const badges = elements.trackerEventFilterCounts || {};
        for (const [key, badge] of Object.entries(badges)) {
            if (!badge) continue;
            const value = counts[key] ?? 0;
            badge.textContent = String(value);
            badge.hidden = value === 0;
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

// OPUS-13: lazy-load registration. The bundle ships a stub for
// createRenderTrackers that delegates through
// window.App._realCreateRenderTrackers once this script lands.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateRenderTrackers = createRenderTrackers;
}
