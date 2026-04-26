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

    // Cap on simultaneously-visible toasts. Anything past this count
    // dismisses the oldest one so the container can never blanket the
    // bottom of the screen during an error storm.
    const MAX_VISIBLE_TOASTS = 3;

    function showToast(message, type = "info", duration = 3000) {
        if (!elements.toastContainer) return null;

        const messageStr = String(message ?? "");

        // Deduplication: if the same (message, type) is already visible
        // and not already in the exit animation, just reset its timer
        // and bump a small "×N" counter on it instead of stacking a
        // duplicate. Cuts the noise when an action retries quickly.
        const existing = Array.from(
            elements.toastContainer.querySelectorAll(`.toast.toast-${type}`)
        ).find((node) => {
            if (node.classList.contains("toast-exit")) return false;
            const msgEl = node.querySelector(".toast-message");
            return msgEl && msgEl.textContent === messageStr;
        });
        if (existing) {
            const previousCount = Number(existing.dataset.toastCount || 1);
            const nextCount = previousCount + 1;
            existing.dataset.toastCount = String(nextCount);
            let badge = existing.querySelector(".toast-count");
            if (!badge) {
                badge = domEl("span", {
                    className: "toast-count",
                    attrs: { "aria-hidden": "true" },
                });
                existing.insertBefore(
                    badge,
                    existing.querySelector(".toast-close"),
                );
            }
            badge.textContent = `×${nextCount}`;
            // Reset the auto-dismiss timer so the latest occurrence
            // gets its full duration on screen.
            const prevTimer = Number(existing.dataset.dismissTimer || 0);
            if (prevTimer) clearTimeout(prevTimer);
            const nextTimer = setTimeout(() => dismissToast(existing), duration);
            existing.dataset.dismissTimer = String(nextTimer);
            return existing;
        }

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
            domEl("span", { className: "toast-message", text: messageStr }),
            domEl("button", {
                className: "toast-close",
                type: "button",
                text: "×",
                attrs: { "aria-label": "Закрыть уведомление" },
            }),
        );

        elements.toastContainer.appendChild(toast);

        // Cap on stacked toasts: if we just exceeded the limit, gently
        // dismiss the oldest one. We pick the first non-exiting node so
        // a toast already in its exit animation isn't fast-tracked
        // through twice.
        const visible = Array.from(
            elements.toastContainer.querySelectorAll(".toast:not(.toast-exit)")
        );
        if (visible.length > MAX_VISIBLE_TOASTS) {
            for (const node of visible) {
                if (node !== toast) {
                    dismissToast(node);
                    break;
                }
            }
        }

        // Remove entering class after animation completes
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const animationDuration = prefersReducedMotion ? 10 : 200;
        setTimeout(() => {
            toast.classList.remove("entering");
        }, animationDuration);

        const dismissTimer = setTimeout(() => dismissToast(toast), duration);
        toast.dataset.dismissTimer = String(dismissTimer);

        const closeBtn = toast.querySelector(".toast-close");
        closeBtn.addEventListener("click", () => {
            const timerId = Number(toast.dataset.dismissTimer || 0);
            if (timerId) clearTimeout(timerId);
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
        if (!toast || !toast.parentNode) return;
        if (toast.classList.contains("toast-exit")) return;
        const timerId = Number(toast.dataset.dismissTimer || 0);
        if (timerId) clearTimeout(timerId);
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

    /* ===== Empty state ===== */

    // Tiny SVG icon set for the rich empty states. Picked stroke-only
    // shapes that follow the same Lucide-ish line-weight as the tab
    // icons so the language stays consistent across the app.
    const _EMPTY_STATE_ICONS = {
        watchlist:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
        leads:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-4"/><polyline points="9 11 12 8 15 11"/><line x1="12" y1="2" x2="12" y2="14"/></svg>',
        trackers:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
        events:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
        search:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    };

    /**
     * Build a richer empty state — icon, title, hint, optional CTA —
     * for surfaces where a single dashed paragraph (.tracker-empty)
     * felt under-served. Returns a freshly-built DOM node ready to
     * appendChild into the section's container.
     *
     * @param {object} opts
     * @param {string} opts.icon         Key into _EMPTY_STATE_ICONS, or
     *                                   raw HTML to embed.
     * @param {string} opts.title        First line, bold.
     * @param {string} [opts.hint]       Second line, muted.
     * @param {string} [opts.actionLabel] CTA button label.
     * @param {Function} [opts.onAction] CTA click handler.
     */
    function buildEmptyState(opts) {
        const { icon, title, hint, actionLabel, onAction } = opts || {};
        const iconHtml = _EMPTY_STATE_ICONS[icon] || icon || "";
        const wrap = domEl("div", {
            className: "empty-state",
            attrs: { role: "status" },
        });
        if (iconHtml) {
            const iconBox = document.createElement("div");
            iconBox.className = "empty-state-icon";
            iconBox.innerHTML = iconHtml;
            wrap.appendChild(iconBox);
        }
        if (title) {
            wrap.appendChild(
                domEl("p", { className: "empty-state-title", text: title }),
            );
        }
        if (hint) {
            wrap.appendChild(
                domEl("p", { className: "empty-state-hint", text: hint }),
            );
        }
        if (actionLabel && typeof onAction === "function") {
            const button = domEl("button", {
                className: "empty-state-action",
                type: "button",
                text: actionLabel,
            });
            button.addEventListener("click", onAction);
            wrap.appendChild(button);
        }
        return wrap;
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
                button.tabIndex = isActive ? 0 : -1;
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

    function renderRefinementChips() {
        if (!elements.summaryRefinements || !elements.summaryRefinementsChips) return;
        const refinements = Array.isArray(state.stats?.suggested_refinements)
            ? state.stats.suggested_refinements
            : [];
        clearChildren(elements.summaryRefinementsChips);
        if (!refinements.length) {
            elements.summaryRefinements.hidden = true;
            return;
        }
        for (const token of refinements) {
            if (typeof token !== "string" || !token.trim()) continue;
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "summary-refinement-chip";
            chip.dataset.refinement = token;
            chip.textContent = `+ ${token}`;
            chip.title = `Добавить «${token}» к запросу`;
            elements.summaryRefinementsChips.appendChild(chip);
        }
        elements.summaryRefinements.hidden = false;
    }

    function renderSummary() {
        return safeRender('renderSummary', () => {
            if (!state.stats || !state.query) {
                elements.summaryStrip.hidden = true;
                elements.summaryQuery.textContent = "—";
                elements.summarySignal.textContent = "—";
                elements.summaryMedian.textContent = "—";
                elements.summaryRange.textContent = "—";
                elements.summaryFair.textContent = "—";
                if (elements.summaryRefinements) {
                    elements.summaryRefinements.hidden = true;
                    clearChildren(elements.summaryRefinementsChips);
                }
                return;
            }
            renderRefinementChips();

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
        buildEmptyState,
    };
}
