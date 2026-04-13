/**
 * render_core.js — Infrastructure renders: toast, error bar, loading, currency,
 * view tabs, panels, summary, helper, rates.
 */

function createRenderCore(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatRate,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
    } = context;

    /**
     * Safe render wrapper — catches and logs errors instead of crashing.
     */
    function safeRender(name, fn) {
        try {
            return fn();
        } catch (_) {
            if (typeof showToast === 'function') {
                showToast("Ошибка отображения", 'error');
            }
            return null;
        }
    }

    /**
     * Escape HTML special characters to prevent XSS attacks.
     * Uses a singleton DOM element to avoid creating new elements on every call.
     */
    const _escapeDiv = document.createElement("div");

    function escapeHtml(str) {
        if (str == null) return "";
        _escapeDiv.textContent = String(str);
        return _escapeDiv.innerHTML;
    }

    /* ===== Toast ===== */

    function showToast(message, type = "info", duration = 3000) {
        if (!elements.toastContainer) return;

        const toast = document.createElement("div");
        toast.className = `toast toast-${type}`;
        toast.setAttribute("role", "status");
        toast.setAttribute("aria-live", "polite");

        const iconMap = {
            success: "✓",
            error: "✕",
            info: "ℹ",
        };

        toast.innerHTML = `
            <span class="toast-icon ${type}">${iconMap[type] || iconMap.info}</span>
            <span class="toast-message">${message}</span>
            <button class="toast-close" aria-label="Закрыть уведомление">×</button>
        `;

        elements.toastContainer.appendChild(toast);

        const dismissTimer = setTimeout(() => dismissToast(toast), duration);

        const closeBtn = toast.querySelector(".toast-close");
        closeBtn.addEventListener("click", () => {
            clearTimeout(dismissTimer);
            dismissToast(toast);
        });

        if (window.Telegram?.WebApp?.HapticFeedback) {
            if (type === "success") {
                Telegram.WebApp.HapticFeedback.notificationOccurred("success");
            } else if (type === "error") {
                Telegram.WebApp.HapticFeedback.notificationOccurred("error");
            }
        }
    }

    function dismissToast(toast) {
        if (!toast.parentNode) return;
        toast.classList.add("toast-exit");
        setTimeout(() => {
            if (toast.parentNode) {
                toast.remove();
            }
        }, 200);
    }

    /* ===== Rates ===== */

    function renderRates() {
        return; // removed — currency always BYN
    }

    /* ===== Error ===== */

    function renderError() {
        return safeRender('renderError', () => {
            const message = typeof state.error === "string" ? state.error.trim() : "";
            if (!message) {
                elements.errorBar.hidden = true;
                elements.errorBar.classList.remove("is-visible");
                elements.errorText.textContent = "";
                return;
            }
            elements.errorText.textContent = message;
            elements.errorBar.hidden = false;
            elements.errorBar.classList.add("is-visible");
        });
    }

    /* ===== Loading ===== */

    /**
     * Build a skeleton card element for loading placeholder.
     */
    function buildSkeletonCard() {
        const card = document.createElement("div");
        card.className = "skeleton-card skeleton";
        card.setAttribute("aria-hidden", "true");
        card.innerHTML = '<div class="skeleton-text" style="width:70%"></div><div class="skeleton-text" style="width:45%"></div>';
        return card;
    }

    function renderLoading() {
        return safeRender('renderLoading', () => {
            elements.searchButton.disabled = state.loading || !state.query.trim();
            elements.searchInput.disabled = state.loading;
            if (state.loading) {
                elements.searchButtonLabel.innerHTML = '<span class="spin"></span>';
                elements.listingsSection?.setAttribute('aria-busy', 'true');
                elements.dealsSection?.setAttribute('aria-busy', 'true');
                elements.statsSection?.setAttribute('aria-busy', 'true');

                // Show skeleton cards in listing containers during initial load
                if (!state.listings.length && elements.listingsList) {
                    elements.listingsList.innerHTML = "";
                    for (let i = 0; i < 3; i++) {
                        elements.listingsList.appendChild(buildSkeletonCard());
                    }
                    elements.listingsSection.hidden = false;
                }
                if (!state.dealListings.length && elements.dealsList) {
                    elements.dealsList.innerHTML = "";
                    for (let i = 0; i < 3; i++) {
                        elements.dealsList.appendChild(buildSkeletonCard());
                    }
                    elements.dealsSection.hidden = false;
                }
            } else {
                elements.searchButtonLabel.textContent = "Найти";
                elements.listingsSection?.removeAttribute('aria-busy');
                elements.dealsSection?.removeAttribute('aria-busy');
                elements.statsSection?.removeAttribute('aria-busy');
            }
        });
    }

    /* ===== Currency ===== */

    function renderCurrencyButtons() {
        return; // removed — currency always BYN
    }

    /* ===== Strict Search ===== */

    function renderStrictSearch() {
        return safeRender('renderStrictSearch', () => {
            if (elements.strictSearchToggle) {
                elements.strictSearchToggle.checked = state.strictSearch;
            }
        });
    }

    /* ===== View Tabs ===== */

    function renderViewTabs() {
        return safeRender('renderViewTabs', () => {
            for (const button of elements.viewTabs) {
                button.classList.toggle("active", button.dataset.view === state.activeView);
            }
        });
    }

    /* ===== Panels ===== */

    function renderPanels() {
        return safeRender('renderPanels', () => {
            for (const button of elements.panelToggles) {
                const panelName = button.dataset.panelToggle;
                const isOpen = Boolean(state.panels[panelName]);
                const body = elements.panelBodies[panelName];

                if (body) {
                    body.hidden = !isOpen;
                }

                button.textContent = isOpen ? "Свернуть" : "Показать";
                button.setAttribute("aria-expanded", String(isOpen));
                button.classList.toggle("is-open", isOpen);
            }
        });
    }

    function setPanelOpen(panelName, isOpen, skipLoad) {
        if (!(panelName in state.panels)) {
            return;
        }

        state.panels[panelName] = Boolean(isOpen);
        renderPanels();

        if (panelName === "distribution") {
            if (state.panels.distribution) {
                // renderChart is provided via cross-module hooks
                if (context._hooks?.renderChart) context._hooks.renderChart();
            } else {
                if (context._hooks?.destroyChart) context._hooks.destroyChart();
            }
        }

        if (panelName === "history") {
            if (state.panels.history) {
                if (context._hooks?.renderHistory) context._hooks.renderHistory();
            } else {
                if (context._hooks?.destroyHistoryChart) context._hooks.destroyHistoryChart();
            }
        }
    }

    /* ===== Summary ===== */

    function renderSummary() {
        return safeRender('renderSummary', () => {
            if (!state.stats || !state.query) {
                elements.summaryStrip.hidden = true;
                elements.summaryQuery.textContent = "—";
                elements.summarySignal.textContent = "—";
                elements.summaryMedian.textContent = "—";
                elements.summaryMarketTotal.textContent = "—";
                elements.summaryCoverage.textContent = "—";
                return;
            }

            const totalResults = Number(state.stats.total_results || 0);
            const analyzedCount = Number(state.stats.analyzed_count || state.stats.count || 0);
            const marketMedian = Number(state.stats.median || 0);
            const marketMean = Number(state.stats.mean || 0);
            const marketMin = Number(state.stats.min || 0);
            const marketMax = Number(state.stats.max || 0);
            const spreadRatio = marketMedian > 0 ? (marketMax - marketMin) / marketMedian : 0;
            const meanDeltaRatio = marketMedian > 0 ? Math.abs(marketMean - marketMedian) / marketMedian : 0;
            let signal = "Рынок читается ровно, медиана подходит как главный ориентир.";
            if (analyzedCount < 5) {
                signal = "Выборка маленькая, смотрите объявления и сравнивайте вручную.";
            } else if (spreadRatio > 0.8 || meanDeltaRatio > 0.12) {
                signal = "Рынок неоднородный: сначала смотрите медиану, затем историю и сегменты.";
            } else if (totalResults > analyzedCount * 1.6) {
                signal = "Часть рынка без цены, ориентируйтесь на медиану и полный список объявлений.";
            }

            elements.summaryQuery.textContent = state.query;
            elements.summarySignal.textContent = signal;
            elements.summaryMedian.textContent = formatPrice(state.stats.median);
            elements.summaryMarketTotal.textContent = String(totalResults || 0);
            elements.summaryCoverage.textContent =
                `${analyzedCount} / ${totalResults}`;
            elements.summaryStrip.hidden = false;
        });
    }

    /* ===== Helper ===== */

    function renderHelper() {
        return safeRender('renderHelper', () => {
            const shouldShow =
                !state.loading &&
                !state.error &&
                !state.stats &&
                state.activeView !== "tracking" &&
                state.activeView !== "monitoring" &&
                state.activeView !== "deals";
            elements.helperPanel.hidden = !shouldShow;
        });
    }

    /* ===== Views ===== */

    function renderViews() {
        return safeRender('renderViews', () => {
            for (const [name, panel] of Object.entries(elements.views)) {
                panel.hidden = state.activeView !== name;
            }
        });
    }

    return {
        escapeHtml,
        showToast,
        dismissToast,
        renderRates,
        renderError,
        renderLoading,
        renderCurrencyButtons,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
    };
}
