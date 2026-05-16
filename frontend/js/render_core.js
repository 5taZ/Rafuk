/**
 * render_core.js — Infrastructure renders: toast, error bar, loading,
 * view tabs, panels, summary, helper.
 */

function createRenderCore(context) {
    const { state, elements, logClientError = () => {} } = context;

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

    const _APP_URL_BASE = "https://rafuk.local";
    const _KUFAR_LINK_HOSTS = new Set(["kufar.by", "www.kufar.by", "re.kufar.by", "auto.kufar.by"]);
    const _KUFAR_GALLERY_PREFIX = "https://rms.kufar.by/v1/gallery/";
    const _KUFAR_GALLERY_PATH_PREFIX = "/v1/gallery/";

    function _sameOriginBase() {
        return (typeof window !== "undefined" && window.location?.origin)
            ? window.location.origin
            : _APP_URL_BASE;
    }

    function _parseSafeUrl(url, base) {
        if (!url || typeof url !== "string") return null;
        const trimmed = url.trim();
        if (!trimmed || trimmed.startsWith("//") || /[\u0000-\u001F\u007F]/.test(trimmed)) {
            return null;
        }
        try {
            return new URL(trimmed, base || _sameOriginBase());
        } catch (_) {
            return null;
        }
    }

    function _hasEncodedTraversal(pathname) {
        try {
            return decodeURIComponent(pathname).includes("..");
        } catch (_) {
            return true;
        }
    }

    // FE-URL-HARDEN: dynamic links/images can include backend or AI-provided
    // values, so validate by parsed origin/host instead of string prefixes.
    function safeUrl(url) {
        const trimmed = typeof url === "string" ? url.trim() : "";
        const isAbsolute = /^[a-z][a-z0-9+.-]*:/i.test(trimmed);
        const isRootRelative = trimmed.startsWith("/") && !trimmed.startsWith("//");
        if (!isAbsolute && !isRootRelative) return "";
        const base = _sameOriginBase();
        const parsed = _parseSafeUrl(trimmed, base);
        if (parsed && parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
        if (!parsed || parsed.origin !== base || parsed.username || parsed.password) return "";
        return isAbsolute ? parsed.href : `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }

    function safeKufarUrl(url) {
        const parsed = _parseSafeUrl(url);
        if (!parsed || parsed.protocol !== "https:" || parsed.username || parsed.password) {
            return "";
        }
        if (!_KUFAR_LINK_HOSTS.has(parsed.hostname)) return "";
        if (_hasEncodedTraversal(parsed.pathname)) return "";
        if (parsed.hostname === "auto.kufar.by" && !parsed.pathname.startsWith("/vi/")) {
            return "";
        }
        if (!["re.kufar.by", "auto.kufar.by"].includes(parsed.hostname) && !parsed.pathname.startsWith("/item/")) {
            return "";
        }
        if (parsed.pathname === "/") return "";
        return parsed.href;
    }

    function openExternalLink(url) {
        const parsed = _parseSafeUrl(url);
        if (!parsed || !["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
            return false;
        }
        const href = parsed.href;
        const tg = window.Telegram?.WebApp;
        if (tg?.openLink) {
            try {
                tg.openLink(href, { try_browser: true });
                return true;
            } catch (err) {
                logClientError("Telegram openLink failed, falling back to window.open", err, "warn");
            }
        }
        if (typeof window.open === "function") {
            window.open(href, "_blank", "noopener,noreferrer");
            return true;
        }
        window.location.href = href;
        return true;
    }

    function safeImageUrl(url) {
        const parsed = _parseSafeUrl(url);
        if (!parsed || parsed.protocol !== "https:" || parsed.username || parsed.password) {
            return "";
        }
        if (parsed.hostname !== "rms.kufar.by") return "";
        if (!parsed.pathname.startsWith(_KUFAR_GALLERY_PATH_PREFIX)) return "";
        if (_hasEncodedTraversal(parsed.pathname)) return "";
        return parsed.href;
    }

    // Image-proxy router. We had a proxy that transcoded Kufar's
    // JPEG to WebP/AVIF, but on first paint the user has to wait
    // for the proxy to fetch + Pillow-encode every thumbnail —
    // that's worse latency than just letting the browser pull the
    // original JPEG and rely on its native cache. The original is
    // ~25-40 % bigger but parses immediately on Telegram WebView.
    //
    // ``options.useProxy = true`` returns the authenticated router
    // URL for header-bearing fetch() callers (listing-detail large
    // photos). Plain <img> tags cannot send Telegram initData, so
    // card/list thumbnails stay direct CDN by default.
    function optimizedImage(url, options) {
        const validated = safeImageUrl(url);
        if (!validated) return "";
        const opts = options || {};
        if (!opts.useProxy) {
            // Pass-through — direct rms.kufar.by URL. Browser cache
            // (and the SW image-asset cache on the same path) does
            // the heavy lifting on repeat hits.
            return validated;
        }
        if (!validated.startsWith(_KUFAR_GALLERY_PREFIX)) return validated;
        const path = validated.slice(_KUFAR_GALLERY_PREFIX.length);
        if (!path || path.includes("..") || path.includes("?") || path.includes("#")) {
            return validated;
        }
        const width = Number.isFinite(opts.width) ? Math.round(opts.width) : null;
        const params = [];
        if (width && width > 0) params.push(`w=${width}`);
        const query = params.length ? `?${params.join("&")}` : "";
        return `/api/v1/img/${path}${query}`;
    }

    function safeRender(name, fn) {
        try {
            return fn();
        } catch (err) {
            logClientError(`[render] ${name} failed:`, err);
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

        const messageStr = String(message ?? "")
            .replace(/^[\s✓✔✅☑✕✖❌×↩←→★⭐❤🔥⚠\uFE0F]+/u, "")
            .trim();
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

        const toast = domEl(
            "div",
            {
                className: `toast toast-${type} entering`,
                attrs: { role: "status", "aria-live": "polite" },
            },
            domEl(
                "span",
                { className: "toast-body" },
                domEl("span", { className: "toast-message", text: messageStr }),
            ),
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
        const prefersReducedMotion = _prefersReducedMotion();
        const animationDuration = prefersReducedMotion ? 10 : 120;
        setTimeout(() => {
            toast.classList.remove("entering");
        }, animationDuration);

        const dismissTimer = setTimeout(() => dismissToast(toast), duration);
        toast.dataset.dismissTimer = String(dismissTimer);

        // FE-M11: pause the auto-dismiss timer while the user is
        // hovering the toast or has keyboard focus inside it. The
        // remaining time after a pause/resume cycle is what was left
        // when the pause started — so a user who hovers a 3 s toast
        // 1 s in and lets go after another 5 s still gets 2 s to
        // read the message before it slides out. ``pointerenter`` /
        // ``pointerleave`` fire on the same element regardless of
        // mouse vs touch (touch hovers don't fire on iOS Safari
        // mid-tap, but we restart on ``focusout`` from the close
        // button anyway). Mouse-only listeners are intentional;
        // touch users dismiss with the × button or wait it out.
        let _remainingMs = duration;
        let _pauseStart = 0;
        function _pauseDismiss() {
            const timerId = Number(toast.dataset.dismissTimer || 0);
            if (!timerId) return;
            clearTimeout(timerId);
            toast.dataset.dismissTimer = "0";
            _pauseStart = Date.now();
        }
        function _resumeDismiss() {
            if (!_pauseStart) return;
            _remainingMs = Math.max(400, _remainingMs - (Date.now() - _pauseStart));
            _pauseStart = 0;
            const next = setTimeout(() => dismissToast(toast), _remainingMs);
            toast.dataset.dismissTimer = String(next);
        }
        toast.addEventListener("pointerenter", _pauseDismiss);
        toast.addEventListener("pointerleave", _resumeDismiss);
        toast.addEventListener("focusin", _pauseDismiss);
        toast.addEventListener("focusout", _resumeDismiss);

        const closeBtn = toast.querySelector(".toast-close");
        closeBtn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            const timerId = Number(toast.dataset.dismissTimer || 0);
            if (timerId) clearTimeout(timerId);
            dismissToast(toast);
        });

        const _haptic = window.Telegram?.WebApp;
        if (_haptic?.HapticFeedback && (!_haptic.version || parseFloat(_haptic.version) >= 6.1)) {
            if (type === "success") {
                _haptic.HapticFeedback.notificationOccurred("success");
            } else if (type === "error") {
                _haptic.HapticFeedback.notificationOccurred("error");
            }
        }

        return toast;
    }

    function dismissToast(toast) {
        if (!toast || !toast.parentNode) return;
        if (toast.classList.contains("toast-exit")) return;
        const timerId = Number(toast.dataset.dismissTimer || 0);
        if (timerId) clearTimeout(timerId);
        const prefersReducedMotion = _prefersReducedMotion();
        const exitDuration = prefersReducedMotion ? 10 : 120;
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
            const message = typeof state.ui.error === "string" ? state.ui.error.trim() : "";
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

    /**
     * Build a richer empty state — title, hint, optional CTA —
     * for surfaces where a single dashed paragraph (.tracker-empty)
     * felt under-served. Returns a freshly-built DOM node ready to
     * appendChild into the section's container.
     *
     * @param {object} opts
     * @param {string} opts.title        First line, bold.
     * @param {string} [opts.hint]       Second line, muted.
     * @param {string} [opts.actionLabel] CTA button label.
     * @param {Function} [opts.onAction] CTA click handler.
     */
    function buildEmptyState(opts) {
        const { title, hint, actionLabel, onAction } = opts || {};
        const wrap = domEl("div", {
            className: "empty-state",
            attrs: { role: "status" },
        });
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
            const query = state.search.query.trim();
            const showOverviewLoading = Boolean(state.ui.loading && query);
            elements.searchButton.disabled = state.ui.loading || !state.search.query.trim();
            elements.searchInput.disabled = state.ui.loading;
            if (elements.overviewLoading) {
                elements.overviewLoading.hidden = !showOverviewLoading;
                if (showOverviewLoading) {
                    elements.overviewLoading.setAttribute("aria-busy", "true");
                } else {
                    elements.overviewLoading.removeAttribute("aria-busy");
                }
            }
            if (elements.overviewLoadingQuery) {
                elements.overviewLoadingQuery.textContent = query ? `Загружаю обзор по запросу «${query}»` : "Загружаю обзор";
            }
            if (elements.views?.overview) {
                if (showOverviewLoading) {
                    elements.views.overview.setAttribute("aria-busy", "true");
                } else {
                    elements.views.overview.removeAttribute("aria-busy");
                }
            }
            if (state.ui.loading) {
                elements.searchButtonLabel.replaceChildren(domEl("span", { className: "spin" }));
                elements.listingsSection?.setAttribute('aria-busy', 'true');
                elements.statsSection?.setAttribute('aria-busy', 'true');

                // Show skeleton cards in listing containers during initial load
                if (!state.listings.items.length && elements.listingsList) {
                    domClear(elements.listingsList);
                    for (let i = 0; i < 3; i++) {
                        elements.listingsList.appendChild(buildSkeletonCard());
                    }
                    elements.listingsSection.hidden = false;
                }
            } else {
                elements.searchButtonLabel.textContent = "Найти";
                elements.listingsSection?.removeAttribute('aria-busy');
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
                elements.strictSearchToggle.checked = state.search.strictSearch;
            }
        });
    }

    /* ===== View Tabs ===== */

    function renderViewTabs() {
        return safeRender('renderViewTabs', () => {
            for (const button of elements.viewTabs) {
                const isActive = button.dataset.view === state.ui.activeView;
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

    const MAX_REFINEMENT_CHIPS = 4;

    function normaliseRefinementText(value) {
        return String(value ?? "")
            .toLowerCase()
            .replace(/[^0-9a-zа-яё]+/gi, " ")
            .trim()
            .replace(/\s+/g, " ");
    }

    function queryContainsRefinement(query, token) {
        const normalizedQuery = normaliseRefinementText(query);
        const normalizedToken = normaliseRefinementText(token);
        return !!normalizedToken && ` ${normalizedQuery} `.includes(` ${normalizedToken} `);
    }

    function visibleRefinementTokens(refinements, query) {
        const seen = new Set();
        const visible = [];
        for (const raw of refinements) {
            const token = typeof raw === "string" ? raw.trim().replace(/\s+/g, " ") : "";
            const key = normaliseRefinementText(token);
            if (!key || seen.has(key) || queryContainsRefinement(query, token)) continue;
            seen.add(key);
            visible.push(token);
            if (visible.length >= MAX_REFINEMENT_CHIPS) break;
        }
        return visible;
    }

    function renderRefinementChips() {
        if (!elements.summaryRefinements || !elements.summaryRefinementsChips) return;
        const refinements = Array.isArray(state.misc.stats?.suggested_refinements)
            ? state.misc.stats.suggested_refinements
            : [];
        const visibleRefinements = visibleRefinementTokens(refinements, state.search.query);
        domClear(elements.summaryRefinementsChips);
        if (!visibleRefinements.length) {
            elements.summaryRefinements.hidden = true;
            return;
        }
        for (const token of visibleRefinements) {
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
            if (!state.misc.stats || !state.search.query) {
                elements.summaryStrip.hidden = true;
                elements.summaryQuery.textContent = "—";
                elements.summarySignal.textContent = "—";
                elements.summaryMedian.textContent = "—";
                elements.summaryRange.textContent = "—";
                elements.summaryFair.textContent = "—";
                if (elements.summaryRefinements) {
                    elements.summaryRefinements.hidden = true;
                    domClear(elements.summaryRefinementsChips);                }
                return;
            }
            renderRefinementChips();

            const totalResults = Number(state.misc.stats.total_results || 0);
            const analyzedCount = Number(state.misc.stats.analyzed_count || state.misc.stats.count || 0);
            const marketMedian = Number(state.misc.stats.median || 0);
            const marketMean = Number(state.misc.stats.mean || 0);
            const marketMin = Number(state.misc.stats.min || 0);
            const marketMax = Number(state.misc.stats.max || 0);
            const fairFrom = state.misc.stats.fair_price_from != null ? Number(state.misc.stats.fair_price_from) : null;
            const fairTo = state.misc.stats.fair_price_to != null ? Number(state.misc.stats.fair_price_to) : null;
            const spreadRatio = marketMedian > 0 ? (marketMax - marketMin) / marketMedian : 0;
            const meanDeltaRatio = marketMedian > 0 ? Math.abs(marketMean - marketMedian) / marketMedian : 0;
            let signal = "Рынок читается ровно, медиана подходит как главный ориентир.";
            // SEARCH-10: an empty result set used to fall through to
            // the "Выборка маленькая" branch (analyzedCount < 5),
            // implying analytics still apply. Make the zero case
            // explicit so the user reads "ничего не найдено" instead
            // of guessing why every stat shows "—".
            if (totalResults === 0 && analyzedCount === 0) {
                signal = "По запросу ничего не найдено. Откройте «Объявления» и попробуйте снять фильтры или изменить запрос.";
            } else if (analyzedCount < 5) {
                signal = "Выборка маленькая, смотрите объявления и сравнивайте вручную.";
            } else if (spreadRatio > 0.8 || meanDeltaRatio > 0.12) {
                signal = "Рынок неоднородный: сначала смотрите медиану, затем историю и сегменты.";
            } else if (totalResults > analyzedCount * 1.6) {
                signal = "Часть рынка без цены, ориентируйтесь на медиану и полный список объявлений.";
            }

            elements.summaryQuery.textContent = state.search.query;
            elements.summarySignal.textContent = signal;
            function shortPrice(v) {
                if (v == null || v === 0) return "—";
                const n = Number(v);
                if (Number.isNaN(n)) return "—";
                return `${Math.round(n)} BYN`;
            }

            elements.summaryMedian.textContent = shortPrice(state.misc.stats.median);
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
                !state.ui.loading &&
                !state.ui.error &&
                !state.misc.stats &&
                state.ui.activeView !== "tracking" &&
                state.ui.activeView !== "monitoring" &&
                state.ui.activeView !== "deals";
            elements.helperPanel.hidden = !shouldShow;
        });
    }

    /* ===== Views ===== */

    function renderViews() {
        return safeRender('renderViews', () => {
            for (const [name, panel] of Object.entries(elements.views)) {
                if (!panel) continue;
                panel.hidden = state.ui.activeView !== name;
            }
        });
    }

    return {
        escapeHtml,
        safeUrl,
        safeKufarUrl,
        openExternalLink,
        safeImageUrl,
        optimizedImage,
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
