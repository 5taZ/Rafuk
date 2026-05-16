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
        safeKufarUrl,
        safeImageUrl,
        optimizedImage,
        safeRender: safeRender,
        escapeHtml,
        // OPUS-13 wave 74: dom_helpers ride through context because
        // this file loads as a lazy <script> in _lazy_deals_stub and
        // can't see bundle-IIFE-scoped callables.
        domClear,
        openModalAnimated,
        closeModalAnimated,
        attachPinchZoom,
    } = context;

    let _detailImageLoadId = 0;

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

    const ACTIVE_DETAIL_STATUSES = new Set([
        "new",
        "in_progress",
        "researching",
        "bought",
        "sold",
    ]);

    function _sameAdId(left, right) {
        return left != null && right != null && String(left) === String(right);
    }

    function _detailCollectionState(detail) {
        const adId = detail?.ad_id;
        const inLeads = (state.leads?.items || []).some(
            (lead) => _sameAdId(lead.ad_id, adId) && ACTIVE_DETAIL_STATUSES.has(lead.status),
        );
        const inWatchlist = !inLeads && (
            Boolean(state.detail.fromWatchlist)
            || (state.watchlist?.items || []).some((watch) => _sameAdId(watch.ad_id, adId))
        );
        return { inLeads, inWatchlist };
    }

    function _setDetailActionButton(button, text, disabled, label) {
        if (!button) return;
        button.hidden = false;
        button.textContent = text;
        button.disabled = Boolean(disabled);
        if (disabled) {
            button.setAttribute("aria-disabled", "true");
        } else {
            button.removeAttribute("aria-disabled");
        }
        button.setAttribute("aria-label", label);
    }

    /* ===== Detail Modal ===== */

    function renderDetailModal() {
        return safeRender('renderDetailModal', () => {
            if (!state.detail.data) {
            // Don't toggle .hidden here — closeDetailModal animates it
            // out and a stray render call would otherwise abort that
            // transition. The modal starts hidden in HTML and is only
            // opened through an explicit openModalAnimated call below.
            return;
        }

        const detail = state.detail.data;
        const images = detail.images || [];
        const hasImages = images.length > 0;
        const currentImage = hasImages ? images[state.detail.imageIndex] || images[0] : null;

        elements.detailTitle.textContent = detail.title || "Объявление";
        elements.detailPrice.textContent = formatPrice(detail.price, detail.price_type);
        const detailLink = safeKufarUrl(detail.link);
        if (detailLink) {
            elements.detailLink.href = detailLink;
            elements.detailLink.removeAttribute("aria-disabled");
        } else {
            elements.detailLink.removeAttribute("href");
            elements.detailLink.setAttribute("aria-disabled", "true");
        }

        const aiState = state.detail.ai || {};
        if (elements.detailAiBlock && elements.detailAiContent) {
            elements.detailAiBlock.hidden = true;
            domClear(elements.detailAiContent);
        }

        elements.detailDescription.textContent = detail.description || "";
        elements.detailDescription.hidden = !detail.description;

        // Flip estimates hidden — resale info now shown in AI analysis
        elements.detailProfitBlock.hidden = true;

        domClear(elements.detailLiquidity);
        if (detail.liquidity) {
            elements.detailLiquidity.appendChild(buildDetailField(
                "Оценка",
                `${detail.liquidity.label} · ${Math.round(detail.liquidity.score)}/100`
            ));
            const reasons = (detail.liquidity.reasons || []).map(String).filter(Boolean);
            if (reasons.length) {
                elements.detailLiquidity.appendChild(buildDetailField("Факторы", reasons.join(" · ")));
            }
        }
        elements.detailLiquidityBlock.hidden = !detail.liquidity;

        // Build the price-vs-market badge with the reference label so the user
        // sees WHAT the deviation is measured against (per-category fallback
        // is computed on the backend when the category has ≥3 ads).
        let priceVsMarketLabel = detail.fair_price_label || "";
        if (priceVsMarketLabel && detail.price_reference_scope === "category"
            && detail.price_reference_label) {
            priceVsMarketLabel += ` · ${detail.price_reference_label}`;
        } else if (priceVsMarketLabel && detail.price_reference_scope === "query") {
            priceVsMarketLabel += " · по запросу";
        }

        const metaItems = [
            detail.category,
            detail.condition ? formatCondition(detail.condition) : "",
            detail.seller_type ? formatSeller(detail.seller_type) : "",
            detail.list_time ? formatDate(detail.list_time) : "",
            priceVsMarketLabel,
            formatDelta(detail.price_vs_median),
        ].filter(Boolean);
        domClear(elements.detailMeta);
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
            const current = images.length ? (state.detail.imageIndex + 1) : 1;
            elements.detailMedia.setAttribute("aria-label", `Фото объявления ${current} из ${total}`);
        }

        const optimizeWith = (url, width, useProxy = false) => {
            const validated = safeImageUrl(url);
            if (!validated) return "";
            return typeof optimizedImage === "function"
                ? optimizedImage(validated, { width, useProxy })
                : validated;
        };

        function setDetailMainImage(url, width, alt) {
            const requestId = ++_detailImageLoadId;
            const directUrl = optimizeWith(url, width, false);
            const proxyUrl = optimizeWith(url, width, true);
            elements.detailMainImage.alt = alt;
            if (
                proxyUrl &&
                proxyUrl !== directUrl &&
                typeof actions.fetchProxyImageObjectUrl === "function"
            ) {
                if (directUrl) {
                    elements.detailMainImage.src = directUrl;
                }
                void actions.fetchProxyImageObjectUrl(proxyUrl).then((objectUrl) => {
                    if (requestId !== _detailImageLoadId || !state.detail.data) return;
                    elements.detailMainImage.src = objectUrl;
                }).catch(() => {
                    if (requestId !== _detailImageLoadId || !state.detail.data) return;
                    if (directUrl) {
                        elements.detailMainImage.src = directUrl;
                    } else if (!elements.detailMainImage.getAttribute("src")) {
                        elements.detailMainImage.removeAttribute("src");
                    }
                });
                return;
            }
            if (directUrl) {
                elements.detailMainImage.src = directUrl;
            } else {
                elements.detailMainImage.removeAttribute("src");
            }
        }

        if (currentImage) {
            setDetailMainImage(currentImage, 800, detail.title || "Фото объявления");
        } else {
            _detailImageLoadId++;
            elements.detailMainImage.removeAttribute("src");
        }

        domClear(elements.detailThumbs);
        for (const [index, image] of images.entries()) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `detail-thumb${state.detail.imageIndex === index ? " active" : ""}`;
            const img = document.createElement("img");
            img.src = optimizeWith(image, 120, false);
            img.alt = "";
            button.appendChild(img);
            button.addEventListener("click", () => {
                state.detail.imageIndex = index;
                renderDetailModal();
            });
            elements.detailThumbs.appendChild(button);
        }

        domClear(elements.detailParams);
        const params = detail.parameters || [];
        for (const field of params) {
            elements.detailParams.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailParamsBlock.hidden = params.length === 0;

        domClear(elements.detailSeller);
        const sellerFields = detail.seller_fields || [];
        for (const field of sellerFields) {
            elements.detailSeller.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailSellerBlock.hidden = sellerFields.length === 0;

        const collectionState = _detailCollectionState(detail);
        const detailTitleText = detail.title || "товар";
        const leadText = collectionState.inLeads ? "В покупках" : "В покупки";
        const watchText = collectionState.inWatchlist ? "В избранном" : "В избранное";
        _setDetailActionButton(
            elements.detailAddLeadButton,
            leadText,
            collectionState.inLeads,
            collectionState.inLeads
                ? `«${detailTitleText}» уже в покупках`
                : `Добавить «${detailTitleText}» в покупки`,
        );
        _setDetailActionButton(
            elements.detailAddWatchlistButton,
            watchText,
            collectionState.inWatchlist,
            collectionState.inWatchlist
                ? `«${detailTitleText}» уже в избранном`
                : collectionState.inLeads
                    ? `«${detailTitleText}» уже в покупках`
                    : `Добавить «${detailTitleText}» в избранное`,
        );

        const shouldOpenDetailModal = elements.detailModal.hidden;

        // Reset scroll position to top when modal opens
        const scrollContainer = elements.detailModal?.querySelector(".detail-sheet-content");
        if (shouldOpenDetailModal && scrollContainer) {
            scrollContainer.scrollTop = 0;
        }

        // Pinch-zoom: lazy-init once per session and stash the controller
        // on the image so closeDetailModal / navigateDetailImage can
        // reset the transform when the user moves on to a different lot
        // or photo. The helper itself is a no-op on desktop because the
        // touch events never fire — but it costs nothing to keep wired.
        if (
            elements.detailMainImage &&
            !elements.detailMainImage._pinchController &&
            typeof attachPinchZoom === "function"
        ) {
            elements.detailMainImage._pinchController = attachPinchZoom(
                elements.detailMainImage,
            );
        }
        if (elements.detailMainImage?._pinchController) {
            elements.detailMainImage._pinchController.reset(false);
        }

        if (shouldOpenDetailModal) {
            openModalAnimated(elements.detailModal);
        }
        });
    }

    function closeDetailModal() {
        _detailImageLoadId++;
        // FE-C4: drop the in-flight detail/AI fetch (if any) before
        // we tear the modal down. Otherwise a slow /listing-detail
        // call can resolve into already-cleared state.detail and
        // either flash the modal back open or trip a "Cannot read
        // properties of null" in renderDetailModal.
        if (typeof actions.abortDetailRequest === "function") {
            actions.abortDetailRequest();
        }
        if (state.misc.modalCleanup) {
            state.misc.modalCleanup();
            state.misc.modalCleanup = null;
        }
        // Drop any pinch-zoom transform so the next lot opens at 1×
        // even if the previous viewer left the photo magnified.
        if (elements.detailMainImage?._pinchController) {
            elements.detailMainImage._pinchController.reset(false);
        }
        if (typeof actions.clearProxyImageObjectUrls === "function") {
            actions.clearProxyImageObjectUrls();
        }
        elements.detailMainImage?.removeAttribute("src");
        state.detail.data = null;
        state.detail.imageIndex = 0;
        state.detail.fromWatchlist = false;
        state.detail.ai = {
            adId: null,
            loading: false,
            result: null,
            error: "",
            source: "",
        };
        // Animate close, THEN renderDetailModal will see state.detail = null
        // and the modal will already be hidden by the helper.
        closeModalAnimated(elements.detailModal);
    }

    /* ===== Expenses Modal ===== */

    function renderExpensesModal() {
        return safeRender('renderExpensesModal', () => {
            if (!elements.expensesModal) return;
            domClear(elements.expensesList);

        // Show loading state
        if (state.expenses.loading) {
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

        if (!state.expenses.items.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Расходов пока нет.";
            elements.expensesList.appendChild(note);
            return;
        }

        let totalExpenses = 0;
        for (const expense of state.expenses.items) {
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
                void actions.deleteExpense(state.expenses.currentLeadId, expense.id);
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
        state.expenses.currentLeadId = leadId;
        state.expenses.items = [];
        if (elements.expensesSubtitle) {
            elements.expensesSubtitle.textContent = leadTitle;
            elements.expensesSubtitle.hidden = false;
        }
        if (elements.expensesModal) {
            if (state.misc.modalCleanup) {
                state.misc.modalCleanup();
                state.misc.modalCleanup = null;
            }
            // FE-H4/UX-H1: openModalAnimated() already installs a
            // focus trap and stores the cleanup on the modal element
            // (_focusTrapCleanup), which closeModalAnimated() will run
            // on exit. Adding a second trap here caused two keydown
            // listeners to fight over Tab, and the outer cleanup never
            // ran because closeExpensesModal relies on closeModalAnimated.
            openModalAnimated(elements.expensesModal);
        }
        void actions.loadExpenses(leadId);
    }

    function closeExpensesModal() {
        if (state.misc.modalCleanup) {
            state.misc.modalCleanup();
            state.misc.modalCleanup = null;
        }
        if (elements.expensesModal) {
            closeModalAnimated(elements.expensesModal);
        }
        state.expenses.currentLeadId = null;
        state.expenses.items = [];
        if (elements.expenseTypeSelect) elements.expenseTypeSelect.value = "delivery";
        if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
        if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
    }

    return {
        renderDetailModal,
        closeDetailModal,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
    };
}

// OPUS-13: lazy-load registration; stub in the bundle proxies into
// window.App._realCreateRenderModals once this script lands.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateRenderModals = createRenderModals;
}
