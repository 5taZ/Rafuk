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
        formatDate,
        trapFocus,
        safeUrl: safeUrl,
        safeRender: safeRender,
    } = context;

    function clearChildren(node) {
        if (node) node.replaceChildren();
    }

    function buildDetailField(label, value) {
        const item = document.createElement("div");
        item.className = "detail-field";
        const labelEl = document.createElement("span");
        labelEl.className = "detail-field-label";
        labelEl.textContent = label;
        const valueEl = document.createElement("span");
        valueEl.className = "detail-field-value";
        valueEl.textContent = value;
        item.append(labelEl, valueEl);
        return item;
    }

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
        elements.detailLink.href = safeUrl(detail.link) || "#";

        const aiState = state.detailAi || {};
        if (elements.detailAiBlock && elements.detailAiContent) {
            elements.detailAiBlock.hidden = true;
            clearChildren(elements.detailAiContent);
        }

        elements.detailDescription.textContent = detail.description || "";
        elements.detailDescription.hidden = !detail.description;

        // Flip estimates hidden — resale info now shown in AI analysis
        elements.detailProfitBlock.hidden = true;

        clearChildren(elements.detailLiquidity);
        if (detail.liquidity) {
            const item = buildDetailField(
                detail.liquidity.label,
                `${Math.round(detail.liquidity.score)} • ${(detail.liquidity.reasons || []).map(String).join(" · ")}`
            );
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
        clearChildren(elements.detailMeta);
        for (const item of metaItems) {
            const pill = document.createElement("span");
            pill.className = "detail-pill";
            pill.textContent = item;
            elements.detailMeta.appendChild(pill);
        }

        elements.detailMainImage.hidden = !hasImages;
        elements.detailNoImage.hidden = hasImages;

        // Update gallery aria-label with current image index
        if (elements.detailMedia) {
            const total = images.length || 1;
            const current = images.length ? (state.detailImageIndex + 1) : 1;
            elements.detailMedia.setAttribute("aria-label", `Фото объявления ${current} из ${total}`);
        }

        if (currentImage) {
            const safeImage = safeUrl(currentImage);
            if (safeImage) {
                elements.detailMainImage.src = safeImage;
                elements.detailMainImage.alt = detail.title || "Фото объявления";
            } else {
                elements.detailMainImage.removeAttribute("src");
            }
        } else {
            elements.detailMainImage.removeAttribute("src");
        }

        clearChildren(elements.detailThumbs);
        for (const [index, image] of images.entries()) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `detail-thumb${state.detailImageIndex === index ? " active" : ""}`;
            const img = document.createElement("img");
            img.src = safeUrl(image);
            img.alt = "";
            button.appendChild(img);
            button.addEventListener("click", () => {
                state.detailImageIndex = index;
                renderDetailModal();
            });
            elements.detailThumbs.appendChild(button);
        }

        clearChildren(elements.detailParams);
        const params = detail.parameters || [];
        for (const field of params) {
            elements.detailParams.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailParamsBlock.hidden = params.length === 0;

        clearChildren(elements.detailSeller);
        const sellerFields = detail.seller_fields || [];
        for (const field of sellerFields) {
            elements.detailSeller.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailSellerBlock.hidden = sellerFields.length === 0;

        void actions.loadDetailRisks(detail);

        // Hide "Следить" button if item is already in watchlist
        if (elements.detailAddWatchlistButton) {
            elements.detailAddWatchlistButton.hidden = state.detailFromWatchlist || false;
        }

        // Reset scroll position to top when modal opens
        const scrollContainer = elements.detailModal?.querySelector(".detail-sheet-content");
        if (scrollContainer) {
            scrollContainer.scrollTop = 0;
        }

        elements.detailModal.hidden = false;
        document.body.classList.add("modal-open");
        });
    }

    function renderDetailRisks(riskData) {
        return safeRender('renderDetailRisks', () => {
        clearChildren(elements.detailRisks);

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

        const labelEl = document.createElement("span");
        labelEl.className = "detail-field-label";
        labelEl.textContent = `${overallEmoji} ${overallLabel}`;
        const badgesWrap = document.createElement("div");
        badgesWrap.className = "risk-badges-wrap";
        for (const risk of riskData.risks) {
            const badge = document.createElement("span");
            const levelClass = {
                low: "risk-low",
                medium: "risk-medium",
                high: "risk-high",
            }[risk.level] || "";
            badge.className = `risk-badge ${levelClass}`.trim();
            badge.textContent = risk.message;
            badgesWrap.appendChild(badge);
        }
        item.append(labelEl, badgesWrap);
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
        document.body.classList.remove("modal-open");
    }

    /* ===== Expenses Modal ===== */

    function renderExpensesModal() {
        return safeRender('renderExpensesModal', () => {
            if (!elements.expensesModal) return;
            clearChildren(elements.expensesList);

        // Show loading state
        if (state.expensesLoading) {
            const loader = document.createElement("div");
            loader.className = "expenses-loading";
            for (let i = 0; i < 3; i++) {
                const row = document.createElement("div");
                row.className = "skeleton-expense-row";
                loader.appendChild(row);
            }
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
            const main = document.createElement("div");
            main.className = "expense-main";
            const type = document.createElement("span");
            type.className = "expense-type";
            type.textContent = typeLabels[expense.expense_type] || expense.expense_type;
            const meta = document.createElement("span");
            meta.className = "expense-meta";
            meta.textContent = expense.notes || "";
            main.append(type, meta);
            const amountEl = document.createElement("span");
            amountEl.className = "expense-amount mono";
            amountEl.textContent = `-${Math.round(amount)} BYN`;
            const deleteBtn = document.createElement("button");
            deleteBtn.className = "expense-delete-btn";
            deleteBtn.type = "button";
            deleteBtn.setAttribute("aria-label", "Удалить расход");
            deleteBtn.textContent = "✕";
            deleteBtn.addEventListener("click", () => {
                void actions.deleteExpense(state.currentExpenseLeadId, expense.id);
            });
            row.append(main, amountEl, deleteBtn);
            elements.expensesList.appendChild(row);
        }

        // Show total
        const totalRow = document.createElement("div");
        totalRow.className = "expense-total";
        const totalLabel = document.createElement("span");
        totalLabel.className = "expense-total-label";
        totalLabel.textContent = "Итого расходов";
        const totalValue = document.createElement("span");
        totalValue.className = "expense-total-value mono";
        totalValue.textContent = `-${Math.round(totalExpenses)} BYN`;
        totalRow.append(totalLabel, totalValue);
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
