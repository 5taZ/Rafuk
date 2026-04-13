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
        renderTrackerInputs,
        renderStrictSearch,
        renderDealInputs,
        setPanelOpen,
        showToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        buildCommonQuery,
        search,
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
        if (state.activeView !== "tracking") return;
        try {
            const [trackersResult, eventsResult] = await Promise.allSettled([
                getJson("/api/v1/trackers"),
                getJson("/api/v1/tracker-events"),
            ]);
            if (trackersResult.status === "fulfilled") {
                state.trackers = trackersResult.value;
                renderTrackers();
            }
            if (eventsResult.status === "fulfilled") {
                state.trackerEvents = eventsResult.value;
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
            state.trackers = [];
            state.trackerEvents = [];
            state.trackerStatus = "";
            renderTrackers();
            renderTrackerEvents();
            renderTrackerStatus();
            return;
        }

        const [trackersResult, eventsResult] = await Promise.allSettled([
            getJson("/api/v1/trackers"),
            getJson("/api/v1/tracker-events"),
        ]);

        state.trackers = trackersResult.status === "fulfilled" ? trackersResult.value : [];
        state.trackerEvents = eventsResult.status === "fulfilled" ? eventsResult.value : [];

        const failures = [trackersResult, eventsResult].filter((r) => r.status === "rejected");
        if (failures.length > 0 && context.showToast) {
            context.showToast("Не удалось загрузить некоторые данные", "error", 3000);
        }

        if (trackersResult.status === "rejected") {
            state.trackerStatus = trackersResult.reason?.message || "Не удалось загрузить трекеры.";
            state.trackerStatusKind = "error";
        } else {
            state.trackerStatus = "";
            state.trackerStatusKind = "info";
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

        const query = state.query.trim();
        if (!query) {
            showToast("Сначала введите запрос");
            return;
        }

        const normalizedQuery = query.toLocaleLowerCase("ru-RU");
        const duplicate = state.trackers.find(
            (t) => t.query.trim().toLocaleLowerCase("ru-RU") === normalizedQuery
        );
        if (duplicate) {
            showToast("Такой трекер уже существует");
            return;
        }

        try {
            state.creatingTracker = true;
            if (context.renderAll) context.renderAll();
            await postJson("/api/v1/trackers", {
                query,
                strict_mode: state.strictSearch,
                interval_min: 15,
                min_discount_percent: state.trackerMinDiscountPercent,
                max_price_byn: state.trackerMaxPriceByn,
                seller_type: state.trackerSellerType || null,
                condition: state.trackerCondition || null,
                region_name: state.trackerRegionName || null,
                config_keyword: state.trackerConfigKeyword || null,
                exclude_duplicates: state.trackerExcludeDuplicates,
            });
            showToast("Трекер добавлен", "success");
            await loadTrackers();
            renderAll();
        } catch (error) {
            state.trackerStatus = error.message || "Не удалось создать трекер.";
            state.trackerStatusKind = "error";
            renderTrackerStatus();
        } finally {
            state.creatingTracker = false;
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
            state.trackerStatus = error.message || "Не удалось удалить трекер.";
            state.trackerStatusKind = "error";
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
        const tracker = state.trackers.find((t) => t.id === trackerId);
        if (!tracker) {
            showToast("Трекер не найден");
            return;
        }

        state.editingTrackerId = trackerId;

        if (elements.editTrackerQuery) elements.editTrackerQuery.value = tracker.query;
        if (elements.editStrictModeToggle) elements.editStrictModeToggle.checked = Boolean(tracker.strict_mode);
        if (elements.editMinDiscountInput) elements.editMinDiscountInput.value = tracker.min_discount_percent ?? 10;
        if (elements.editMaxPriceInput) elements.editMaxPriceInput.value = tracker.max_price_byn ?? "";
        if (elements.editSellerSelect) elements.editSellerSelect.value = tracker.seller_type || "";
        if (elements.editConditionSelect) elements.editConditionSelect.value = tracker.condition || "";
        if (elements.editRegionSelect) elements.editRegionSelect.value = tracker.region_name || "";
        if (elements.editConfigInput) elements.editConfigInput.value = tracker.config_keyword || "";
        if (elements.editExcludeDuplicatesToggle) elements.editExcludeDuplicatesToggle.checked = Boolean(tracker.exclude_duplicates);

        if (elements.editTrackerModal) {
            if (state.modalCleanup) {
                state.modalCleanup();
                state.modalCleanup = null;
            }
            elements.editTrackerModal.hidden = false;
            state.modalCleanup = trapFocus(elements.editTrackerModal);
        }
    }

    function closeEditTracker() {
        if (state.modalCleanup) {
            state.modalCleanup();
            state.modalCleanup = null;
        }
        state.editingTrackerId = null;
        if (elements.editTrackerModal) elements.editTrackerModal.hidden = true;
    }

    async function saveTracker() {
        if (!hasTelegramInitData() || !state.editingTrackerId) {
            showToast("Ошибка");
            return;
        }

        try {
            await requestJson(`/api/v1/trackers/${state.editingTrackerId}`, {
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
                    exclude_duplicates: elements.editExcludeDuplicatesToggle?.checked,
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
