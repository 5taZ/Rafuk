/**
 * render_modals.js — Listing detail, expenses and reminders.
 */

function createRenderModals(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatMarketDelta,
        formatCondition,
        formatSeller,
        trapFocus,
        safeKufarUrl,
        safeImageUrl,
        optimizedImage,
        // FR-18: shared lead / ad identity rules (dom_helpers.js).
        ACTIVE_LEAD_STATUSES,
        sameAdId,
        safeRender: safeRender,
        // OPUS-13 wave 74: dom_helpers ride through context because
        // this file loads as a lazy <script> in _lazy_deals_stub and
        // can't see bundle-IIFE-scoped callables.
        domClear,
        openModalAnimated,
        closeModalAnimated,
        attachPinchZoom,
    } = context;

    let _detailImageLoadId = 0;
    let _renderedDetailId = null;

    function buildDetailField(label, value) {
        const item = document.createElement("div");
        item.className = "detail-field";
        const labelEl = document.createElement("dt");
        labelEl.className = "detail-field-label";
        labelEl.textContent = label;
        const valueEl = document.createElement("dd");
        valueEl.className = "detail-field-value";
        valueEl.textContent = value;
        item.append(labelEl, valueEl);
        return item;
    }

    function _detailCollectionState(detail) {
        const adId = detail?.ad_id;
        const inLeads = (state.leads?.items || []).some(
            (lead) => sameAdId(lead.ad_id, adId) && ACTIVE_LEAD_STATUSES.has(lead.status),
        );
        const inWatchlist = !inLeads && (state.watchlist?.items || []).some((watch) => sameAdId(watch.ad_id, adId));
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

    function renderDetailMarket(detail) {
        domClear(elements.detailLiquidity);
        const liquidity = detail.liquidity?.basis === "current_market" ? detail.liquidity : null;
        if (liquidity) {
            const summary = document.createElement("div");
            summary.className = "detail-liquidity-summary";
            const label = document.createElement("strong");
            label.textContent = liquidity.label;
            summary.append(label);
            if (liquidity.sample_size > 0) {
                const sample = document.createElement("span");
                sample.className = "detail-liquidity-sample mono";
                sample.textContent = `${liquidity.sample_size} в выборке`;
                summary.appendChild(sample);
            }
            const source = document.createElement("p");
            source.className = "detail-liquidity-source";
            source.textContent = "Оценка по текущим объявлениям Kufar";
            elements.detailLiquidity.append(summary, source);

            const reasons = liquidity.reasons || [];
            if (reasons.length) {
                const list = document.createElement("ul");
                list.className = "detail-liquidity-reasons";
                for (const reason of reasons) {
                    const item = document.createElement("li");
                    item.textContent = String(reason);
                    list.appendChild(item);
                }
                elements.detailLiquidity.appendChild(list);
            }
        }
        if (!liquidity && (state.detail.marketLoading || state.detail.marketError)) {
            const status = document.createElement("p");
            status.className = "detail-loading";
            status.setAttribute("role", "status");
            status.textContent = state.detail.marketLoading ? "Оцениваем рынок…"
                : "Не удалось загрузить оценку. Откройте объявление ещё раз.";
            elements.detailLiquidity.appendChild(status);
        }
        elements.detailLiquidityBlock.hidden = !liquidity
            && !state.detail.marketLoading && !state.detail.marketError;

        domClear(elements.detailMeta);
        const comparison = detail.market_comparison;
        const marketDelta = comparison?.delta_percent == null ? "" : formatMarketDelta(comparison);
        if (marketDelta) {
            const market = document.createElement("div");
            market.className = "detail-market";
            if (Math.round(comparison.delta_percent) < 0) {
                market.classList.add("detail-market--below");
            }
            market.textContent = marketDelta;
            elements.detailMeta.appendChild(market);
        }
        const metaItems = [
            detail.category,
            detail.condition ? formatCondition(detail.condition) : "",
            detail.seller_type ? formatSeller(detail.seller_type) : "",
            comparison?.sample_limited ? "Часть рынка" : "",
        ].filter(Boolean);
        for (const item of metaItems) {
            const pill = document.createElement("span");
            pill.className = "detail-pill";
            pill.textContent = item;
            elements.detailMeta.appendChild(pill);
        }
    }

    /* ===== Detail Modal ===== */

    function renderDetailModal({ marketOnly = false } = {}) {
        return safeRender('renderDetailModal', () => {
            if (!state.detail.data) {
            // Don't toggle .hidden here — closeDetailModal animates it
            // out and a stray render call would otherwise abort that
            // transition. The modal starts hidden in HTML and is only
            // opened through an explicit openModalAnimated call below.
            return;
        }

        const detail = state.detail.data;
        renderDetailMarket(detail);
        if (marketOnly) return;

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

        const descriptionStatus = state.detail.descriptionLoading ? "Загружаем описание…"
            : state.detail.descriptionError ? "Описание временно недоступно." : "";
        elements.detailDescription.textContent = detail.description || descriptionStatus;
        elements.detailDescription.hidden = !elements.detailDescription.textContent;
        elements.detailDescription.classList.toggle("detail-loading", !detail.description);
        const descriptionRetry = elements.detailDescriptionRetry;
        if (descriptionRetry) {
            descriptionRetry.hidden = !state.detail.descriptionError || Boolean(detail.description);
            descriptionRetry.onclick = () => { void actions.retryDetailDescription(); };
        }

        // Flip estimates hidden — resale info now shown in AI analysis
        elements.detailProfitBlock.hidden = true;

        elements.detailMainImage.hidden = !hasImages;

        // Update gallery aria-label with current image index
        if (elements.detailMedia) {
            elements.detailMedia.hidden = !hasImages;
            const total = images.length || 1;
            const current = images.length ? (state.detail.imageIndex + 1) : 1;
            elements.detailMedia.setAttribute("aria-label", `Фото объявления ${current} из ${total}`);
            if (elements.detailPhotoCounter) elements.detailPhotoCounter.textContent = `${current} / ${total}`;
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
        // FR-06: thumbnails are named buttons and the active one is marked
        // for AT. A tap swaps only the main photo — re-rendering the whole
        // detail removed the focused button (focus fell to <body>).
        const thumbButtons = [];
        const syncThumbs = () => {
            thumbButtons.forEach((thumb, i) => {
                if (i === state.detail.imageIndex) {
                    thumb.classList.add("active");
                    thumb.setAttribute("aria-current", "true");
                } else {
                    thumb.classList.remove("active");
                    thumb.removeAttribute("aria-current");
                }
            });
            if (elements.detailMedia) {
                elements.detailMedia.setAttribute(
                    "aria-label",
                    `Фото объявления ${state.detail.imageIndex + 1} из ${images.length}`,
                );
                if (elements.detailPhotoCounter) elements.detailPhotoCounter.textContent = `${state.detail.imageIndex + 1} / ${images.length}`;
            }
        };
        for (const [index, image] of images.entries()) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "detail-thumb";
            button.setAttribute("aria-label", `Фото ${index + 1} из ${images.length}`);
            const img = document.createElement("img");
            img.src = optimizeWith(image, 120, false);
            img.alt = "";
            button.appendChild(img);
            button.addEventListener("click", () => {
                state.detail.imageIndex = index;
                setDetailMainImage(image, 800, detail.title || "Фото объявления");
                syncThumbs();
            });
            thumbButtons.push(button);
            elements.detailThumbs.appendChild(button);
        }
        if (thumbButtons.length) syncThumbs();

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
        const lead = state.leads?.items.find(item => sameAdId(item.ad_id, detail.ad_id));
        for (const button of [elements.detailExpensesButton, elements.detailRemindersButton]) {
            if (button) button.hidden = !lead;
        }
        const expenseButton = elements.detailExpensesButton;
        if (expenseButton) expenseButton.onclick = () => openExpensesModal(lead.id, lead.title);
        const reminderButton = elements.detailRemindersButton;
        if (reminderButton) reminderButton.onclick = () => openRemindersModal(lead.id, lead.title);
        const detailTitleText = detail.title || "товар";
        const leadText = collectionState.inLeads ? "В покупках" : "В покупки";
        const watchText = collectionState.inWatchlist ? "В избранном" : "В избранное";
        const watchDisabled = collectionState.inLeads || collectionState.inWatchlist;
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
            watchDisabled,
            collectionState.inWatchlist
                ? `«${detailTitleText}» уже в избранном`
                : collectionState.inLeads
                    ? `«${detailTitleText}» уже в покупках`
                    : `Добавить «${detailTitleText}» в избранное`,
        );

        const shouldOpenDetailModal = elements.detailModal.hidden
            || elements.detailModal.classList.contains("is-closing");

        const scrollContainer = elements.detailModal?.querySelector(".detail-sheet-content");
        const resetScroll = shouldOpenDetailModal || _renderedDetailId !== String(detail.ad_id);
        _renderedDetailId = String(detail.ad_id);

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
        if (resetScroll && scrollContainer) scrollContainer.scrollTop = 0;
        });
    }

    function closeDetailModal() {
        _renderedDetailId = null;
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
        state.detail.data = null;
        state.detail.marketLoading = false;
        state.detail.marketError = false;
        state.detail.descriptionLoading = false;
        state.detail.descriptionError = false;
        state.detail.imageIndex = 0;
        state.detail.fromWatchlist = false;
        state.detail.ai = {
            adId: null,
            loading: false,
            result: null,
            error: "",
            source: "",
        };
        closeModalAnimated(elements.detailModal, {
            onClosed: () => {
                elements.detailMainImage?._pinchController?.reset(false);
                elements.detailMainImage?.removeAttribute("src");
                if (typeof actions.clearProxyImageObjectUrls === "function") {
                    actions.clearProxyImageObjectUrls();
                }
            },
        });
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

        if (state.expenses.error) {
            const note = document.createElement("p");
            note.className = "form-error";
            note.setAttribute("role", "alert");
            note.textContent = state.expenses.error;
            const retry = document.createElement("button");
            retry.type = "button";
            retry.className = "ghost-btn small";
            retry.textContent = "Повторить загрузку";
            retry.onclick = () => { void actions.loadExpenses(state.expenses.currentLeadId); };
            elements.expensesList.append(note, retry);
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
            const typeLabels = { delivery: "Доставка", repair: "Ремонт", customs: "Таможня", packaging: "Упаковка", transport: "Транспорт", other: "Другое" };
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
            amountEl.textContent = `−${context.formatDealAmount(amount)} BYN`;
            const deleteBtn = document.createElement("button");
            deleteBtn.className = "expense-delete-btn";
            deleteBtn.type = "button";
            deleteBtn.setAttribute("aria-label", "Удалить расход");
            deleteBtn.textContent = "✕";
            deleteBtn.addEventListener("click", async () => {
                deleteBtn.disabled = true;
                try { await actions.deleteExpense(state.expenses.currentLeadId, expense.id); }
                finally { deleteBtn.disabled = false; }
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
        totalValue.textContent = `−${context.formatDealAmount(totalExpenses)} BYN`;
        totalRow.append(totalLabel, totalValue);
        elements.expensesList.prepend(totalRow);
        });
    }

    function openExpensesModal(leadId, leadTitle) {
        state.expenses.currentLeadId = leadId;
        state.expenses.items = [];
        state.expenses.error = "";
        if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
        if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
        const formError = document.getElementById("expense-form-error");
        if (formError) formError.hidden = true;
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
            elements.expensesModal.querySelector(".detail-sheet-content").scrollTop = 0;
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

    function renderReminders() {
        return safeRender("renderReminders", () => {
            const list = elements.reminderList;
            if (!list) return;
            const data = state.reminders;
            domClear(list);
            if (elements.reminderCount) {
                elements.reminderCount.textContent = data.loading ? "…" : String(data.items.length);
                elements.reminderCount.hidden = Boolean(data.error);
            }
            if (data.loading || data.error || !data.items.length) {
                const status = document.createElement("div");
                status.className = "reminder-list-state";
                const title = document.createElement("strong");
                title.textContent = data.loading ? "Загружаем напоминания…" : data.error ? "Не удалось загрузить список" : "Напоминаний пока нет";
                status.appendChild(title);
                if (!data.loading) {
                    const note = document.createElement("p");
                    note.textContent = data.error || "Добавьте первое — оно появится здесь.";
                    status.appendChild(note);
                }
                if (data.error) {
                    const retry = document.createElement("button");
                    retry.className = "ghost-btn";
                    retry.type = "button";
                    retry.textContent = "Повторить загрузку";
                    retry.onclick = () => { void actions.loadReminders(data.leadId); };
                    status.appendChild(retry);
                }
                list.appendChild(status);
                return;
            }
            const leadId = data.leadId;
            for (const item of data.items) {
                const date = new Date(item.remind_at);
                const card = document.createElement("article");
                card.className = "reminder-card";
                card.dataset.reminderId = item.id;
                card.tabIndex = -1;
                const header = document.createElement("div");
                header.className = "reminder-card-header";
                for (const [className, text] of [
                    ["reminder-card-date", date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric" })],
                    ["reminder-card-time", date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })],
                ]) {
                    const time = document.createElement("time");
                    time.className = className;
                    time.dateTime = item.remind_at;
                    time.textContent = text;
                    header.appendChild(time);
                }
                const message = document.createElement("p");
                message.className = "reminder-card-message";
                message.textContent = item.message || "Проверить сделку";
                const footer = document.createElement("div");
                footer.className = "reminder-card-footer";
                const status = document.createElement("span");
                const key = ["pending", "sent", "failed", "cancelled"].includes(item.status) ? item.status : "pending";
                status.className = `reminder-status reminder-status--${key}`;
                status.textContent = { pending: "Запланировано", sent: "Отправлено", failed: "Не доставлено", cancelled: "Отменено" }[key];
                const remove = document.createElement("button");
                remove.className = "reminder-cancel";
                remove.type = "button";
                remove.textContent = key === "pending" ? "Отменить" : "Удалить";
                remove.setAttribute("aria-label", `${remove.textContent} напоминание: ${item.message || "Проверить сделку"}`);
                remove.onclick = async () => {
                    remove.disabled = true;
                    try { await actions.deleteReminder(leadId, item); }
                    finally { remove.disabled = false; }
                };
                footer.append(status, remove);
                card.append(header, message, footer);
                list.appendChild(card);
            }
        });
    }

    function openRemindersModal(leadId, leadTitle) {
        if (!elements.remindersModal) return;
        state.reminders.sessionId++;
        elements.remindersSubtitle.textContent = leadTitle || "Сделка";
        const date = new Date();
        date.setDate(date.getDate() + 1);
        const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
        elements.reminderDateInput.value = local.toISOString().slice(0, 10);
        elements.reminderTimeInput.value = "10:00";
        elements.reminderDateInput.removeAttribute("aria-invalid");
        elements.reminderTimeInput.removeAttribute("aria-invalid");
        elements.reminderMessageInput.value = "";
        elements.reminderFormError.hidden = true;
        elements.reminderSubmitStatus.hidden = true;
        for (const chip of elements.remindersModal.querySelectorAll("[data-reminder-offset]")) {
            chip.setAttribute("aria-pressed", String(chip.dataset.reminderOffset === "1d"));
        }
        const zone = new Intl.DateTimeFormat("ru-RU", { timeZoneName: "short" }).formatToParts(date).find(part => part.type === "timeZoneName")?.value;
        const timezone = document.getElementById("reminder-timezone");
        if (timezone) timezone.textContent = `Время вашего устройства${zone ? ` · ${zone}` : ""}`;
        openModalAnimated(elements.remindersModal);
        elements.remindersModal.querySelector(".detail-sheet-content").scrollTop = 0;
        void actions.loadReminders(leadId);
    }

    function closeRemindersModal() {
        if (!elements.remindersModal || elements.remindersModal.hidden) return;
        state.reminders.leadId = null;
        state.reminders.sessionId++;
        state.reminders.requestId++;
        closeModalAnimated(elements.remindersModal);
    }

    return {
        renderDetailModal,
        closeDetailModal,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
        renderReminders,
        openRemindersModal,
        closeRemindersModal,
    };
}

// OPUS-13: lazy-load registration; stub in the bundle proxies into
// window.App._realCreateRenderModals once this script lands.
if (typeof window !== "undefined") {
    window.App = window.App || {};
    window.App._realCreateRenderModals = createRenderModals;
}
