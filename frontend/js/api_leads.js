/**
 * api_leads.js — Leads (purchases) CRUD, lifecycle, and detail.
 *
 * Handles loading, confirming, cancelling, closing, reverting, deleting leads,
 * plus updating lead metadata and opening the detail modal for a lead.
 */

function createApiLeads(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        renderError,
        renderLeads,
        renderDealsHeroStats,
        renderProfitDashboard,
        renderDetailModal,
        showToast,
        dismissToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
    } = context;

    let _leadDetailAbortController = null;
    let _analyticsAbortController = null;

    // ── Load leads ───────────────────────────────────────────────────────
    async function loadLeads() {
        if (!hasTelegramInitData()) {
            state.leads.items = [];
            renderLeads();
            renderDealsHeroStats();
            renderProfitDashboard();
            return;
        }
        // Stale-response guard: rapid tab toggling or mutations followed
        // by reloads can issue multiple in-flight loadLeads() calls. The
        // older response can otherwise resolve last and overwrite a
        // fresher state with outdated rows.
        const requestId = (state.leads._requestId = (state.leads._requestId + 1) % 1_000_000);
        let nextLeads;
        try {
            nextLeads = await getJson("/api/v1/leads");
        } catch (_) {
            nextLeads = [];
        }
        // Drop the response if a newer loadLeads() has started since.
        if (requestId !== state.leads._requestId) return;
        state.leads.items = nextLeads;
        renderLeads();
        renderDealsHeroStats();
        renderProfitDashboard();
        // Refresh server-side analytics in parallel with the lead list —
        // the dashboard depends on lead-mutating endpoints (sale, expense)
        // so any reload of leads should also refresh aggregates.
        if (!state.analytics.loading) {
            void loadAnalytics();
        }
    }

    // ── Load lead analytics dashboard ────────────────────────────────────
    async function loadAnalytics() {
        if (!hasTelegramInitData()) {
            state.analytics.dashboard = null;
            renderProfitDashboard();
            return;
        }

        // Abort previous in-flight analytics request
        if (_analyticsAbortController) {
            _analyticsAbortController.abort();
        }
        _analyticsAbortController = new AbortController();
        const signal = _analyticsAbortController.signal;

        const requestId = (state._analyticsRequestId =
            ((state._analyticsRequestId || 0) + 1) % 1_000_000);
        state.analytics.loading = true;
        renderProfitDashboard();
        try {
            const days = Number(state.analytics.periodDays || 90);
            const response = await getJson(
                `/api/v1/analytics/leads?days=${encodeURIComponent(days)}`,
                { signal },
            );
            if (requestId !== state._analyticsRequestId) return;
            state.analytics.dashboard = response;
        } catch (err) {
            if (err.name === "AbortError") return;
            if (requestId !== state._analyticsRequestId) return;
            state.analytics.dashboard = null;
        } finally {
            if (requestId === state._analyticsRequestId) {
                state.analytics.loading = false;
                renderProfitDashboard();
            }
        }
    }

    // ── Clear all leads (active only) ────────────────────────────────────
    async function clearAllLeads() {
        const activeLeads = state.leads.items.filter((l) => l.status !== "closed");
        if (!activeLeads.length) {
            showToast("Нет активных сделок для удаления");
            return;
        }
        try {
            await deleteJson("/api/v1/leads/all");
            const count = activeLeads.length;
            state.leads.items = state.leads.items.filter((l) => l.status === "closed");
            renderLeads();
            showToast(`Удалено ${count} сделок`);
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось очистить список");
            await loadLeads();
        }
    }

    // ── Confirm lead (sold stage with prices) ────────────────────────────
    async function confirmLead(lead, cardElement) {
        const buyPriceInput = cardElement.querySelector('[data-role="buy-price"]');
        const soldPriceInput = cardElement.querySelector('[data-role="sold-price"]');

        const buyPriceRaw = buyPriceInput?.value?.trim();
        const soldPriceRaw = soldPriceInput?.value?.trim();

        if (!buyPriceRaw || !soldPriceRaw) {
            showToast("Заполните оба поля: цена покупки и цена продажи");
            if (!buyPriceRaw) {
                buyPriceInput?.focus();
            } else {
                soldPriceInput?.focus();
            }
            return;
        }

        const buyPriceNum = parseInt(buyPriceRaw, 10);
        const soldPriceNum = parseInt(soldPriceRaw, 10);

        if (!Number.isInteger(buyPriceNum) || buyPriceNum <= 0) {
            showToast("Введите целую цену покупки больше 0");
            buyPriceInput?.focus();
            return;
        }
        if (buyPriceNum > 10_000_000) {
            showToast("Цена покупки слишком большая (макс. 10 000 000 BYN)");
            buyPriceInput?.focus();
            return;
        }

        if (!Number.isInteger(soldPriceNum) || soldPriceNum <= 0) {
            showToast("Введите целую цену продажи больше 0");
            soldPriceInput?.focus();
            return;
        }
        if (soldPriceNum > 10_000_000) {
            showToast("Цена продажи слишком большая (макс. 10 000 000 BYN)");
            soldPriceInput?.focus();
            return;
        }

        try {
            const payload = {
                buy_price_byn: buyPriceNum,
                sold_price_byn: soldPriceNum,
                status: "sold",
                version: lead.version,
            };

            const updatedLead = await requestJson(`/api/v1/leads/${lead.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            const leadInState = state.leads.items.find((l) => l.id === lead.id);
            if (leadInState) {
                Object.assign(leadInState, updatedLead);
            }

            renderLeads();

            const profit = soldPriceNum - buyPriceNum;
            const profitSign = profit >= 0 ? "+" : "";
            showToast(`✓ Сделка подтверждена! ${profitSign}${Math.round(profit)} BYN`, "success");

            // Full reload to refresh analytics/profit dashboard data
            await loadLeads();

            setTimeout(() => {
                const updatedCard = elements.leadInboxList?.querySelector(
                    `[data-lead-id="${lead.id}"]`
                );
                if (updatedCard) {
                    updatedCard.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }, 100);
        } catch (error) {
            // Revert optimistic state by reloading from server
            showToast(error.message || "Не удалось подтвердить сделку");
            await loadLeads();
        }
    }

    // ── Cancel lead (delete) ─────────────────────────────────────────────
    async function cancelLead(leadId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}`);
            state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
            renderLeads();
            showToast("✓ Сделка отменена", "info");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось отменить сделку");
        }
    }

    // ── Close deal (mark closed, show profit) ────────────────────────────
    async function closeDeal(leadId) {
        try {
            const lead = state.leads.items.find((l) => l.id === leadId);
            const buyPriceByn = lead?.buy_price_byn || 0;
            const soldPriceByn = lead?.sold_price_byn || 0;

            const profit = soldPriceByn - buyPriceByn;

            const updatedLead = await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status: "closed", version: lead?.version }),
            });

            if (lead) {
                Object.assign(lead, updatedLead);
            }
            renderLeads();

            const profitSign = profit >= 0 ? "+" : "";
            if (profit >= 0) {
                showToast(`✓ Сделка закрыта. Результат: ${profitSign}${Math.round(profit)} BYN`);
            } else {
                showToast(`✓ Сделка закрыта. Результат: ${Math.round(profit)} BYN`);
            }
        } catch (error) {
            showToast(error.message || "Не удалось закрыть сделку");
        }
    }

    // ── Revert lead stage (back to new) ──────────────────────────────────
    async function revertLeadStage(leadId, currentStatus) {
        try {
            const leadInState = state.leads.items.find((l) => l.id === leadId);
            const updatedLead = await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "new",
                    buy_price_byn: null,
                    sold_price_byn: null,
                    version: leadInState?.version,
                }),
            });

            if (leadInState) {
                Object.assign(leadInState, updatedLead);
            }
            renderLeads();

            showToast("↩ Сделка возвращена на этап «Новая»");

            setTimeout(() => {
                const updatedCard = elements.leadInboxList?.querySelector(
                    `[data-lead-id="${leadId}"]`
                );
                if (updatedCard) {
                    updatedCard.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }, 100);
        } catch (error) {
            showToast(error.message || "Не удалось вернуть сделку");
        }
    }

    // ── Delete single lead ───────────────────────────────────────────────
    async function deleteLead(leadId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}`);
            state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
            renderLeads();
            showToast("Сделка удалена");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        }
    }

    // ── Update lead metadata ─────────────────────────────────────────────
    async function updateLeadMeta(leadId, payload) {
        try {
            const lead = state.leads.items.find((l) => l.id === leadId);
            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    ...payload,
                    version: payload.version ?? lead?.version,
                }),
            });
            await loadLeads();
            return true;
        } catch (error) {
            showToast(error.message || "Не удалось обновить сделку");
            await loadLeads();
            return false;
        }
    }

    async function updateLeadStatus(leadId, nextStatus) {
        await updateLeadMeta(leadId, { status: nextStatus });
    }

    // ── Mark lead as sold ────────────────────────────────────────────────
    async function markLeadAsSold(lead, priceNum) {
        if (!priceNum || !Number.isFinite(priceNum) || priceNum <= 0) {
            showToast("Введите корректную цену");
            return;
        }

        try {
            const updatedLead = await requestJson(`/api/v1/leads/${lead.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "sold",
                    sold_price_byn: priceNum,
                    version: lead.version,
                }),
            });

            const leadInState = state.leads.items.find((l) => l.id === lead.id);
            if (leadInState) {
                Object.assign(leadInState, updatedLead);
            }
            renderLeads();

            const priceBynRaw = lead.price_byn || 0;
            const profit = priceNum - priceBynRaw;
            const profitSign = profit >= 0 ? "+" : "";
            showToast(`✓ Сделка продана! Результат: ${profitSign}${Math.round(profit)} BYN`);
        } catch (err) {
            showToast(err.message || "Не удалось отметить сделку как проданную");
            await loadLeads();
        }
    }

    // ── Open lead detail modal ───────────────────────────────────────────
    async function openLeadDetail(lead) {
        if (!lead?.ad_id) {
            showToast("Не удалось открыть: нет ID объявления");
            return;
        }
        const queryToUse = lead.query || state.search.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        // Abort previous in-flight lead detail request
        if (_leadDetailAbortController) {
            _leadDetailAbortController.abort();
        }
        _leadDetailAbortController = new AbortController();
        const signal = _leadDetailAbortController.signal;

        // Shared stale-response guard with openListingDetail and
        // openWatchlistDetail — only the latest tap wins, older
        // listing-detail responses are dropped.
        const requestId = (state.detail._requestId =
            (state.detail._requestId + 1) % 1_000_000);

        const loadingToast = showToast("Загружаю...", "info", 1400);
        state.ui.error = null;
        renderError();
        try {
            const catParam = state.filters.category != null ? `&category=${state.filters.category}` : "";
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.misc.currency}&strict_search=${state.search.strictSearch}&ad_id=${lead.ad_id}${catParam}`,
                { signal }
            );
            if (requestId !== state.detail._requestId) return;
            state.detail.data = fullDetail;
            state.detail.imageIndex = 0;
            state.detail.fromWatchlist = false;
            state.detail.ai = {
                adId: fullDetail.ad_id || lead.ad_id,
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
        } finally {
            if (loadingToast) dismissToast(loadingToast);
        }
    }

    return {
        loadLeads,
        loadAnalytics,
        clearAllLeads,
        confirmLead,
        cancelLead,
        closeDeal,
        revertLeadStage,
        deleteLead,
        updateLeadMeta,
        updateLeadStatus,
        markLeadAsSold,
        openLeadDetail,
    };
}
