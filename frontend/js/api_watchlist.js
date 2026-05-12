/**
 * api_watchlist.js — Watchlist (favorites) CRUD, promotion to leads, and detail.
 *
 * Manages the watchlist: load, add items, update meta/status, promote to leads,
 * open detail, delete items (single or all), and manual refresh.
 */

function createApiWatchlist(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        renderError,
        renderWatchlist,
        renderLeads,
        renderDetailModal,
        showToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        buildCommonQuery,
    } = context;

    let _watchlistDetailAbortController = null;

    // After watchlist mutations we need to refresh BOTH renders. The
    // legacy "Избранное" view still binds renderWatchlist, while the
    // unified "Мои объявления" tab binds renderLeads which itself reads
    // from state.watchlist for the "Избранное" tab. Without this, a
    // delete/promote leaves the card visible until the user switches tabs.
    function refreshAfterWatchlistChange() {
        if (typeof renderWatchlist === "function") renderWatchlist();
        if (typeof renderLeads === "function") renderLeads();
    }

    // Tracks ad_ids and watchlist row ids with an in-flight mutation so
    // a rapid double-click doesn't fire two POST/PATCH/DELETE for the
    // same row. The backend is race-safe (savepoint + 409), but the
    // user otherwise sees doubled toasts and one round-trip is wasted.
    const _inflightAd = new Set();
    const _inflightWatchId = new Set();

    // FE-M14: ``INFLIGHT_GUARD_MS`` lives in dom_helpers.js so the
    // dedupe window matches the cross-pipeline guard in app_actions —
    // a "Добавить" click and a "Удалить" on the same row can't race
    // past each other regardless of which surface fired first.
    function _guardInflightAd(adId) {
        _inflightAd.add(adId);
        setTimeout(() => _inflightAd.delete(adId), INFLIGHT_GUARD_MS);
    }
    function _guardInflightWatchId(watchId) {
        _inflightWatchId.add(watchId);
        setTimeout(() => _inflightWatchId.delete(watchId), INFLIGHT_GUARD_MS);
    }

    function _validVersion(value) {
        const numeric = Number(value);
        return Number.isInteger(numeric) && numeric >= 1 ? numeric : null;
    }

    function _findWatchlistSnapshot(itemOrId) {
        const itemId = typeof itemOrId === "object" ? itemOrId?.id : itemOrId;
        return state.watchlist.items.find((w) => w.id === itemId) || null;
    }

    function _nextWatchlistRequestId() {
        state.watchlist._requestId = (state.watchlist._requestId + 1) % 1_000_000;
        return state.watchlist._requestId;
    }

    async function _resolveWatchlistVersion(itemOrId) {
        const snapshot = _findWatchlistSnapshot(itemOrId)
            || (typeof itemOrId === "object" ? itemOrId : null);
        const version = _validVersion(snapshot?.version);
        if (version) return version;
        await loadWatchlist();
        return _validVersion(_findWatchlistSnapshot(itemOrId)?.version);
    }

    function _showStaleWatchlistToast() {
        showToast("Данные устарели. Обновите список и попробуйте ещё раз.", "error");
    }

    // ── Load watchlist ───────────────────────────────────────────────────
    async function loadWatchlist() {
        if (!hasTelegramInitData()) {
            state.watchlist.items = [];
            refreshAfterWatchlistChange();
            return;
        }
        state.watchlist._loading = true;
        refreshAfterWatchlistChange();

        // Stale-response guard — same pattern as loadLeads(). Watchlist
        // and leads share the same lead_items table, so a stale
        // watchlist GET arriving after a promote/delete can resurrect
        // a row that no longer belongs there.
        const requestId = _nextWatchlistRequestId();
        let nextWatchlist;
        try {
            nextWatchlist = await getJson("/api/v1/watchlist");
        } catch (_) {
            nextWatchlist = [];
        }
        if (requestId !== state.watchlist._requestId) return;
        state.watchlist._loading = false;
        state.watchlist.items = nextWatchlist;
        refreshAfterWatchlistChange();
    }

    // ── Clear entire watchlist ───────────────────────────────────────────
    async function clearAllWatchlist() {
        if (!state.watchlist.items.length) {
            showToast("Список уже пуст");
            return;
        }
        try {
            await deleteJson("/api/v1/watchlist/all");
            const count = state.watchlist.items.length;
            state.watchlist.items = [];
            await loadWatchlist();
            showToast(`Удалено ${count} лотов`);
        } catch (error) {
            showToast(error.message || "Не удалось очистить список");
            await loadWatchlist();
        }
    }

    // ── Add item to watchlist from a listing ─────────────────────────────
    async function addWatchlistFromListing(item, queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }
        if (_inflightAd.has(item.ad_id)) {
            return;
        }

        // Check both surfaces — after the watchlist→leads merge a single
        // ad_id can only be in ONE state at a time.
        const ACTIVE_LEAD_STATUSES = new Set([
            "new",
            "in_progress",
            "researching",
            "bought",
            "sold",
        ]);
        const alreadyInWatchlist = state.watchlist.items.some((w) => w.ad_id === item.ad_id);
        if (alreadyInWatchlist) {
            showToast("Уже в избранном");
            return;
        }
        const alreadyInLeads = state.leads.items.some(
            (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
        );
        if (alreadyInLeads) {
            showToast("Уже в покупках");
            return;
        }

        _guardInflightAd(item.ad_id);
        try {
            await postJson("/api/v1/watchlist", {
                query: queryOverride || state.search.query || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
                thumbnail: item.thumbnail || null,
                market_median_byn: state.misc.stats?.median ? Number(state.misc.stats.median) : null,
            });
            showToast("В избранном", "success", 1600);
            await loadWatchlist();
        } catch (error) {
            // Server returns 409 with detail "Этот лот уже в покупках"
            // when the ad already has a non-watching lead. Surface a
            // friendly toast instead of the generic "internal error".
            const message = error?.message || "";
            if (/уже\s+в\s+покупках/i.test(message)) {
                showToast("Уже в покупках");
                // Make sure UI reflects reality.
                if (typeof context.loadLeads === "function") {
                    await context.loadLeads();
                }
                return;
            }
            showToast(message || "Не удалось добавить в избранное", "error");
        } finally {
            _inflightAd.delete(item.ad_id);
        }
    }

    // ── Update watchlist item metadata ───────────────────────────────────
    async function updateWatchlistMeta(watchlistId, payload) {
        try {
            const item = state.watchlist.items.find((w) => w.id === watchlistId);
            const version = payload.version ?? await _resolveWatchlistVersion(item || watchlistId);
            if (!version) {
                _showStaleWatchlistToast();
                return;
            }
            await requestJson(`/api/v1/watchlist/${watchlistId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    ...payload,
                    version,
                }),
            });
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось обновить");
        }
    }

    async function updateWatchlistStatus(watchlistId, workflowStatus) {
        await updateWatchlistMeta(watchlistId, { workflow_status: workflowStatus });
    }

    // ── Promote watchlist item to lead ───────────────────────────────────
    async function promoteWatchlistToLead(item) {
        if (!item?.id || !item?.ad_id) {
            return;
        }
        if (_inflightWatchId.has(item.id)) {
            return;
        }

        // Watchlist items live in the same lead_items table after the
        // 20260427_0001 merge — promotion is a pure status transition,
        // no INSERT + DELETE dance needed.
        const ACTIVE_LEAD_STATUSES = new Set([
            "new",
            "in_progress",
            "researching",
            "bought",
            "sold",
        ]);
        const alreadyInLeads = state.leads.items.some(
            (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
        );
        if (alreadyInLeads) {
            showToast("Уже в покупках");
            return;
        }

        _guardInflightWatchId(item.id);
        try {
            const version = await _resolveWatchlistVersion(item);
            if (!version) {
                _showStaleWatchlistToast();
                return;
            }
            await requestJson(`/api/v1/leads/${item.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "new",
                    version,
                }),
            });
            showToast("В покупках", "success", 1600);
            // Optimistic local state cleanup so the UI reflects the move
            // immediately, even before the parallel reloads finish.
            state.watchlist.items = state.watchlist.items.filter((w) => w.id !== item.id);
            refreshAfterWatchlistChange();
            await Promise.all([
                loadWatchlist(),
                typeof context.loadLeads === "function" ? context.loadLeads() : null,
            ]);
        } catch (error) {
            showToast(error?.message || "Не удалось перевести в покупки", "error");
        } finally {
            _inflightWatchId.delete(item.id);
        }
    }

    // ── Open watchlist item detail modal ─────────────────────────────────
    async function openWatchlistDetail(item) {
        if (!item?.ad_id) {
            return;
        }
        const queryToUse = item.query || state.search.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        // Abort previous in-flight watchlist detail request
        if (_watchlistDetailAbortController) {
            _watchlistDetailAbortController.abort();
        }
        _watchlistDetailAbortController = new AbortController();
        const signal = _watchlistDetailAbortController.signal;

        // Shared stale-response guard with openListingDetail and
        // openLeadDetail — older detail responses are dropped.
        const requestId = (state.detail._requestId =
            (state.detail._requestId + 1) % 1_000_000);

        state.ui.error = null;
        renderError();
        try {
            const catParam = state.filters.category != null ? `&category=${state.filters.category}` : "";
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.misc.currency}&strict_search=${state.search.strictSearch}&ad_id=${item.ad_id}${catParam}`,
                { signal }
            );
            if (requestId !== state.detail._requestId) return;
            state.detail.data = fullDetail;
            state.detail.imageIndex = 0;
            state.detail.fromWatchlist = true;
            state.detail.ai = {
                adId: fullDetail.ad_id || item.ad_id,
                loading: false,
                result: null,
                error: "",
                source: "",
            };
            renderDetailModal();
        } catch (error) {
            if (error.name === "AbortError") {
                return;
            }
            if (requestId !== state.detail._requestId) return;
            state.ui.error = error.message || "Не удалось загрузить детали";
            renderError();
        }
    }

    // ── Delete single watchlist item ─────────────────────────────────────
    async function deleteWatchlistItem(watchlistId) {
        if (_inflightWatchId.has(watchlistId)) {
            return;
        }
        _guardInflightWatchId(watchlistId);
        _nextWatchlistRequestId();
        // Optimistic remove so the card disappears immediately even
        // when the network is slow. The server-side DELETE is
        // idempotent (always 204), so a duplicate click later — even
        // after this guard's TTL — is still safe.
        const previousWatchlist = state.watchlist.items;
        state.watchlist.items = state.watchlist.items.filter((w) => w.id !== watchlistId);
        state.watchlist._loading = false;
        refreshAfterWatchlistChange();
        try {
            await deleteJson(`/api/v1/watchlist/${watchlistId}`);
            // Confirmation toast was missing — users couldn't tell
            // delete actually fired vs the card just animating out.
            showToast("Удалено из избранного", "info");
        } catch (error) {
            // Rollback the optimistic removal so the user can see the
            // item didn't actually delete and retry.
            state.watchlist.items = previousWatchlist;
            refreshAfterWatchlistChange();
            showToast(error.message || "Не удалось удалить", "error");
        } finally {
            _inflightWatchId.delete(watchlistId);
        }
    }

    // ── Delete all watchlist ─────────────────────────────────────────────
    async function deleteAllWatchlist() {
        if (!state.watchlist.items.length) {
            showToast("Список уже пуст");
            return;
        }
        const count = state.watchlist.items.length;
        try {
            await deleteJson("/api/v1/watchlist/all");
            state.watchlist.items = [];
            await loadWatchlist();
            showToast(`Удалено ${count} лотов`);
        } catch (error) {
            showToast(error.message || "Не удалось очистить");
            await loadWatchlist();
        }
    }

    // ── Refresh watchlist (server-side price check) ──────────────────────
    async function refreshWatchlist() {
        const button = elements.watchlistSection?.querySelector('[data-role="refresh-watchlist"]');
        let originalText = "";
        if (button) {
            originalText = button.textContent;
            button.disabled = true;
            button.classList.add('is-loading');
            button.textContent = 'Обновляю...';
        }
        try {
            const payload = await postJson("/api/v1/watchlist/refresh", {});
            const parts = [`${payload.updated} проверено`, `${payload.price_drops} падений цены`];
            if (payload.missing > 0) {
                parts.push(`${payload.missing} пропало`);
            }
            if (payload.auto_removed > 0) {
                parts.push(`${payload.auto_removed} удалено (устарело)`);
            }
            showToast(`Обновлено: ${parts.join(", ")}`);
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось обновить цены");
        } finally {
            if (button) {
                button.disabled = false;
                button.classList.remove('is-loading');
                button.textContent = originalText;
            }
        }
    }

    return {
        loadWatchlist,
        clearAllWatchlist,
        addWatchlistFromListing,
        updateWatchlistMeta,
        updateWatchlistStatus,
        promoteWatchlistToLead,
        openWatchlistDetail,
        deleteWatchlistItem,
        deleteAllWatchlist,
        refreshWatchlist,
    };
}
