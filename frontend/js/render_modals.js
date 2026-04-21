/**
 * render_modals.js — Detail modal, expenses modal, edit tracker modal.
 */

function createRenderModals(context) {
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

    /* ===== Detail Modal ===== */

    function renderDetailModal() {
        return safeRender('renderDetailModal', () => {
            if (!state.detail) {
            elements.detailModal.hidden = true;
            return;
        }

        const detail = state.detail;
        const images = detail.images || [];
        const hasImages = images.length > 0;
        const currentImage = hasImages ? images[state.detailImageIndex] || images[0] : null;

        elements.detailTitle.textContent = detail.title || "Объявление";
        elements.detailPrice.textContent = formatPrice(detail.price);
        elements.detailLink.href = detail.link || "#";

        const aiState = state.detailAi || {};
        if (elements.detailAiBlock && elements.detailAiContent) {
            const hasAiContext = aiState.adId === detail.ad_id;
            elements.detailAiBlock.hidden = !(
                (hasAiContext && (aiState.loading || aiState.error || aiState.result))
            );
            if (hasAiContext) {
                if (aiState.loading) {
                    elements.detailAiContent.innerHTML = '<div class="ai-loading">Анализирую объявление…</div>';
                } else if (aiState.error) {
                    elements.detailAiContent.innerHTML = `<div class="ai-error">${escapeHtml(aiState.error)}</div>`;
                }
            } else {
                elements.detailAiBlock.hidden = true;
                elements.detailAiContent.innerHTML = "";
            }
        }

        elements.detailDescription.textContent = detail.description || "";
        elements.detailDescription.hidden = !detail.description;

        elements.detailProfit.innerHTML = "";
        for (const estimate of detail.flip_estimates || []) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${escapeHtml(estimate.label)}</span>
                <span class="detail-field-value">${formatPrice(estimate.target_price)} • ${Math.round(estimate.profit_byn)} BYN (${estimate.profit_percent > 0 ? "+" : ""}${escapeHtml(estimate.profit_percent)}%)</span>
                <span class="detail-field-note">оценка</span>
            `;
            elements.detailProfit.appendChild(item);
        }
        elements.detailProfitBlock.hidden = (detail.flip_estimates || []).length === 0;
        // Add disclaimer to resale block
        if (elements.detailProfitBlock && !elements.detailProfitBlock.hidden) {
            const title = elements.detailProfitBlock.querySelector(".detail-block-title");
            if (title && !title.dataset.disclaimerAdded) {
                title.dataset.disclaimerAdded = "true";
                const note = document.createElement("span");
                note.className = "detail-disclaimer";
                note.textContent = " — оценка, не гарантия";
                title.appendChild(note);
            }
        }

        elements.detailLiquidity.innerHTML = "";
        if (detail.liquidity) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${escapeHtml(detail.liquidity.label)}</span>
                <span class="detail-field-value">${Math.round(detail.liquidity.score)} • ${(detail.liquidity.reasons || []).map(String).map(escapeHtml).join(" · ")}</span>
            `;
            elements.detailLiquidity.appendChild(item);
        }
        elements.detailLiquidityBlock.hidden = !detail.liquidity;

        const metaItems = [
            detail.category,
            detail.condition ? formatCondition(detail.condition) : "",
            detail.seller_type ? formatSeller(detail.seller_type) : "",
            detail.list_time ? formatDate(detail.list_time) : "",
            detail.fair_price_label || "",
            formatDelta(detail.price_vs_median),
        ].filter(Boolean);
        elements.detailMeta.innerHTML = metaItems.map((item) => `<span class="detail-pill">${escapeHtml(item)}</span>`).join("");

        elements.detailMainImage.hidden = !hasImages;
        elements.detailNoImage.hidden = hasImages;

        // Update gallery aria-label with current image index
        if (elements.detailMedia) {
            const total = images.length || 1;
            const current = images.length ? (state.detailImageIndex + 1) : 1;
            elements.detailMedia.setAttribute("aria-label", `Фото объявления ${current} из ${total}`);
        }

        if (currentImage) {
            elements.detailMainImage.src = currentImage;
            elements.detailMainImage.alt = detail.title || "Фото объявления";
        } else {
            elements.detailMainImage.removeAttribute("src");
        }

        elements.detailThumbs.innerHTML = "";
        for (const [index, image] of images.entries()) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `detail-thumb${state.detailImageIndex === index ? " active" : ""}`;
            button.innerHTML = `<img src="${escapeHtml(image)}" alt="">`;
            button.addEventListener("click", () => {
                state.detailImageIndex = index;
                renderDetailModal();
            });
            elements.detailThumbs.appendChild(button);
        }

        elements.detailParams.innerHTML = "";
        const params = detail.parameters || [];
        for (const field of params) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${escapeHtml(field.label)}</span>
                <span class="detail-field-value">${escapeHtml(field.value)}</span>
            `;
            elements.detailParams.appendChild(item);
        }
        elements.detailParamsBlock.hidden = params.length === 0;

        elements.detailSeller.innerHTML = "";
        const sellerFields = detail.seller_fields || [];
        for (const field of sellerFields) {
            const item = document.createElement("div");
            item.className = "detail-field";
            item.innerHTML = `
                <span class="detail-field-label">${escapeHtml(field.label)}</span>
                <span class="detail-field-value">${escapeHtml(field.value)}</span>
            `;
            elements.detailSeller.appendChild(item);
        }
        elements.detailSellerBlock.hidden = sellerFields.length === 0;

        void actions.loadDetailRisks(detail);

        // Hide "Следить" button if item is already in watchlist
        if (elements.detailAddWatchlistButton) {
            elements.detailAddWatchlistButton.hidden = state.detailFromWatchlist || false;
        }

        // Reset scroll position to top when modal opens
        if (elements.detailModalContent) {
            elements.detailModalContent.scrollTop = 0;
        } else if (elements.detailModal) {
            elements.detailModal.scrollTop = 0;
        }

        elements.detailModal.hidden = false;
        });
    }

    function renderDetailRisks(riskData) {
        return safeRender('renderDetailRisks', () => {
        elements.detailRisks.innerHTML = "";

        if (!riskData || !riskData.risks || riskData.risks.length === 0) {
            elements.detailRiskBlock.hidden = true;
            return;
        }

        const item = document.createElement("div");
        item.className = "detail-field";
        const overallEmoji = riskData.overall_emoji || "🟢";
        const overallLabel = {
            low: "Низкий риск",
            medium: "Средний риск",
            high: "Высокий риск",
        }[riskData.overall_risk] || riskData.overall_risk;

        const riskBadges = riskData.risks
            .map((risk) => {
                const levelClass = {
                    low: "risk-low",
                    medium: "risk-medium",
                    high: "risk-high",
                }[risk.level] || "";
                return `<span class="risk-badge ${levelClass}">${escapeHtml(risk.message)}</span>`;
            })
            .join("");

        item.innerHTML = `
            <span class="detail-field-label">${escapeHtml(overallEmoji)} ${escapeHtml(overallLabel)}</span>
            <div class="risk-badges-wrap">${riskBadges}</div>
        `;
        elements.detailRisks.appendChild(item);
        elements.detailRiskBlock.hidden = false;
        });
    }

    function closeDetailModal() {
        if (state.modalCleanup) {
            state.modalCleanup();
            state.modalCleanup = null;
        }
        state.detail = null;
        state.detailImageIndex = 0;
        state.detailFromWatchlist = false;
        state.detailAi = {
            adId: null,
            loading: false,
            result: null,
            error: "",
            source: "",
        };
        renderDetailModal();
    }

    /* ===== Expenses Modal ===== */

    function renderExpensesModal() {
        return safeRender('renderExpensesModal', () => {
            if (!elements.expensesModal) return;
            elements.expensesList.innerHTML = "";

        // Show loading state
        if (state.expensesLoading) {
            const loader = document.createElement("div");
            loader.className = "expenses-loading";
            loader.innerHTML = `
                <div class="skeleton-expense-row"></div>
                <div class="skeleton-expense-row"></div>
                <div class="skeleton-expense-row"></div>
            `;
            elements.expensesList.appendChild(loader);
            return;
        }

        if (!state.expenses.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Расходов пока нет.";
            elements.expensesList.appendChild(note);
            return;
        }

        let totalExpenses = 0;
        for (const expense of state.expenses) {
            const amount = Number(expense.amount_byn || 0);
            totalExpenses += amount;
            const row = document.createElement("div");
            row.className = "expense-row";
            const typeLabels = { delivery: "🚚 Доставка", repair: "🔧 Ремонт", other: "📦 Другое" };
            row.innerHTML = `
                <div class="expense-main">
                    <span class="expense-type">${typeLabels[expense.expense_type] || expense.expense_type}</span>
                    <span class="expense-meta">${expense.notes || ""}</span>
                </div>
                <span class="expense-amount mono">-${Math.round(amount)} BYN</span>
                <button class="expense-delete-btn" data-expense-id="${expense.id}" type="button" aria-label="Удалить расход">✕</button>
            `;
            row.querySelector('[data-expense-id]')?.addEventListener("click", () => {
                void actions.deleteExpense(state.currentExpenseLeadId, expense.id);
            });
            elements.expensesList.appendChild(row);
        }

        // Show total
        const totalRow = document.createElement("div");
        totalRow.className = "expense-total";
        totalRow.innerHTML = `
            <span class="expense-total-label">Итого расходов</span>
            <span class="expense-total-value mono">-${Math.round(totalExpenses)} BYN</span>
        `;
        elements.expensesList.prepend(totalRow);
        });
    }

    function openExpensesModal(leadId, leadTitle) {
        state.currentExpenseLeadId = leadId;
        state.expenses = [];
        if (elements.expensesSubtitle) {
            elements.expensesSubtitle.textContent = leadTitle;
            elements.expensesSubtitle.hidden = false;
        }
        if (elements.expensesModal) {
            if (state.modalCleanup) {
                state.modalCleanup();
                state.modalCleanup = null;
            }
            elements.expensesModal.hidden = false;
            state.modalCleanup = trapFocus(elements.expensesModal);
        }
        void actions.loadExpenses(leadId);
    }

    function closeExpensesModal() {
        if (state.modalCleanup) {
            state.modalCleanup();
            state.modalCleanup = null;
        }
        if (elements.expensesModal) {
            elements.expensesModal.hidden = true;
        }
        state.currentExpenseLeadId = null;
        state.expenses = [];
        if (elements.expenseTypeSelect) elements.expenseTypeSelect.value = "delivery";
        if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
        if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
    }

    return {
        renderDetailModal,
        renderDetailRisks,
        closeDetailModal,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
    };
}
