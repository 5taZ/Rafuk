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
        getJson,
        postJson,
        deleteJson,
        requestJson,
    } = context;

    // ── Load leads ───────────────────────────────────────────────────────
    async function loadLeads() {
        if (!hasTelegramInitData()) {
            state.leads = [];
            renderLeads();
            renderDealsHeroStats();
            renderProfitDashboard();
            return;
        }
        try {
            state.leads = await getJson("/api/v1/leads");
        } catch (_) {
            state.leads = [];
        } finally {
            renderLeads();
            renderDealsHeroStats();
            renderProfitDashboard();
        }
    }

    // ── Clear all leads (active only) ────────────────────────────────────
    async function clearAllLeads() {
        const activeLeads = state.leads.filter((l) => l.status !== "closed");
        if (!activeLeads.length) {
            showToast("Нет активных сделок для удаления");
            return;
        }
        try {
            await deleteJson("/api/v1/leads/all");
            const count = activeLeads.length;
            await loadLeads();
            renderDealsHeroStats();
            showToast(`Удалено ${count} сделок`);
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

        if (!Number.isInteger(soldPriceNum) || soldPriceNum <= 0) {
            showToast("Введите целую цену продажи больше 0");
            soldPriceInput?.focus();
            return;
        }

        try {
            const payload = {
                buy_price_byn: buyPriceNum,
                sold_price_byn: soldPriceNum,
                status: "sold",
            };

            await requestJson(`/api/v1/leads/${lead.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            const leadInState = state.leads.find((l) => l.id === lead.id);
            if (leadInState) {
                leadInState.buy_price_byn = buyPriceNum;
                leadInState.sold_price_byn = soldPriceNum;
                leadInState.status = "sold";
            }

            renderLeads();

            const profit = soldPriceNum - buyPriceNum;
            const profitSign = profit >= 0 ? "+" : "";
            showToast(`✓ Сделка подтверждена! ${profitSign}${Math.round(profit)} BYN`, "success");

            setTimeout(() => {
                const updatedCard = elements.leadInboxList?.querySelector(
                    `[data-lead-id="${lead.id}"]`
                );
                if (updatedCard) {
                    updatedCard.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }, 100);
        } catch (error) {
            showToast(error.message || "Не удалось подтвердить сделку");
        }
    }

    // ── Cancel lead (delete) ─────────────────────────────────────────────
    async function cancelLead(leadId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}`);
            showToast("✓ Сделка отменена", "info");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось отменить сделку");
        }
    }

    // ── Close deal (mark closed, show profit) ────────────────────────────
    async function closeDeal(leadId) {
        try {
            const lead = state.leads.find((l) => l.id === leadId);
            const buyPriceByn = lead?.buy_price_byn || 0;
            const soldPriceByn = lead?.sold_price_byn || 0;

            const profit = soldPriceByn - buyPriceByn;

            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status: "closed" }),
            });

            if (lead) {
                lead.status = "closed";
            }
            renderLeads();

            const profitSign = profit >= 0 ? "+" : "";
            if (profit >= 0) {
                showToast(`✓ Сделка закрыта. Прибыль: ${profitSign}${Math.round(profit)} BYN`);
            } else {
                showToast(`✓ Сделка закрыта. Убыль: ${Math.round(profit)} BYN`);
            }
        } catch (error) {
            showToast(error.message || "Не удалось закрыть сделку");
        }
    }

    // ── Revert lead stage (back to new) ──────────────────────────────────
    async function revertLeadStage(leadId, currentStatus) {
        try {
            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "new",
                    buy_price_byn: null,
                    sold_price_byn: null,
                }),
            });

            const leadInState = state.leads.find((l) => l.id === leadId);
            if (leadInState) {
                leadInState.status = "new";
                leadInState.buy_price_byn = null;
                leadInState.sold_price_byn = null;
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
            showToast("Сделка удалена");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        }
    }

    // ── Update lead metadata ─────────────────────────────────────────────
    async function updateLeadMeta(leadId, payload) {
        try {
            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
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

        await requestJson(`/api/v1/leads/${lead.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                status: "sold",
                sold_price_byn: priceNum,
            }),
        });

        const leadInState = state.leads.find((l) => l.id === lead.id);
        if (leadInState) {
            leadInState.status = "sold";
            leadInState.sold_price_byn = priceNum;
        }
        renderLeads();

        const priceBynRaw = lead.price_byn || 0;
        const profit = priceNum - priceBynRaw;
        const profitSign = profit >= 0 ? "+" : "";
        showToast(`✓ Сделка продана! Прибыль: ${profitSign}${Math.round(profit)} BYN`);
    }

    // ── Open lead detail modal ───────────────────────────────────────────
    async function openLeadDetail(lead) {
        if (!lead?.ad_id) {
            showToast("Не удалось открыть: нет ID объявления");
            return;
        }
        const queryToUse = lead.query || state.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        showToast("Загружаю...");
        state.error = null;
        renderError();
        try {
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.currency}&strict_search=${state.strictSearch}&ad_id=${lead.ad_id}`
            );
            state.detail = fullDetail;
            state.detailImageIndex = 0;
            state.detailFromWatchlist = false;
            renderDetailModal();
        } catch (error) {
            state.error = error.message || "Не удалось загрузить детали";
            renderError();
        }
    }

    return {
        loadLeads,
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
