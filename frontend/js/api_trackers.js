/**
 * Tracker setup, lifecycle and feed requests.
 */
function createApiTrackers(context) {
    const {
        state, elements, hasTelegramInitData, renderTrackers, renderTrackerEvents,
        showToast, getJson, postJson, deleteJson, requestJson, domEl,
        openModalAnimated, closeModalAnimated, confirmAction,
    } = context;
    const tracking = state.trackers;
    let refreshTimer = null;
    let feedRequest = null;
    let feedScope = null;
    let categoriesRequest = null;
    let formBusy = false;
    const minRefreshDelay = 30_000;
    const maxRefreshDelay = 120_000;
    let trackerRefreshDelay = minRefreshDelay;
    let feedRefreshDelay = minRefreshDelay;
    let nextTrackerRefresh = 0;
    let nextFeedRefresh = 0;

    function stopTrackerRefresh() {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }

    function startTrackerRefresh() {
        stopTrackerRefresh();
        if (!hasTelegramInitData()) return;
        trackerRefreshDelay = feedRefreshDelay = minRefreshDelay;
        nextTrackerRefresh = nextFeedRefresh = 0;
        refreshTimer = setInterval(() => {
            if (state.ui.activeView !== "tracking" || document.hidden) return;
            const now = Date.now();
            if (tracking.pane === "events") {
                if (now >= nextFeedRefresh) void refreshTrackerEvents({ background: true });
            } else if (now >= nextTrackerRefresh) {
                void loadTrackers({ skipIfLoading: true, background: true });
            }
        }, minRefreshDelay);
    }

    async function loadTrackers({ skipIfLoading = false, background = false } = {}) {
        if (!hasTelegramInitData()) {
            renderTrackers();
            return;
        }
        if (skipIfLoading && tracking._loading) return;
        const requestId = ++tracking._requestId;
        tracking._loading = true;
        if (!tracking.items.length && !background) renderTrackers();
        let changed = !background;
        try {
            const items = await getJson("/api/v1/trackers");
            if (requestId !== tracking._requestId) return;
            const signature = (rows) => JSON.stringify(rows, (key, value) =>
                key === "avg_events_per_day" ? undefined : value);
            const dataChanged = signature(items) !== signature(tracking.items);
            changed ||= Boolean(tracking.loadError) || dataChanged;
            trackerRefreshDelay = background && !dataChanged
                ? Math.min(trackerRefreshDelay * 2, maxRefreshDelay) : minRefreshDelay;
            nextTrackerRefresh = Date.now() + trackerRefreshDelay;
            tracking.items = items;
            tracking.loadError = null;
        } catch (error) {
            if (requestId !== tracking._requestId) return;
            trackerRefreshDelay = Math.min(trackerRefreshDelay * 2, maxRefreshDelay);
            nextTrackerRefresh = Date.now() + trackerRefreshDelay;
            if (error.sessionExpired || error.accountDeleted) stopTrackerRefresh();
            if (!background || !tracking.items.length) {
                tracking.loadError = "Не удалось загрузить трекеры.";
                changed = true;
            }
        } finally {
            if (requestId === tracking._requestId) {
                tracking._loading = false;
                if (changed) renderTrackers();
            }
        }
    }

    async function refreshTrackerEvents({ background = false } = {}) {
        if (!hasTelegramInitData()) {
            renderTrackerEvents();
            return;
        }
        if (tracking.clearingEvents || (background && feedRequest)) return;
        const params = new URLSearchParams({ limit: String(tracking.eventLimit) });
        if (tracking.eventFilterTrackerId != null) params.set("tracker_id", tracking.eventFilterTrackerId);
        if (tracking.eventFilter !== "all") params.set("event_type", tracking.eventFilter);
        const scope = params.toString();
        if (feedRequest && feedScope === scope && tracking.eventsLoading) return;
        if (scope !== feedScope) {
            tracking.events = [];
            tracking.eventsError = null;
            feedScope = scope;
        }
        feedRequest?.abort();
        const controller = new AbortController();
        feedRequest = controller;
        let changed = !background;
        if (!background) {
            tracking.eventsLoading = true;
            tracking.eventsError = null;
            renderTrackerEvents();
        }

        try {
            const events = await getJson(`/api/v1/tracker-events?${scope}`, { signal: controller.signal });
            if (controller.signal.aborted) return;
            const dataChanged = JSON.stringify(events) !== JSON.stringify(tracking.events);
            changed ||= Boolean(tracking.eventsError) || dataChanged;
            feedRefreshDelay = background && !dataChanged
                ? Math.min(feedRefreshDelay * 2, maxRefreshDelay) : minRefreshDelay;
            nextFeedRefresh = Date.now() + feedRefreshDelay;
            tracking.events = events;
            for (const event of events) tracking.eventSources.set(event.tracker_id, event.query);
            tracking.eventsError = null;
        } catch (error) {
            if (controller.signal.aborted) return;
            feedRefreshDelay = Math.min(feedRefreshDelay * 2, maxRefreshDelay);
            nextFeedRefresh = Date.now() + feedRefreshDelay;
            if (error.sessionExpired || error.accountDeleted) stopTrackerRefresh();
            if (!background || !tracking.events.length) {
                tracking.eventsError = "Не удалось загрузить ленту.";
                changed = true;
            }
        } finally {
            if (feedRequest === controller) {
                feedRequest = null;
                tracking.eventsLoading = false;
                if (changed) renderTrackerEvents();
            }
        }
    }

    async function clearTrackerEvents() {
        if (tracking.clearingEvents || tracking.eventFilterTrackerId != null || tracking.eventFilter !== "all") return;
        if (!await confirmAction({
            title: "Очистить всю ленту?",
            message: "Все события автопоиска будут удалены. Трекеры продолжат работать и добавлять новые события.",
            confirmLabel: "Очистить ленту",
            destructive: true,
        })) return;
        // UI-08: recheck the scope after the dialog; filters may have changed while it was open.
        if (tracking.clearingEvents || tracking.eventFilterTrackerId != null || tracking.eventFilter !== "all") return;
        tracking.clearingEvents = true;
        renderTrackerEvents();
        try {
            await deleteJson("/api/v1/tracker-events");
            feedRequest?.abort();
            feedRequest = null;
            tracking.eventsLoading = false;
            tracking.eventsError = null;
            tracking.events = [];
            tracking.eventSources.clear();
            showToast("Лента очищена", "success");
        } catch (error) {
            showToast(error.message || "Не удалось очистить ленту", "error");
        } finally {
            tracking.clearingEvents = false;
            await Promise.all([loadTrackers(), refreshTrackerEvents()]);
        }
    }

    const filterKey = (value) => String(value ?? "").trim().toLocaleLowerCase("ru-RU").replace(/\s+/g, " ");
    const sellerLabel = { private: "Частное лицо", "частное лицо": "Частное лицо", "частник": "Частное лицо", shop: "Магазин", "магазин": "Магазин" };
    const conditionLabel = { new: "Новый", "новый": "Новый", used: "Б/у", "б/у": "Б/у" };
    const displayFilter = (value, labels) => (labels && Object.hasOwn(labels, filterKey(value)) ? labels[filterKey(value)] : value) || "";

    function setSelect(select, value, labels) {
        select.querySelector("[data-legacy-option]")?.remove();
        const label = displayFilter(value, labels);
        if (label && !Array.from(select.options).some((option) => option.value === label)) {
            select.appendChild(domEl("option", { value: label, text: label, dataset: { legacyOption: "true" } }));
        }
        select.value = label;
    }

    function setFormError(message = "") {
        elements.trackerFormError.textContent = message;
        elements.trackerFormError.hidden = !message;
    }

    function setCategoryStatus(message = "") {
        elements.editCategoryStatus.textContent = message;
        elements.editCategoryStatus.hidden = !message;
    }

    function showCategories(categories = [], selected = null) {
        const options = [domEl("option", { value: "", text: "Любая категория" })];
        const seen = new Set();
        for (const category of categories) {
            const id = Number(category.id);
            if (!Number.isSafeInteger(id) || id < 1 || seen.has(id)) continue;
            seen.add(id);
            options.push(domEl("option", { value: id, text: category.label || `Категория ${id}` }));
        }
        elements.editCategorySelect.replaceChildren(...options);
        elements.editCategorySelect.value = String(selected ?? "");
    }

    function cancelCategoryRequest() {
        categoriesRequest?.abort();
        categoriesRequest = null;
        elements.trackerLoadCategories.disabled = false;
        setCategoryStatus();
    }

    async function loadTrackerCategories() {
        const query = elements.editTrackerQuery.value.trim();
        if (!query) {
            setCategoryStatus("Сначала введите запрос.");
            elements.editTrackerQuery.focus();
            return;
        }
        cancelCategoryRequest();
        const controller = new AbortController();
        categoriesRequest = controller;
        elements.trackerLoadCategories.disabled = true;
        setCategoryStatus("Ищем категории…");
        try {
            const params = new URLSearchParams({ query, strict_search: "false" });
            const stats = await getJson(`/api/v1/price-stats?${params}`, { signal: controller.signal });
            if (controller.signal.aborted) return;
            const select = elements.editCategorySelect;
            const selected = select.value;
            const saved = selected ? [{ id: selected, label: select.selectedOptions[0].textContent }] : [];
            showCategories([...saved, ...(stats.categories || [])], selected);
            setCategoryStatus(select.options.length > 1 ? "" : "По этому запросу категории не найдены.");
        } catch (error) {
            if (!controller.signal.aborted) setCategoryStatus(error.message || "Не удалось подобрать категории.");
        } finally {
            if (categoriesRequest === controller) {
                categoriesRequest = null;
                elements.trackerLoadCategories.disabled = false;
            }
        }
    }

    function useCurrentSearch() {
        const query = state.search.activeQuery?.trim();
        if (!query) return;
        cancelCategoryRequest();
        elements.editTrackerQuery.value = query;
        elements.editStrictModeToggle.checked = state.search.strictSearch;
        const id = state.filters.category;
        const known = state.filters.categories || [];
        const missing = id != null && !known.some((category) => Number(category.id) === Number(id));
        showCategories(missing ? [{ id }, ...known] : known, id);
        elements.editMaxPriceInput.value = state.filters.maxPrice ?? "";
        setSelect(elements.editConditionSelect, state.filters.condition, conditionLabel);
        setSelect(elements.editSellerSelect, state.filters.sellerType, sellerLabel);
        setSelect(elements.editRegionSelect, state.filters.regionName);
        elements.editMinDiscountInput.value = state.search.sort === "cheap" ? state.filters.discountFromPercent : "";
        elements.trackerAdvanced.open = true;
        const note = elements.trackerSearchFiltersNote;
        if (note) {
            note.hidden = false;
            note.textContent = "Перенесены запрос, категория, город, состояние, продавец, точность поиска и максимальная цена. " +
            (state.filters.minPrice != null ? "Нижняя граница цены в трекерах не поддерживается. " : "") +
            (state.search.sort === "cheap" ? "Перенесена минимальная скидка; верхняя граница скидки в трекерах не поддерживается." : "");
        }
        elements.editTrackerQuery.focus();
    }

    function openEditTracker(trackerId = null, { copy = false } = {}) {
        if (!hasTelegramInitData() || formBusy) return;
        const tracker = tracking.items.find((item) => item.id === trackerId);
        if (trackerId != null && !tracker) return;
        const editing = Boolean(tracker) && !copy;
        tracking.editingId = editing ? trackerId : null;
        cancelCategoryRequest();
        setFormError();
        elements.editTrackerTitle.textContent = editing ? "Настройки трекера" : copy ? "Копия трекера" : "Новый трекер";
        elements.saveTrackerBtn.textContent = editing ? "Сохранить" : "Запустить автопоиск";
        elements.editTrackerQuery.value = tracker?.query ?? "";
        elements.editTrackerQuery.readOnly = editing;
        if (elements.trackerQueryHelp) elements.trackerQueryHelp.hidden = !editing;
        if (elements.trackerCopyButton) elements.trackerCopyButton.hidden = !editing;
        if (elements.trackerSearchFiltersNote) elements.trackerSearchFiltersNote.hidden = true;
        elements.editMaxPriceInput.value = tracker?.max_price_byn ?? "";
        elements.editMinDiscountInput.value = tracker?.min_discount_percent || "";
        elements.editStrictModeToggle.checked = Boolean(tracker?.strict_mode);
        setSelect(elements.editConditionSelect, tracker?.condition, conditionLabel);
        setSelect(elements.editSellerSelect, tracker?.seller_type, sellerLabel);
        setSelect(elements.editRegionSelect, tracker?.region_name);
        const savedCategory = tracker?.category_id != null ? [{ id: tracker.category_id, label: tracker.category_label }] : [];
        showCategories(savedCategory, tracker?.category_id);
        elements.trackerAdvanced.open = Boolean(tracker?.condition || tracker?.seller_type ||
            tracker?.strict_mode || tracker?.min_discount_percent || savedCategory.length);
        const searchQuery = state.search.activeQuery?.trim();
        elements.trackerUseSearch.hidden = editing || !searchQuery;
        elements.trackerUseSearch.textContent = searchQuery ? `Из поиска · ${searchQuery}` : "";
        elements.trackerManage.hidden = !editing;
        elements.toggleTrackerPause.textContent = tracker?.paused ? "Возобновить" : "Поставить на паузу";
        const interval = tracker?.interval_min ?? 15;
        elements.trackerFormNote.textContent = tracker?.paused
            ? "Трекер на паузе. Возобновите его, чтобы снова получать уведомления."
            : `Проверка каждые ${interval} мин. Уведомления приходят в чат бота.`;
        openModalAnimated(elements.editTrackerModal);
        const scrollBody = elements.editTrackerModal.querySelector(".tracker-form-body");
        if (scrollBody) scrollBody.scrollTop = 0;
        if (!editing) elements.editTrackerQuery.focus();
    }

    function closeEditTracker(options) {
        if (formBusy || elements.editTrackerModal?.hidden) return;
        cancelCategoryRequest();
        tracking.editingId = null;
        closeModalAnimated(elements.editTrackerModal, options);
    }

    function readFilters() {
        const select = elements.editCategorySelect;
        const price = elements.editMaxPriceInput.value;
        return {
            strict_mode: elements.editStrictModeToggle.checked,
            category_id: select.value ? Number(select.value) : null,
            category_label: select.value ? select.selectedOptions[0].textContent : null,
            min_discount_percent: Number(elements.editMinDiscountInput.value) || null,
            max_price_byn: price === "" ? null : Number(price),
            condition: elements.editConditionSelect.value || null,
            seller_type: elements.editSellerSelect.value || null,
            region_name: elements.editRegionSelect.value || null,
        };
    }

    function matchesTracker(tracker, query, filters) {
        return filterKey(tracker.query) === filterKey(query)
            && Boolean(tracker.strict_mode) === filters.strict_mode
            && (tracker.category_id ?? null) === filters.category_id
            && (tracker.min_discount_percent || null) === filters.min_discount_percent
            && (tracker.max_price_byn ?? null) === filters.max_price_byn
            && displayFilter(tracker.seller_type, sellerLabel) === (filters.seller_type || "")
            && displayFilter(tracker.condition, conditionLabel) === (filters.condition || "")
            && filterKey(tracker.region_name) === filterKey(filters.region_name);
    }

    async function applyTrackerAction(action, { successMessage, button, pendingLabel, create = false, remove = false }) {
        if (formBusy) return;
        const id = tracking.editingId;
        const label = button.textContent;
        formBusy = true;
        setFormError();
        button.textContent = pendingLabel;
        elements.trackerForm.setAttribute("aria-busy", "true");
        const controls = [elements.saveTrackerBtn, elements.cancelEditBtn, elements.closeEditModal,
            elements.toggleTrackerPause, elements.trackerMarket, elements.deleteTrackerButton];
        for (const control of controls) control.disabled = true;
        let success = false;
        try {
            const updated = await action();
            if (remove) {
                tracking.items = tracking.items.filter((item) => item.id !== id);
            } else if (updated?.id != null) {
                const previous = tracking.items.find((item) => item.id === updated.id);
                if (previous) {
                    const { event_count, avg_events_per_day, last_event_at } = previous;
                    Object.assign(previous, updated, { event_count, avg_events_per_day, last_event_at });
                } else {
                    tracking.items = [updated, ...tracking.items];
                }
            }
            success = true;
        } catch (error) {
            setFormError(error.status === 409 ? "Трекер с такими настройками уже есть."
                : error.message || "Не удалось выполнить действие. Попробуйте ещё раз.");
        } finally {
            button.textContent = label;
            for (const control of controls) control.disabled = false;
            elements.trackerForm.removeAttribute("aria-busy");
            formBusy = false;
        }
        if (success) {
            if (create) context.selectTrackingPane?.("trackers");
            closeEditTracker(id != null ? {
                onClosed: () => {
                    const edit = elements.trackersList?.querySelector(`[data-tracker-id="${id}"] [data-role="edit"]`);
                    (remove ? elements.trackQueryButton : edit || elements.trackQueryButton)?.focus();
                },
            } : undefined);
            showToast(successMessage, "success");
            await loadTrackers();
        }
    }

    function saveTracker() {
        if (formBusy) return;
        const query = elements.editTrackerQuery.value.trim();
        if (!query) {
            setFormError("Введите название или модель того, что хотите найти.");
            elements.editTrackerQuery.focus();
            return;
        }
        const id = tracking.editingId;
        const filters = readFilters();
        if (id == null && tracking.items.some((item) =>
            matchesTracker(item, query, filters) && !item.alert_price_threshold && !item.alert_discount_percent)) {
            setFormError("Трекер с такими настройками уже есть. Откройте его в списке «Мои трекеры».");
            return;
        }
        const save = id == null
            ? () => postJson("/api/v1/trackers", { query, interval_min: 15, ...filters })
            : () => requestJson(`/api/v1/trackers/${id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(filters),
            });
        return applyTrackerAction(save, {
            successMessage: id == null ? "Автопоиск запущен" : "Настройки сохранены",
            button: elements.saveTrackerBtn,
            pendingLabel: "Сохраняем…",
            create: id == null,
        });
    }

    function togglePause() {
        const tracker = tracking.items.find((item) => item.id === tracking.editingId);
        if (!tracker) return;
        const action = tracker.paused ? "resume" : "pause";
        void applyTrackerAction(() => postJson(`/api/v1/trackers/${tracker.id}/${action}`), {
            successMessage: tracker.paused ? "Автопоиск возобновлён" : "Автопоиск на паузе",
            button: elements.toggleTrackerPause,
            pendingLabel: "Обновляем…",
        });
    }

    async function deleteTracker() {
        const id = tracking.editingId;
        const tracker = tracking.items.find((item) => item.id === id);
        if (!tracker || formBusy) return;
        if (!await confirmAction({
            title: "Удалить трекер?",
            message: `Автопоиск «${tracker.query}» остановится. Найденные объявления останутся в ленте.`,
            confirmLabel: "Удалить трекер",
            destructive: true,
        })) return;
        if (formBusy || tracking.editingId !== id || elements.editTrackerModal.hidden) return;
        return applyTrackerAction(() => deleteJson(`/api/v1/trackers/${id}`), {
            successMessage: "Трекер удалён",
            button: elements.deleteTrackerButton,
            pendingLabel: "Удаляем…",
            remove: true,
        });
    }

    function openTrackerMarket() {
        const tracker = tracking.items.find((item) => item.id === tracking.editingId);
        if (!tracker) return;
        elements.searchInput.value = tracker.query;
        state.search.draft = tracker.query;
        state.search.strictSearch = Boolean(tracker.strict_mode);
        const seller = displayFilter(tracker.seller_type, sellerLabel);
        const condition = displayFilter(tracker.condition, conditionLabel);
        state.filters.category = tracker.category_id ?? null;
        state.filters.pendingCategory = tracker.category_id ?? null;
        state.filters.categories = tracker.category_id
            ? [{ id: tracker.category_id, label: tracker.category_label || `Категория ${tracker.category_id}`, count: 0 }]
            : [];
        state.filters.sellerType = state.filters.pendingSellerType = seller === "Частное лицо" ? "private" : seller === "Магазин" ? "shop" : "";
        state.filters.condition = state.filters.pendingCondition = condition === "Новый" ? "new" : condition === "Б/у" ? "used" : "";
        state.filters.regionName = state.filters.pendingRegionName = tracker.region_name || "";
        state.filters.maxPrice = state.filters.pendingMaxPrice = tracker.max_price_byn ?? null;
        state.filters.minPrice = state.filters.pendingMinPrice = null;
        state.filters.filterDropdownOpen = false;
        closeEditTracker();
        context.renderStrictSearch();
        context.markDirty("categories");
        void context.search("overview", { keepFilters: true });
    }

    elements.trackerForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        void saveTracker();
    });
    elements.trackerForm?.addEventListener("invalid", (event) => {
        if (elements.trackerAdvanced.contains(event.target)) elements.trackerAdvanced.open = true;
    }, true);
    elements.editTrackerQuery?.addEventListener("input", () => {
        cancelCategoryRequest();
        showCategories();
        setFormError();
    });
    elements.trackerUseSearch?.addEventListener("click", useCurrentSearch);
    elements.trackerCopyButton?.addEventListener("click", () => openEditTracker(tracking.editingId, { copy: true }));
    elements.trackerLoadCategories?.addEventListener("click", loadTrackerCategories);
    elements.toggleTrackerPause?.addEventListener("click", togglePause);
    elements.trackerMarket?.addEventListener("click", openTrackerMarket);
    elements.deleteTrackerButton?.addEventListener("click", deleteTracker);
    elements.closeEditModal?.addEventListener("click", () => closeEditTracker());
    elements.cancelEditBtn?.addEventListener("click", () => closeEditTracker());
    elements.editTrackerModal?.addEventListener("click", (event) => {
        if (event.target === elements.editTrackerModal) closeEditTracker();
    });

    return {
        loadTrackers, openEditTracker, closeEditTracker,
        refreshTrackerEvents, clearTrackerEvents, startTrackerRefresh, stopTrackerRefresh,
    };
}

// OPUS-13: the lazy stub delegates here once this script lands.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateApiTrackers = createApiTrackers;
}
