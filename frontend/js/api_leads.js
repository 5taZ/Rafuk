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
        renderLeads,
        renderDealsHeroStats,
        renderProfitDashboard,
        showToast,
        confirmAction,
        formatDealAmount,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        // FR-18: shared lead / ad identity rules (dom_helpers.js).
        sameAdId,
        validVersion,
    } = context;

    let _analyticsAbortController = null;
    const pendingLeadActions = new Set();
    let clearingLeads = false;

    function _findLeadSnapshot(leadOrId) {
        const leadId = typeof leadOrId === "object" ? leadOrId?.id : leadOrId;
        return state.leads.items.find((l) => l.id === leadId) || null;
    }

    async function _resolveLeadVersion(leadOrId) {
        const snapshot = _findLeadSnapshot(leadOrId) || (typeof leadOrId === "object" ? leadOrId : null);
        const version = validVersion(snapshot?.version);
        if (version) return version;
        await loadLeads();
        return validVersion(_findLeadSnapshot(leadOrId)?.version);
    }

    function _showStaleLeadToast() {
        showToast("Данные устарели. Обновите список и попробуйте ещё раз.", "error");
    }

    function parseDealPrice(value) {
        const amount = value.trim().replace(/[\u00a0\u202f]/g, " ");
        if (!/^(?:\d+|\d{1,3}(?: \d{3})+)(?:[.,]\d{1,2})?$/.test(amount)) return null;
        const number = Number(amount.replaceAll(" ", "").replace(",", "."));
        return number > 0 && number <= 10_000_000 ? number : null;
    }

    // ── Load leads ───────────────────────────────────────────────────────
    async function loadLeads({ skipIfLoading = false, refreshAnalytics = false } = {}) {
        if (skipIfLoading && state.leads._loading) return;
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
        state.leads._loading = true;
        let nextLeads;
        try {
            nextLeads = await getJson("/api/v1/leads");
        } catch (error) {
            if (requestId !== state.leads._requestId) return;
            // RV-FE-05: a failed reload (offline, 5xx, the 401 that
            // follows initData's 2 h lifetime) used to become [] and read
            // as "no deals". Keep the last good list and let the list
            // show an error state with "Повторить" instead.
            state.leads._loading = false;
            state.leads.error = error?.message || "Не удалось загрузить покупки.";
            renderLeads();
            renderDealsHeroStats();
            renderProfitDashboard();
            return;
        }
        // Drop the response if a newer loadLeads() has started since.
        if (requestId !== state.leads._requestId) return;
        state.leads._loading = false;
        state.leads.error = null;
        state.leads.items = Array.isArray(nextLeads) ? nextLeads : [];
        context.refreshCollectionButtons?.();
        renderLeads();
        renderDealsHeroStats();
        renderProfitDashboard();
        // Refresh server-side analytics in parallel with the lead list —
        // the dashboard depends on lead-mutating endpoints (sale, expense)
        // so any reload of leads should also refresh aggregates.
        if (refreshAnalytics || !state.analytics.loading) {
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
            const response = await getJson(
                "/api/v1/analytics/leads",
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
        if (clearingLeads || pendingLeadActions.size) return;
        const activeLeads = state.leads.items.filter((l) => l.status !== "closed");
        if (!activeLeads.length) {
            showToast("Нет активных сделок для удаления");
            return;
        }
        clearingLeads = true;
        try {
            const count = activeLeads.length;
            if (!await confirmAction({
                title: "Очистить покупки?",
                message: `Записей к удалению — ${count}. Завершённые сделки останутся в истории.`,
                confirmLabel: "Очистить покупки",
                destructive: true,
            })) return;
            await deleteJson("/api/v1/leads/all");
            state.leads.items = state.leads.items.filter((l) => l.status === "closed");
            renderLeads();
            showToast("Покупки очищены");
            await loadLeads({ refreshAnalytics: true });
        } catch (error) {
            showToast(error.message || "Не удалось очистить список");
            await loadLeads({ refreshAnalytics: true });
        } finally {
            clearingLeads = false;
        }
    }

    // ── Confirm lead (sold stage with prices) ────────────────────────────
    async function confirmLead(lead, cardElement) {
        if (clearingLeads || pendingLeadActions.has(lead.id)) return;
        const buyPriceInput = cardElement.querySelector('[data-role="buy-price"]');
        const soldPriceInput = cardElement.querySelector('[data-role="sold-price"]');
        const priceError = cardElement.querySelector('[data-role="price-error"]');
        const buyPriceNum = parseDealPrice(buyPriceInput.value);
        const soldPriceNum = parseDealPrice(soldPriceInput.value);
        const invalidInput = buyPriceNum === null ? buyPriceInput : soldPriceNum === null ? soldPriceInput : null;
        buyPriceInput.setAttribute("aria-invalid", String(buyPriceNum === null));
        soldPriceInput.setAttribute("aria-invalid", String(soldPriceNum === null));
        priceError.hidden = !invalidInput;
        if (invalidInput) {
            const field = invalidInput === buyPriceInput ? "покупки" : "продажи";
            priceError.textContent = `Введите цену ${field} от 0,01 до 10 000 000 BYN. Например, 1000,50.`;
            invalidInput.focus();
            return;
        }
        priceError.textContent = "";

        pendingLeadActions.add(lead.id);
        try {
            const version = await _resolveLeadVersion(lead);
            if (!version) {
                _showStaleLeadToast();
                return;
            }
            if (!await confirmAction({
                title: "Подтвердить продажу?",
                message: `Покупка — ${formatDealAmount(buyPriceNum)}\u202fBYN\nПродажа — ${formatDealAmount(soldPriceNum)}\u202fBYN`,
                confirmLabel: "Подтвердить продажу",
            })) return;

            const payload = {
                buy_price_byn: buyPriceNum,
                sold_price_byn: soldPriceNum,
                status: "sold",
                version,
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

            showToast("Продажа подтверждена", "success");

            // Full reload to refresh analytics/profit dashboard data
            await loadLeads({ refreshAnalytics: true });

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
            showToast(error.message || "Не удалось подтвердить сделку", "error");
            await loadLeads({ refreshAnalytics: true });
        } finally {
            pendingLeadActions.delete(lead.id);
        }
    }

    // ── Close deal (move to history) ─────────────────────────────────────
    async function closeDeal(leadId) {
        if (clearingLeads || pendingLeadActions.has(leadId)) return;
        pendingLeadActions.add(leadId);
        try {
            const lead = state.leads.items.find((l) => l.id === leadId);
            const version = await _resolveLeadVersion(lead || leadId);
            if (!version) {
                _showStaleLeadToast();
                return;
            }
            const updatedLead = await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "closed",
                    version,
                }),
            });

            if (lead) {
                Object.assign(lead, { status: updatedLead.status, version: updatedLead.version, updated_at: updatedLead.updated_at });
            }
            renderLeads();

            showToast("Сделка завершена", "success");
        } catch (error) {
            showToast(error.message || "Не удалось закрыть сделку");
        } finally {
            pendingLeadActions.delete(leadId);
        }
    }

    // ── Revert a sale ────────────────────────────────────────────────────
    async function revertLeadStage(leadId) {
        if (clearingLeads || pendingLeadActions.has(leadId)) return;
        pendingLeadActions.add(leadId);
        try {
            const lead = _findLeadSnapshot(leadId);
            if (lead?.status !== "sold") return;
            const version = await _resolveLeadVersion(lead);
            if (!version) {
                _showStaleLeadToast();
                return;
            }
            if (!await confirmAction({
                title: "Отменить продажу?",
                message: "Цена продажи будет сброшена. Цена покупки и расходы сохранятся.",
                confirmLabel: "Отменить продажу",
            })) return;
            // The workflow API reverses the sale when only the sale price is cleared.
            const updatedLead = await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sold_price_byn: null,
                    version,
                }),
            });

            const leadInState = _findLeadSnapshot(leadId);
            if (leadInState) {
                Object.assign(leadInState, updatedLead);
            }
            renderLeads();

            showToast("Продажа отменена", "success");
            await loadLeads({ refreshAnalytics: true });

            setTimeout(() => {
                const updatedCard = elements.leadInboxList?.querySelector(
                    `[data-lead-id="${leadId}"]`
                );
                if (updatedCard) {
                    updatedCard.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }, 100);
        } catch (error) {
            if (error.status === 409) _showStaleLeadToast();
            else showToast("Не удалось отменить продажу. Попробуйте ещё раз.", "error");
            await loadLeads({ refreshAnalytics: true });
        } finally {
            pendingLeadActions.delete(leadId);
        }
    }

    // ── Delete single lead ───────────────────────────────────────────────
    async function deleteLead(leadId) {
        const lead = _findLeadSnapshot(leadId);
        if (!lead || clearingLeads || pendingLeadActions.has(leadId)) return;
        pendingLeadActions.add(leadId);
        try {
            const fromHistory = lead.status === "closed";
            if (!await confirmAction({
                title: fromHistory ? "Удалить сделку из истории?" : "Удалить сделку?",
                message: fromHistory
                    ? `«${lead.title}» исчезнет из истории и расчёта прибыли.`
                    : `«${lead.title}» будет удалено из покупок.`,
                confirmLabel: "Удалить",
                destructive: true,
            })) return;
            await deleteJson(`/api/v1/leads/${leadId}`);
            state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
            renderLeads();
            showToast("Сделка удалена");
            await loadLeads({ refreshAnalytics: true });
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        } finally {
            pendingLeadActions.delete(leadId);
        }
    }

    // ── Update lead metadata ─────────────────────────────────────────────
    async function updateLeadMeta(leadId, payload) {
        try {
            const lead = state.leads.items.find((l) => l.id === leadId);
            const version = payload.version ?? await _resolveLeadVersion(lead || leadId);
            if (!version) {
                _showStaleLeadToast();
                return false;
            }
            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    ...payload,
                    version,
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
            const version = await _resolveLeadVersion(lead);
            if (!version) {
                _showStaleLeadToast();
                return;
            }

            const updatedLead = await requestJson(`/api/v1/leads/${lead.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "sold",
                    sold_price_byn: priceNum,
                    version,
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
            showToast(`Сделка продана: ${profitSign}${Math.round(profit)} BYN`);
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
        return context.openListingDetail(lead, {
            referenceContext: "current",
            onLoaded(fullDetail) {
                // Mirror the server-side thumbnail backfill without reloading the list.
                const newThumb = fullDetail.images?.[0];
                if (!newThumb || lead.thumbnail || !state.leads?.items) return;
                const matchingLead = state.leads.items.find(
                    (l) => sameAdId(l.ad_id, lead.ad_id) && !l.thumbnail
                );
                if (matchingLead) {
                    matchingLead.thumbnail = newThumb;
                    renderLeads();
                }
            },
        });
    }

    return {
        loadLeads,
        loadAnalytics,
        clearAllLeads,
        confirmLead,
        cancelLead: deleteLead,
        closeDeal,
        revertLeadStage,
        deleteLead,
        updateLeadMeta,
        updateLeadStatus,
        markLeadAsSold,
        openLeadDetail,
    };
}

// OPUS-13: lazy-load registration; stub in the bundle proxies into
// window.App._realCreateApiLeads once this script lands.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateApiLeads = createApiLeads;
}
