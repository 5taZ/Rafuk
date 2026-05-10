/**
 * api_trackers.js — Tracker CRUD, event feed, and auto-refresh.
 *
 * Manages tracker lifecycle: create, pause, resume, delete, edit,
 * plus the periodic background refresh of the tracker event feed.
 */

function createApiTrackers(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        trapFocus,
        renderAll,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
        renderTrackerStatus,
        showToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
    } = context;

    // ── Auto-refresh state ───────────────────────────────────────────────
    let trackerRefreshTimer = null;
    const TRACKER_REFRESH_MS = 30_000;

    function startTrackerRefresh() {
        stopTrackerRefresh();
        trackerRefreshTimer = setInterval(() => {
            void refreshTrackerEvents();
        }, TRACKER_REFRESH_MS);
    }

    function stopTrackerRefresh() {
        if (trackerRefreshTimer) {
            clearInterval(trackerRefreshTimer);
            trackerRefreshTimer = null;
        }
    }

    async function refreshTrackerEvents() {
        if (!hasTelegramInitData()) return;
        if (state.ui.activeView !== "tracking") return;
        try {
            const [trackersResult, eventsResult] = await Promise.allSettled([
                getJson("/api/v1/trackers"),
                getJson("/api/v1/tracker-events"),
            ]);
            if (trackersResult.status === "fulfilled") {
                state.trackers.items = trackersResult.value;
                renderTrackers();
            }
            if (eventsResult.status === "fulfilled") {
                state.trackers.events = eventsResult.value;
                renderTrackerEvents();
                renderTrackerEventFilters();
            }
        } catch (err) {
            // Background refresh failed — will retry on next interval
        }
    }

    // ── Load trackers + events ───────────────────────────────────────────
    async function loadTrackers() {
        if (!hasTelegramInitData()) {
            state.trackers.items = [];
            state.trackers.events = [];
            state.trackers.status = "";
            renderTrackers();
            renderTrackerEvents();
            renderTrackerStatus();
            return;
        }

        // Show skeleton cards while loading
        state.trackers.items = [];
        state.trackers._loading = true;
        renderTrackers();

        const [trackersResult, eventsResult] = await Promise.allSettled([
            getJson("/api/v1/trackers"),
            getJson("/api/v1/tracker-events"),
        ]);

        state.trackers._loading = false;
        state.trackers.items = trackersResult.status === "fulfilled" ? trackersResult.value : [];
        state.trackers.events = eventsResult.status === "fulfilled" ? eventsResult.value : [];

        const failures = [trackersResult, eventsResult].filter((r) => r.status === "rejected");
        if (failures.length > 0 && context.showToast) {
            context.showToast("Не удалось загрузить некоторые данные", "error", 3000);
        }

        if (trackersResult.status === "rejected") {
            state.trackers.status = trackersResult.reason?.message || "Не удалось загрузить трекеры.";
            state.trackers.statusKind = "error";
        } else {
            state.trackers.status = "";
            state.trackers.statusKind = "info";
        }

        renderTrackers();
        renderTrackerEvents();
        renderTrackerStatus();
    }

    // ── Create tracker ───────────────────────────────────────────────────
    async function createTracker() {
        if (!hasTelegramInitData()) {
            showToast("Доступно только в Telegram");
            return;
        }

        const query = state.search.query.trim();
        if (!query) {
            showToast("Сначала введите запрос");
            return;
        }

        const normalizedQuery = query.toLocaleLowerCase("ru-RU");
        const duplicate = state.trackers.items.find(
            (t) => t.query.trim().toLocaleLowerCase("ru-RU") === normalizedQuery
        );
        if (duplicate) {
            showToast("Такой трекер уже существует");
            return;
        }

        try {
            state.trackers.creating = true;
            if (context.renderAll) context.renderAll();
            await postJson("/api/v1/trackers", {
                query,
                strict_mode: state.search.strictSearch,
                interval_min: 15,
                min_discount_percent: state.trackers.minDiscountPercent,
                max_price_byn: state.trackers.maxPriceByn,
                seller_type: state.trackers.sellerType || null,
                condition: state.trackers.condition || null,
                region_name: state.trackers.regionName || null,
                config_keyword: state.trackers.configKeyword || null,
            });
            showToast("Трекер добавлен", "success");
            await loadTrackers();
            renderAll();
        } catch (error) {
            state.trackers.status = error.message || "Не удалось создать трекер.";
            state.trackers.statusKind = "error";
            renderTrackerStatus();
        } finally {
            state.trackers.creating = false;
            if (context.renderAll) context.renderAll();
        }
    }

    // ── Delete tracker ───────────────────────────────────────────────────
    async function deleteTracker(trackerId) {
        if (!trackerId) {
            return;
        }

        try {
            await deleteJson(`/api/v1/trackers/${trackerId}`);
            showToast("Трекер удалён");
            await loadTrackers();
            renderAll();
        } catch (error) {
            state.trackers.status = error.message || "Не удалось удалить трекер.";
            state.trackers.statusKind = "error";
            renderTrackerStatus();
        }
    }

    // ── Pause / Resume tracker ───────────────────────────────────────────
    async function pauseTracker(trackerId) {
        if (!hasTelegramInitData()) {
            showToast("Доступно только в Telegram");
            return;
        }
        try {
            await postJson(`/api/v1/trackers/${trackerId}/pause`);
            showToast("Трекер приостановлен");
            await loadTrackers();
            renderAll();
        } catch (error) {
            showToast(error.message || "Не удалось приостановить трекер");
        }
    }

    async function resumeTracker(trackerId) {
        if (!hasTelegramInitData()) {
            showToast("Доступно только в Telegram");
            return;
        }
        try {
            await postJson(`/api/v1/trackers/${trackerId}/resume`);
            showToast("Трекер возобновлен");
            await loadTrackers();
            renderAll();
        } catch (error) {
            showToast(error.message || "Не удалось возобновить трекер");
        }
    }

    // ── Edit tracker modal ───────────────────────────────────────────────
    function openEditTracker(trackerId) {
        const tracker = state.trackers.items.find((t) => t.id === trackerId);
        if (!tracker) {
            showToast("Трекер не найден");
            return;
        }

        state.trackers.editingId = trackerId;

        if (elements.editTrackerQuery) elements.editTrackerQuery.value = tracker.query;
        if (elements.editStrictModeToggle) elements.editStrictModeToggle.checked = Boolean(tracker.strict_mode);
        if (elements.editMinDiscountInput) elements.editMinDiscountInput.value = tracker.min_discount_percent ?? 10;
        if (elements.editMaxPriceInput) elements.editMaxPriceInput.value = tracker.max_price_byn ?? "";
        if (elements.editSellerSelect) elements.editSellerSelect.value = tracker.seller_type || "";
        if (elements.editConditionSelect) elements.editConditionSelect.value = tracker.condition || "";
        if (elements.editRegionSelect) elements.editRegionSelect.value = tracker.region_name || "";
        if (elements.editConfigInput) elements.editConfigInput.value = tracker.config_keyword || "";

        if (elements.editTrackerModal) {
            if (state.misc.modalCleanup) {
                state.misc.modalCleanup();
                state.misc.modalCleanup = null;
            }
            // FE-H4/UX-H1: openModalAnimated() already installs a
            // focus trap (see dom_helpers.js). The extra trapFocus()
            // call we used to make here registered a second keydown
            // listener that both handled Tab, leading to focus fights
            // and a leaked listener once closeModalAnimated cleaned up
            // only _focusTrapCleanup.
            openModalAnimated(elements.editTrackerModal);
        }
    }

    function closeEditTracker() {
        if (state.misc.modalCleanup) {
            state.misc.modalCleanup();
            state.misc.modalCleanup = null;
        }
        state.trackers.editingId = null;
        if (elements.editTrackerModal) closeModalAnimated(elements.editTrackerModal);
    }

    async function saveTracker() {
        if (!hasTelegramInitData() || !state.trackers.editingId) {
            showToast("Ошибка");
            return;
        }

        try {
            await requestJson(`/api/v1/trackers/${state.trackers.editingId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    strict_mode: elements.editStrictModeToggle?.checked,
                    min_discount_percent: Number(elements.editMinDiscountInput?.value) || null,
                    max_price_byn: elements.editMaxPriceInput?.value ? Number(elements.editMaxPriceInput.value) : null,
                    seller_type: elements.editSellerSelect?.value || null,
                    condition: elements.editConditionSelect?.value || null,
                    region_name: elements.editRegionSelect?.value || null,
                    config_keyword: elements.editConfigInput?.value || null,
                }),
            });

            showToast("Трекер обновлен");
            closeEditTracker();
            await loadTrackers();
            renderAll();
        } catch (error) {
            showToast(error.message || "Не удалось обновить трекер");
        }
    }

    return {
        loadTrackers,
        createTracker,
        pauseTracker,
        resumeTracker,
        deleteTracker,
        openEditTracker,
        closeEditTracker,
        saveTracker,
        refreshTrackerEvents,
        startTrackerRefresh,
        stopTrackerRefresh,
    };
}
