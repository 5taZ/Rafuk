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
        renderMonitoringHeroStats,
        renderDetailModal,
        showToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        buildCommonQuery,
    } = context;

    // ── Load watchlist ───────────────────────────────────────────────────
    async function loadWatchlist() {
        if (!hasTelegramInitData()) {
            state.watchlist = [];
            renderWatchlist();
            return;
        }
        try {
            state.watchlist = await getJson("/api/v1/watchlist");
        } catch (_) {
            state.watchlist = [];
        } finally {
            renderWatchlist();
        }
    }

    // ── Clear entire watchlist ───────────────────────────────────────────
    async function clearAllWatchlist() {
        if (!state.watchlist.length) {
            showToast("Список уже пуст");
            return;
        }
        try {
            await deleteJson("/api/v1/watchlist/all");
            const count = state.watchlist.length;
            state.watchlist = [];
            await loadWatchlist();
            renderMonitoringHeroStats();
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

        const alreadyInWatchlist = state.watchlist.some((w) => w.ad_id === item.ad_id);
        if (alreadyInWatchlist) {
            showToast("Уже в избранном");
            return;
        }

        try {
            await postJson("/api/v1/watchlist", {
                query: queryOverride || state.query || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
                thumbnail: item.thumbnail || null,
                market_median_byn: state.stats?.median ? Number(state.stats.median) : null,
            });
            showToast("Добавлено в избранное", "success");
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось добавить в избранное", "error");
        }
    }

    // ── Update watchlist item metadata ───────────────────────────────────
    async function updateWatchlistMeta(watchlistId, payload) {
        try {
            await requestJson(`/api/v1/watchlist/${watchlistId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
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
        if (!item?.ad_id) {
            return;
        }

        const alreadyInLeads = state.leads.some((l) => l.ad_id === item.ad_id);
        if (alreadyInLeads) {
            showToast("Уже в покупках");
            return;
        }

        // Reuse the addLeadFromListing helper from the listings/cross-module pool
        await context.addLeadFromListing(
            {
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.current_price_byn || item.initial_price_byn,
                thumbnail: item.thumbnail || null,
                flip_estimates: [],
            },
            "watchlist",
            item.query
        );
        await deleteWatchlistItem(item.id);
    }

    // ── Open watchlist item detail modal ─────────────────────────────────
    async function openWatchlistDetail(item) {
        if (!item?.ad_id) {
            return;
        }
        const queryToUse = item.query || state.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        showToast("Загружаю...");
        state.error = null;
        renderError();
        try {
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.currency}&strict_search=${state.strictSearch}&ad_id=${item.ad_id}`
            );
            state.detail = fullDetail;
            state.detailImageIndex = 0;
            state.detailFromWatchlist = true;
            state.detailAi = {
                adId: fullDetail.ad_id || item.ad_id,
                loading: false,
                result: null,
                error: "",
                source: "",
            };
            renderDetailModal();
        } catch (error) {
            state.error = error.message || "Не удалось загрузить детали";
            renderError();
        }
    }

    // ── Delete single watchlist item ─────────────────────────────────────
    async function deleteWatchlistItem(watchlistId) {
        try {
            await deleteJson(`/api/v1/watchlist/${watchlistId}`);
            await loadWatchlist();
            renderMonitoringHeroStats();
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        }
    }

    // ── Delete all watchlist ─────────────────────────────────────────────
    async function deleteAllWatchlist() {
        if (!state.watchlist.length) {
            showToast("Список уже пуст");
            return;
        }
        const count = state.watchlist.length;
        try {
            await deleteJson("/api/v1/watchlist/all");
            state.watchlist = [];
            await loadWatchlist();
            renderMonitoringHeroStats();
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
