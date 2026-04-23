/**
 * render_core.js — Infrastructure renders: toast, error bar, loading,
 * view tabs, panels, summary, helper.
 */

function createRenderCore(context) {
    const { state, elements } = context;

    /**
     * Escape HTML special characters to prevent XSS attacks.
     * Uses a singleton DOM element to avoid creating new elements on every call.
     */
    function escapeHtml(str) {
        if (str == null) return "";
        return String(str).replace(/[&<>"']/g, (char) => {
            const map = {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#39;",
            };
            return map[char] || char;
        });
    }

    /**
     * Validate URL is safe for href/src attributes.
     * Blocks javascript:, data:, and other dangerous schemes.
     */
    function safeUrl(url) {
        if (!url || typeof url !== "string") return "";
        const trimmed = url.trim().toLowerCase();
        if (trimmed.startsWith("https://") || trimmed.startsWith("http://")) {
            return url;
        }
        return "";
    }

    function safeRender(name, fn) {
        try {
            return fn();
        } catch (err) {
            console.error(`[render] ${name} failed:`, err);
            return null;
        }
    }

    /* ===== Toast ===== */

    function showToast(message, type = "info", duration = 3000) {
        if (!elements.toastContainer) return null;

        const iconMap = {
            success: "✓",
            error: "✕",
            info: "ℹ",
        };

        const toast = domEl(
            "div",
            {
                className: `toast toast-${type} entering`,
                attrs: { role: "status", "aria-live": "polite" },
            },
            domEl("span", { className: `toast-icon ${type}`, text: iconMap[type] || iconMap.info }),
            domEl("span", { className: "toast-message", text: message }),
            domEl("button", {
                className: "toast-close",
                type: "button",
                text: "×",
                attrs: { "aria-label": "Закрыть уведомление" },
            }),
        );

        elements.toastContainer.appendChild(toast);

        // Remove entering class after animation completes
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const animationDuration = prefersReducedMotion ? 10 : 200;
        setTimeout(() => {
            toast.classList.remove("entering");
        }, animationDuration);

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

        return toast;
    }

    function dismissToast(toast) {
        if (!toast.parentNode) return;
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const exitDuration = prefersReducedMotion ? 10 : 200;
        toast.classList.add("toast-exit");
        setTimeout(() => {
            if (toast.parentNode) {
                toast.remove();
            }
        }, exitDuration);
    }

    /* ===== Rates ===== */
    // Removed — currency is always BYN

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
        card.className = "skeleton-card";
        card.setAttribute("aria-hidden", "true");
        card.appendChild(
            domFragment(
                domEl("div", { className: "skeleton-image" }),
                domEl(
                    "div",
                    { className: "skeleton-content" },
                    domEl("div", { className: "skeleton-text skeleton-title" }),
                    domEl("div", { className: "skeleton-text skeleton-subtitle" }),
                    domEl("div", { className: "skeleton-text skeleton-price" }),
                ),
            )
        );
        return card;
    }

    function renderLoading() {
        return safeRender('renderLoading', () => {
            elements.searchButton.disabled = state.loading || !state.query.trim();
            elements.searchInput.disabled = state.loading;
            if (state.loading) {
                elements.searchButtonLabel.replaceChildren(domEl("span", { className: "spin" }));
                elements.listingsSection?.setAttribute('aria-busy', 'true');
                elements.dealsSection?.setAttribute('aria-busy', 'true');
                elements.statsSection?.setAttribute('aria-busy', 'true');

                // Show skeleton cards in listing containers during initial load
                if (!state.listings.length && elements.listingsList) {
                    domClear(elements.listingsList);
                    for (let i = 0; i < 3; i++) {
                        elements.listingsList.appendChild(buildSkeletonCard());
                    }
                    elements.listingsSection.hidden = false;
                }
                if (!state.dealListings.length && elements.dealsList) {
                    domClear(elements.dealsList);
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
    // Removed — currency is always BYN

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
                const isActive = button.dataset.view === state.activeView;
                button.classList.toggle("active", isActive);
                button.setAttribute("aria-selected", String(isActive));
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
                elements.summaryRange.textContent = "—";
                elements.summaryFair.textContent = "—";
                return;
            }

            const totalResults = Number(state.stats.total_results || 0);
            const analyzedCount = Number(state.stats.analyzed_count || state.stats.count || 0);
            const marketMedian = Number(state.stats.median || 0);
            const marketMean = Number(state.stats.mean || 0);
            const marketMin = Number(state.stats.min || 0);
            const marketMax = Number(state.stats.max || 0);
            const fairFrom = state.stats.fair_price_from != null ? Number(state.stats.fair_price_from) : null;
            const fairTo = state.stats.fair_price_to != null ? Number(state.stats.fair_price_to) : null;
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
            function shortPrice(v) {
                if (v == null || v === 0) return "—";
                const n = Number(v);
                if (Number.isNaN(n)) return "—";
                if (n >= 1000) {
                    const k = n / 1000;
                    return `${k % 1 === 0 ? k : k.toFixed(1)}к`;
                }
                return `${Math.round(n)} р.`;
            }

            elements.summaryMedian.textContent = shortPrice(state.stats.median);
            elements.summaryRange.textContent = marketMin > 0 && marketMax > 0
                ? `${shortPrice(marketMin)} — ${shortPrice(marketMax)}`
                : "—";
            elements.summaryFair.textContent = fairFrom != null && fairTo != null
                ? `${shortPrice(fairFrom)} — ${shortPrice(fairTo)}`
                : "—";
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
        safeUrl,
        showToast,
        dismissToast,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
    };
}
