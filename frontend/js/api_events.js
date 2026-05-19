/**
 * api_events.js — All DOM event binding for the application.
 *
 * This module wires up every interactive element: search inputs, buttons,
 * chips, tabs, toggles, modals, filters, swipes, keyboard shortcuts, and
 * cross-app lifecycle triggers. It delegates all business logic to the action
 * functions passed through context.
 */

function _haptic(type = "light") {
    try { _tgHaptic()?.impactOccurred?.(type); } catch (_) {}
}

function createApiEvents(context) {
    const logClientError = context.logClientError || (() => {});
    const {
        state,
        elements,
        trapFocus,
        renderAll,
        renderLoading,
        renderStrictSearch,
        renderSortButtons,
        renderDiscountButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderHistory,
        renderProfitDashboard,
        renderTrackerEventFilters,
        renderTrackerEvents,
        renderTrackingHeroStats,
        renderLeads,
        renderWatchlist,
        setActiveView,
        setPanelOpen,
        closeDetailModal,
        closeEditTracker,
        closeExpensesModal,
        showToast,
        openExternalLink,
        buildCommonQuery,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        // Action functions delegated from other modules
        search,
        loadListings,
        loadHistory,
        loadTrackers,
        loadLeads,
        loadAnalytics,
        clearAllLeads,
        loadWatchlist,
        clearAllWatchlist,
        createTracker,
        deleteTracker,
        pauseTracker,
        resumeTracker,
        openEditTracker,
        closeEditTracker: closeEditTrackerAction,
        saveTracker,
        openListingDetail,
        openLeadDetail,
        openWatchlistDetail,
        confirmLead,
        cancelLead,
        closeDeal,
        revertLeadStage,
        deleteLead,
        markLeadAsSold,
        updateWatchlistStatus,
        promoteWatchlistToLead,
        deleteWatchlistItem,
        deleteAllWatchlist,
        refreshWatchlist,
        refreshLeads,
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeadsCSV,
        loadProfile,
        openOverview,
        openProfile,
        openAdmin,
        canUseAiFeature,
        loadAdminUsers,
        updateUserStatus,
        loadAdminStatuses,
        updateStatusLimits,
        loadAdminAudit,
        loadAIAnalysis,
        closeAIModal,
        startTrackerRefresh,
        stopTrackerRefresh,
    } = context;

    // ── Event binding ────────────────────────────────────────────────────
    const _searchDebounce = { timer: null };
    const _confirmTimers = {};
    const _preloadCache = [];
    let _filterCloseTimeout = null;
    // FE-NEW-8: keep dedup state in module scope, not on document.
    let _escapeHandler = null;

    function _parseFilterPrice(value) {
        const trimmed = value == null ? "" : String(value).trim();
        if (trimmed === "") {
            return null;
        }
        const numeric = Number(trimmed);
        return Number.isFinite(numeric) ? Math.max(0, numeric) : null;
    }

    function _normaliseRefinementText(value) {
        return String(value ?? "")
            .toLowerCase()
            .replace(/[^0-9a-zа-яё]+/gi, " ")
            .trim()
            .replace(/\s+/g, " ");
    }

    function _queryContainsRefinement(query, token) {
        const normalizedQuery = _normaliseRefinementText(query);
        const normalizedToken = _normaliseRefinementText(token);
        return !!normalizedToken && ` ${normalizedQuery} `.includes(` ${normalizedToken} `);
    }

    function bindSearchEvents() {
        // ── Search input ─────────────────────────────────────────────
        elements.searchInput?.addEventListener("input", () => {
            state.search.query = elements.searchInput.value.trim();
            renderLoading();

            clearTimeout(_searchDebounce.timer);
            _searchDebounce.timer = setTimeout(() => {
                if (state.search.query.length >= 2) {
                    void search("overview");

                    _haptic("light");
                }
            }, 1200); // 1.2s debounce — gives users time to finish typing
        });

        elements.searchInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                clearTimeout(_searchDebounce.timer);
                void search("overview");

                _haptic("medium");
            }
        });

        elements.searchButton?.addEventListener("click", () => {
            clearTimeout(_searchDebounce.timer);
            void search("overview");

            _haptic("medium");
        });

        // ── Error bar retry ────────────────────────────────────────────
        elements.errorRetry?.addEventListener("click", () => {
            state.ui.error = null;
            void search(state.ui.activeView || "overview", { forceRefresh: true });
        });

    }

    function bindRecentSearchEvents() {
        // ── Recent searches (chips + clear button) ────────────────────
        elements.recentSection?.addEventListener("click", (event) => {
            if (event.target.closest("#recent-clear-btn")) {
                // 1. Instant visual feedback — clear state and hide immediately
                state.search.recentSearches = [];

                // 2. Defer localStorage write to next tick (non-blocking)
                requestAnimationFrame(() => {
                    context.saveRecentSearches();
                });

                // 3. Don't call renderRecentSearches() — we handle it directly
                // to avoid double-render and ensure instant response
                if (elements.recentSection) elements.recentSection.hidden = true;
                if (elements.recentList) domClear(elements.recentList);

                // 4. Optional haptic feedback
                _haptic("light");

                return;
            }
            const chip = event.target.closest("[data-recent-query]");
            if (chip) {
                const query = chip.dataset.recentQuery || "";
                elements.searchInput.value = query;
                state.search.query = query;
                renderLoading();
                clearTimeout(_searchDebounce.timer);
                void search("overview");
            }
        });

        // ── Welcome / helper panel chips ─────────────────────────────
        elements.helperPanel?.addEventListener("click", (event) => {
            const chip = event.target.closest("[data-recent-query]");
            if (chip) {
                const query = chip.dataset.recentQuery || "";
                if (!query) return;
                elements.searchInput.value = query;
                state.search.query = query;
                renderLoading();
                clearTimeout(_searchDebounce.timer);
                void search("overview");
                return;
            }
            const shortcut = event.target.closest("[data-view-shortcut]");
            if (shortcut) {
                const view = shortcut.dataset.viewShortcut;
                if (view) {
                    setActiveView(view);
                    renderAll();
                    requestAnimationFrame(() => {
                        document
                            .getElementById("listing-assistant-section")
                            ?.scrollIntoView({ behavior: "smooth", block: "start" });
                    });
                }
            }
        });

        // ── Refinement chips (summary strip) ─────────────────────────
        elements.summaryRefinements?.addEventListener("click", (event) => {
            const chip = event.target.closest("[data-refinement]");
            if (!chip) return;
            const token = chip.dataset.refinement || "";
            if (!token) return;
            const current = (state.search.query || "").trim();
            // Avoid duplicating the token if it's already in the query.
            const merged = _queryContainsRefinement(current, token)
                ? current
                : `${current} ${token}`.trim();
            if (merged === current) return;
            elements.searchInput.value = merged;
            state.search.query = merged;
            renderLoading();
            clearTimeout(_searchDebounce.timer);
            _haptic("light");
            void search("overview");
        });
    }

    function bindViewTabEvents() {
        // ── Strict search toggle ─────────────────────────────────────
        elements.strictSearchToggle?.addEventListener("change", () => {
            state.search.strictSearch = Boolean(elements.strictSearchToggle.checked);
            renderStrictSearch();
            if (state.search.query.trim()) {
                void search(state.ui.activeView);
            }
        });
        // ── View tabs ────────────────────────────────────────────────
        const tablist = document.querySelector('[role="tablist"].view-nav');
        if (tablist) bindRovingTablist(tablist);
        for (const button of elements.viewTabs || []) {
            button.addEventListener("click", () => {
                const view = button.dataset.view;
                if (!view) {
                    return;
                }
                _preloadCache.length = 0;
                Object.values(_confirmTimers).forEach(clearTimeout);
                _confirmTimers.events = undefined;
                _confirmTimers.leads = undefined;
                _confirmTimers.watchlist = undefined;
                setActiveView(view);
                renderAll();
                if (view === "tracking") {
                    void loadTrackers();
                    startTrackerRefresh();
                } else {
                    stopTrackerRefresh();
                }
                if (view === "deals") {
                    // Unified "Мои объявления" — load both leads and
                    // watchlist (formerly the Избранное view) in parallel
                    // so any filter chip ("Слежу" / "В работе" / …)
                    // has data to render against.
                    void loadLeads();
                    void loadWatchlist();
                }
            });
        }

        // ── Items filter tabs (Избранное / Покупки within deals view) ──
        if (elements.itemsFilterRow) bindRovingTablist(elements.itemsFilterRow);
        for (const button of elements.itemsFilterButtons || []) {
            button.addEventListener("click", () => {
                const filter = button.dataset.itemsFilter;
                if (!filter) return;
                state.leads.itemsFilter = filter;
                renderLeads();

                _haptic("light");
            });
        }

        // ── Panel toggles (collapsible sections) ─────────────────────
        for (const button of elements.panelToggles || []) {
            button.addEventListener("click", () => {
                const panelName = button.dataset.panelToggle;
                if (!panelName) {
                    return;
                }
                setPanelOpen(panelName, !state.panels[panelName]);
            });
        }

        // ── History range buttons ────────────────────────────────────
        for (const button of elements.historyRangeButtons || []) {
            button.addEventListener("click", () => {
                const nextDays = Number(button.dataset.historyDays);
                if (!nextDays || nextDays === state.misc.historyDays) {
                    return;
                }
                state.misc.historyDays = nextDays;
                renderHistory();
                void loadHistory();
            });
        }

        // ── Sort buttons ─────────────────────────────────────────────
        for (const button of elements.sortButtons || []) {
            button.addEventListener("click", () => {
                const sort = button.dataset.sort || "newest";
                if (sort === state.search.sort) {
                    return;
                }
                state.search.sort = sort;
                renderSortButtons();
                // Discount-range controls live below the sort row in the
                // ads view. They're only meaningful for sort=cheap; the
                // hidden attribute toggles their visibility in sync with
                // the sort selection.
                if (elements.dealsControls) {
                    elements.dealsControls.hidden = sort !== "cheap";
                }
                if (state.search.query.trim()) {
                    void loadListings(true);
                }
            });
        }
    }

    function bindProfileAdminEvents() {
        let adminQueryTimer = null;
        elements.profileChip?.addEventListener("click", () => {
            openProfile({ refresh: !state.profile.data });
            _haptic("light");
        });
        elements.profileContent?.addEventListener("click", (event) => {
            const action = event.target.closest("[data-profile-action]")?.dataset.profileAction;
            if (!action) return;
            if (action === "admin") {
                void openAdmin();
            } else if (action === "overview") {
                openOverview();
            } else if (action === "retry") {
                void loadProfile({ silent: false, retry: false });
            }
            _haptic("light");
        });
        elements.adminBackProfileButton?.addEventListener("click", () => {
            openProfile();
            _haptic("light");
        });
        elements.adminUsersRefreshButton?.addEventListener("click", () => {
            void loadAdminUsers({ retry: false });
            _haptic("light");
        });
        elements.adminStatusesRefreshButton?.addEventListener("click", () => {
            void loadAdminStatuses({ retry: false });
            _haptic("light");
        });
        elements.adminAuditRefreshButton?.addEventListener("click", () => {
            state.admin.auditOffset = 0;
            void loadAdminAudit({ retry: false });
            _haptic("light");
        });
        elements.adminAuditList?.addEventListener("click", (event) => {
            if (!event.target.closest("[data-admin-audit-more]")) return;
            state.admin.auditOffset = (state.admin.auditEntries || []).length;
            void loadAdminAudit({ silent: true });
        });
        elements.adminUserQuery?.addEventListener("input", () => {
            state.admin.query = elements.adminUserQuery.value.trim();
            clearTimeout(adminQueryTimer);
            adminQueryTimer = setTimeout(() => {
                void loadAdminUsers({ silent: true, retry: false });
            }, 350);
        });
        elements.adminUserQuery?.addEventListener("keydown", (event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            clearTimeout(adminQueryTimer);
            state.admin.query = elements.adminUserQuery.value.trim();
            void loadAdminUsers({ retry: false });
        });
        elements.adminStatusFilter?.addEventListener("change", () => {
            state.admin.selectedStatus = elements.adminStatusFilter.value || "";
            void loadAdminUsers({ silent: true, retry: false });
        });
        elements.adminUsersList?.addEventListener("click", (event) => {
            const card = event.target.closest("[data-admin-user-card]");
            if (!card) return;
            const telegramUserId = Number(card.dataset.telegramUserId);
            if (!Number.isFinite(telegramUserId)) return;
            if (event.target.closest("[data-admin-edit-user]")) {
                state.admin.editingUserId = telegramUserId;
                context.markDirty("adminUsers");
                renderAll();
                return;
            }
            if (event.target.closest("[data-admin-cancel-user]")) {
                state.admin.editingUserId = null;
                context.markDirty("adminUsers");
                renderAll();
                return;
            }
            if (!event.target.closest("[data-admin-save-user]")) return;
            const statusCode = card.querySelector("[data-admin-user-status]")?.value || "";
            const expiresRaw = card.querySelector("[data-admin-user-expires]")?.value || "";
            const note = card.querySelector("[data-admin-user-note]")?.value?.trim() || null;
            if (!statusCode) {
                showToast("Выберите статус", "error");
                return;
            }
            let expiresAt = null;
            if (expiresRaw) {
                const parsed = new Date(expiresRaw);
                if (Number.isNaN(parsed.getTime())) {
                    showToast("Некорректная дата окончания", "error");
                    return;
                }
                expiresAt = parsed.toISOString();
            }
            void updateUserStatus(telegramUserId, { status_code: statusCode, expires_at: expiresAt, note });
        });
        elements.adminStatusesList?.addEventListener("click", (event) => {
            const card = event.target.closest("[data-admin-status-card]");
            if (!card || !event.target.closest("[data-admin-save-status]")) return;
            const code = card.dataset.statusCode || "";
            const aiLimit = Number(card.querySelector("[data-admin-status-ai]")?.value || 0);
            const assistantLimit = Number(card.querySelector("[data-admin-status-assistant]")?.value || 0);
            if (!Number.isInteger(aiLimit) || !Number.isInteger(assistantLimit) || aiLimit < 0 || assistantLimit < 0) {
                showToast("Лимиты должны быть целыми числами от 0", "error");
                return;
            }
            void updateStatusLimits(code, {
                ai_daily_limit: aiLimit,
                assistant_daily_limit: assistantLimit,
            });
        });
    }

    function bindFilterEvents() {
        // ── Filter button (toggle dropdown) ───────────────────────────
        elements.filterBtn?.addEventListener("click", () => {
            const prefersReducedMotion = _prefersReducedMotion();
            if (!state.filters.filterDropdownOpen) {
                if (_filterCloseTimeout) { clearTimeout(_filterCloseTimeout); _filterCloseTimeout = null; }
                state.filters.pendingCategory = state.filters.category;
                state.filters.pendingCondition = state.filters.condition;
                state.filters.pendingSellerType = state.filters.sellerType;
                state.filters.pendingMinPrice = state.filters.minPrice;
                state.filters.pendingMaxPrice = state.filters.maxPrice;
                state.filters.pendingRegionName = state.filters.regionName;
                state.filters.filterDropdownOpen = true;
                renderAll();
            } else if (prefersReducedMotion) {
                state.filters.filterDropdownOpen = false;
                renderAll();
            } else {
                elements.filterDropdown?.classList.add("closing");
                _filterCloseTimeout = setTimeout(() => {
                    _filterCloseTimeout = null;
                    elements.filterDropdown?.classList.remove("closing");
                    state.filters.filterDropdownOpen = false;
                    renderAll();
                }, 150);
            }

            _haptic("light");
        });

        // Close filter dropdown when clicking outside
        document.addEventListener("click", (event) => {
            if (!state.filters.filterDropdownOpen) return;
            const dropdown = elements.filterDropdown;
            const btn = elements.filterBtn;
            const prefersReducedMotion = _prefersReducedMotion();
            if (dropdown && !dropdown.hidden && !dropdown.contains(event.target) && btn && !btn.contains(event.target)) {
                if (prefersReducedMotion) {
                    state.filters.filterDropdownOpen = false;
                    renderAll();
                } else {
                    dropdown.classList.add("closing");
                    if (_filterCloseTimeout) clearTimeout(_filterCloseTimeout);
                    _filterCloseTimeout = setTimeout(() => {
                        _filterCloseTimeout = null;
                        dropdown.classList.remove("closing");
                        state.filters.filterDropdownOpen = false;
                        renderAll();
                    }, 150);
                }
            }
        });

        // Prevent clicks inside dropdown from closing it
        elements.filterDropdown?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: category chips (event delegation) ────────
        elements.filterCategories?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-category]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            const rawValue = button.dataset.category;
            const newCategory = rawValue === "" ? null : Number(rawValue);

            // Update pending value and re-render to show selection
            state.filters.pendingCategory = newCategory;
            renderAll();

            _haptic("light");
        });

        // ── Filter dropdown: condition chips (event delegation) ───────
        elements.filterConditions?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-condition]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.filters.pendingCondition = button.dataset.condition;
            renderAll();

            _haptic("light");
        });

        // ── Filter dropdown: seller chips (event delegation) ──────────
        elements.filterSellers?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-seller]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.filters.pendingSellerType = button.dataset.seller;
            renderAll();

            _haptic("light");
        });

        // ── Filter dropdown: price range inputs ──────────────────────
        elements.filterMinPrice?.addEventListener("input", () => {
            state.filters.pendingMinPrice = _parseFilterPrice(elements.filterMinPrice.value);
        });

        elements.filterMinPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        elements.filterMaxPrice?.addEventListener("input", () => {
            state.filters.pendingMaxPrice = _parseFilterPrice(elements.filterMaxPrice.value);
        });

        elements.filterMaxPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: region select ─────────────────────────────
        elements.filterRegion?.addEventListener("change", () => {
            state.filters.pendingRegionName = elements.filterRegion.value;
        });

        elements.filterRegion?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: Apply button ─────────────────────────────
        elements.filterApplyBtn?.addEventListener("click", () => {
            // Check if category changed to trigger search
            const categoryChanged = state.filters.pendingCategory !== state.filters.category;
            const filtersChanged = categoryChanged
                || state.filters.pendingCondition !== state.filters.condition
                || state.filters.pendingSellerType !== state.filters.sellerType
                || state.filters.pendingMinPrice !== state.filters.minPrice
                || state.filters.pendingMaxPrice !== state.filters.maxPrice
                || state.filters.pendingRegionName !== state.filters.regionName;
            
            // Apply pending filter values
            state.filters.category = state.filters.pendingCategory;
            state.filters.condition = state.filters.pendingCondition;
            state.filters.sellerType = state.filters.pendingSellerType;
            state.filters.minPrice = state.filters.pendingMinPrice;
            state.filters.maxPrice = state.filters.pendingMaxPrice;
            state.filters.regionName = state.filters.pendingRegionName;
            state.filters.filterDropdownOpen = false;
            renderAll();

            // If category changed, trigger new search keeping the
            // freshly-applied filters (otherwise search() would wipe
            // the user's selection).
            if (categoryChanged && state.search.query.trim()) {
                void search(state.ui.activeView, { keepFilters: true });
            } else if (filtersChanged && state.search.query.trim()) {
                void loadListings(true);
            }

            _haptic("medium");
        });

        // ── Filter dropdown: Cancel button ────────────────────────────
        elements.filterCancelBtn?.addEventListener("click", () => {
            // Reset pending values to current applied values
            state.filters.pendingCategory = state.filters.category;
            state.filters.pendingCondition = state.filters.condition;
            state.filters.pendingSellerType = state.filters.sellerType;
            state.filters.pendingMinPrice = state.filters.minPrice;
            state.filters.pendingMaxPrice = state.filters.maxPrice;
            state.filters.pendingRegionName = state.filters.regionName;
            state.filters.filterDropdownOpen = false;
            renderAll();

            _haptic("light");
        });
    }

    function bindDiscountEvents() {
        // ── Discount buttons ─────────────────────────────────────────
        // Discount range presets (10-20%, 10-30%, …) and the manual
        // From/To inputs both used to live in their own "Выгодно"
        // view that fired loadDeals(). The view is gone — they now
        // sit inside the ads view and trigger loadListings(true) with
        // sort=cheap so the discount filter is applied to the same
        // listings list the user is already looking at.
        function _activateCheapSort() {
            state.search.sort = "cheap";
            renderSortButtons();
            if (elements.dealsControls) {
                elements.dealsControls.hidden = false;
            }
            setActiveView("ads");
            if (state.search.query.trim()) {
                void loadListings(true);
            }
        }

        for (const button of elements.discountButtons || []) {
            button.addEventListener("click", () => {
                const from = Number(button.dataset.discountFrom);
                const to = Number(button.dataset.discountTo);
                if (!Number.isFinite(from) || !Number.isFinite(to)) {
                    return;
                }
                state.filters.discountFromPercent = Math.min(from, to);
                state.filters.discountToPercent = Math.max(from, to);
                renderDiscountButtons();
                renderDealInputs();
                _activateCheapSort();
            });
        }

        // ── Discount apply button ────────────────────────────────────
        elements.dealApplyButton?.addEventListener("click", () => {
            const from = Math.abs(Number(elements.dealFromInput?.value || state.filters.discountFromPercent));
            const to = Math.abs(Number(elements.dealToInput?.value || state.filters.discountToPercent));
            state.filters.discountFromPercent = Math.min(from, to);
            state.filters.discountToPercent = Math.max(from, to);
            renderDiscountButtons();
            renderDealInputs();
            _activateCheapSort();
        });
    }

    function bindTrackerEvents() {
        // ── Tracker create button ────────────────────────────────────
        elements.trackQueryButton?.addEventListener("click", () => {
            const button = elements.trackQueryButton;
            button.disabled = true;
            button.classList.add('is-loading');
            const originalText = button.textContent;
            button.textContent = 'Создаю...';
            void (async () => {
                try {
                    await createTracker();
                } finally {
                    button.disabled = false;
                    button.classList.remove('is-loading');
                    button.textContent = originalText;
                }
            })().catch((err) => { logClientError("tracker create failed", err); });
        });

        // ── Clear events button (double-confirm) ─────────────────────
        let clearEventsConfirmed = false;
        elements.clearEventsButton?.addEventListener("click", () => {
            void (async () => {
                if (state.trackers.events.length === 0) {
                    showToast("Нет событий для удаления");
                    return;
                }

                if (!clearEventsConfirmed) {
                    clearEventsConfirmed = true;
                    elements.clearEventsButton.textContent = "Удалить все?";
                    _confirmTimers.events = setTimeout(() => {
                        clearEventsConfirmed = false;
                        if (elements.clearEventsButton) {
                            elements.clearEventsButton.textContent = "Очистить";
                        }
                    }, 3000);
                    showToast("Нажмите ещё раз для подтверждения");
                    return;
                }
                clearEventsConfirmed = false;
                if (elements.clearEventsButton) {
                    elements.clearEventsButton.textContent = "Очистить";
                }
                try {
                    await deleteJson("/api/v1/tracker-events");
                } catch (_) {
                    // ignore — clear locally anyway
                }
                state.trackers.events = [];
                showToast("События очищены");
                renderTrackerEvents();
                // Refresh the "N событий" hero badge so the count drops
                // to 0 immediately instead of waiting for a page reload.
                if (typeof renderTrackingHeroStats === "function") {
                    renderTrackingHeroStats();
                }
            })();
        });

        // ── Clear all leads button (double-confirm) ──────────────────
        let clearLeadsConfirmed = false;
        elements.clearAllLeadsButton?.addEventListener("click", () => {
            void (async () => {
                const activeLeads = state.leads.items.filter((l) => l.status !== "closed");
                if (activeLeads.length === 0) {
                    showToast("Нет активных сделок для удаления");
                    return;
                }
                if (!clearLeadsConfirmed) {
                    clearLeadsConfirmed = true;
                    elements.clearAllLeadsButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    _confirmTimers.leads = setTimeout(() => {
                        clearLeadsConfirmed = false;
                        if (elements.clearAllLeadsButton) {
                            elements.clearAllLeadsButton.textContent = "Очистить";
                        }
                    }, 3000);
                    return;
                }
                clearLeadsConfirmed = false;
                if (elements.clearAllLeadsButton) {
                    elements.clearAllLeadsButton.textContent = "Очистить";
                }
                await clearAllLeads();
            })();
        });

        // ── Clear all watchlist button (double-confirm) ──────────────
        let clearWatchlistConfirmed = false;
        elements.deleteAllWatchlistButton?.addEventListener("click", () => {
            void (async () => {
                if (state.watchlist.items.length === 0) {
                    showToast("Список уже пуст");
                    return;
                }
                if (!clearWatchlistConfirmed) {
                    clearWatchlistConfirmed = true;
                    elements.deleteAllWatchlistButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    _confirmTimers.watchlist = setTimeout(() => {
                        clearWatchlistConfirmed = false;
                        if (elements.deleteAllWatchlistButton) {
                            elements.deleteAllWatchlistButton.textContent = "Очистить";
                        }
                    }, 3000);
                    return;
                }
                clearWatchlistConfirmed = false;
                if (elements.deleteAllWatchlistButton) {
                    elements.deleteAllWatchlistButton.textContent = "Очистить";
                }
                await clearAllWatchlist();
            })();
        });

        // ── Tracker filter inputs ────────────────────────────────────
        elements.trackerMinDiscountInput?.addEventListener("input", () => {
            const nextValue = Number(elements.trackerMinDiscountInput.value);
            state.trackers.minDiscountPercent = Number.isFinite(nextValue) ? Math.abs(nextValue) : 10;
        });

        elements.trackerMaxPriceInput?.addEventListener("input", () => {
            const rawValue = elements.trackerMaxPriceInput.value.trim();
            if (!rawValue) {
                state.trackers.maxPriceByn = null;
                return;
            }
            const nextValue = Number(rawValue);
            state.trackers.maxPriceByn = Number.isFinite(nextValue) ? Math.abs(nextValue) : null;
        });

        elements.trackerSellerSelect?.addEventListener("change", () => {
            state.trackers.sellerType = elements.trackerSellerSelect.value;
        });

        elements.trackerConditionSelect?.addEventListener("change", () => {
            state.trackers.condition = elements.trackerConditionSelect.value;
        });

        elements.trackerRegionSelect?.addEventListener("change", () => {
            state.trackers.regionName = elements.trackerRegionSelect.value;
        });

        elements.trackerConfigInput?.addEventListener("input", () => {
            state.trackers.configKeyword = elements.trackerConfigInput.value.trim();
        });

        // ── Edit tracker modal ───────────────────────────────────────
        elements.closeEditModal?.addEventListener("click", () => {
            closeEditTrackerAction();
        });
        elements.cancelEditBtn?.addEventListener("click", () => {
            closeEditTrackerAction();
        });
        elements.saveTrackerBtn?.addEventListener("click", () => {
            const button = elements.saveTrackerBtn;
            button.disabled = true;
            button.classList.add('is-loading');
            const originalText = button.textContent;
            button.textContent = 'Сохраняю...';
            void (async () => {
                try {
                    await saveTracker();
                } finally {
                    button.disabled = false;
                    button.classList.remove('is-loading');
                    button.textContent = originalText;
                }
            })().catch((err) => { logClientError("save tracker failed", err); });
        });

        elements.editTrackerModal?.addEventListener("click", (event) => {
            if (event.target === elements.editTrackerModal) {
                closeEditTrackerAction();
            }
        });

        // ── Tracker event filter buttons ─────────────────────────────
        for (const button of elements.trackerEventFilterButtons || []) {
            button.addEventListener("click", () => {
                state.trackers.eventFilter = button.dataset.eventFilter || "all";
                renderTrackerEventFilters();
                renderTrackerEvents();
            });
        }
    }

    function bindModalEvents() {
        // ── Detail modal ─────────────────────────────────────────────
        function setDetailActionButton(button, text, disabled, label) {
            if (!button) return;
            button.textContent = text;
            button.disabled = Boolean(disabled);
            if (disabled) {
                button.setAttribute("aria-disabled", "true");
            } else {
                button.removeAttribute("aria-disabled");
            }
            if (label) button.setAttribute("aria-label", label);
        }

        elements.detailClose?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailOverlay?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailAddLeadButton?.addEventListener("click", () => {
            if (state.detail.data && !elements.detailAddLeadButton.disabled) {
                void Promise.resolve(
                    context.addLeadFromListing(state.detail.data, "detail_modal", state.detail.data.query || state.search.query),
                ).then((changed) => {
                    if (!changed) return;
                    const title = state.detail.data?.title || "товар";
                    setDetailActionButton(elements.detailAddLeadButton, "В покупках", true, `«${title}» уже в покупках`);
                    setDetailActionButton(elements.detailAddWatchlistButton, "В избранное", true, `«${title}» уже в покупках`);
                });
            }
        });

        elements.detailAddWatchlistButton?.addEventListener("click", () => {
            if (state.detail.data && !elements.detailAddWatchlistButton.disabled) {
                void Promise.resolve(
                    context.addWatchlistFromListing(state.detail.data, state.detail.data.query || state.search.query),
                ).then((changed) => {
                    if (!changed) return;
                    const title = state.detail.data?.title || "товар";
                    setDetailActionButton(elements.detailAddWatchlistButton, "В избранном", true, `«${title}» уже в избранном`);
                });
            }
        });

        elements.detailAiBtn?.addEventListener("click", () => {
            if (typeof canUseAiFeature === "function" && !canUseAiFeature("ai")) return;
            if (state.detail.data?.ad_id) {
                void loadAIAnalysis(state.detail.data.ad_id);
            }
        });

        // ── AI modal close ──
        elements.aiModalClose?.addEventListener("click", () => {
            closeAIModal();
        });
        elements.aiOverlay?.addEventListener("click", () => {
            closeAIModal();
        });

        // ── Escape key (modal close) ─────────────────────────────────
        // Topmost-modal priority. Listing-Assistant deliberately
        // NOT handled here — that module registers its OWN
        // ``keydown`` listener in api_listing_assistant.js which
        // calls its internal ``closeModal()`` (which goes through
        // ``closeModalAnimated`` and therefore runs the full close
        // path: ``is-closing`` animation, ``unlockBodyScroll``,
        // focus-trap cleanup, ``_restoreInertSiblings``).
        //
        // Wave 25.5: the earlier code here had its own LA branch
        // that did ``laModal.hidden = true;`` directly, bypassing
        // every part of that cleanup. Symptom: pressing Esc inside
        // the listing-assistant left ``body.modal-open`` (page
        // scroll-locked), the ``inert`` siblings still inert (no
        // clicks anywhere) and the focus trapped inside the now-
        // hidden modal — UI completely frozen until the user
        // refreshed.
        //
        // FE-04: previously this used an anonymous arrow which made
        // ``bindEvents()`` idempotency impossible — a re-init would
        // pile a second listener on top, and Escape would close two
        // modals at once. Now we hang the handler off a module-scoped
        // variable so we can dedupe.
        // FE-NEW-8: keep dedup state in module scope, not on document.
        if (_escapeHandler) {
            document.removeEventListener("keydown", _escapeHandler);
        }
        const escapeHandler = (event) => {
            if (event.key !== "Escape") return;
            if (!elements.aiModal?.hidden) {
                closeAIModal();
            } else if (!elements.expensesModal?.hidden) {
                closeExpensesModal();
            } else if (state.detail.data) {
                closeDetailModal();
            } else if (!elements.editTrackerModal?.hidden) {
                closeEditTrackerAction();
            }
        };
        _escapeHandler = escapeHandler;
        document.addEventListener("keydown", _escapeHandler);
    }

    function bindCarouselEvents() {
        // ── Image carousel — minimal opacity-crossfade swipe ──────────
        //
        // Earlier we tried a finger-follow live drag with rAF
        // coalescing and a compositor layer hint. Even with all the
        // tricks the live drag stuttered on Telegram WebView for some
        // users — the cheapest layout-only frame is still 16 ms of
        // composite work and many devices can't keep up at 60 Hz on
        // a full-width image.
        //
        // The simpler approach: don't animate during the gesture at
        // all. Detect the swipe on touchend, then run a 280 ms
        // crossfade with a tiny direction-hint translate (8 px). This
        // is one transition over a fixed time window — no per-frame
        // work, no layer-promotion fight with the page scroll. Looks
        // clean and performs identically on every device.
        //
        // Adjacent images get preloaded so src-swap is instant.
        let isAnimating = false;

        function _preloadAdjacent() {
            const images = state.detail.data?.images || [];
            const idx = state.detail.imageIndex || 0;
            _preloadCache.length = 0;
            for (const i of [idx - 1, idx + 1]) {
                if (i < 0 || i >= images.length) continue;
                const raw = images[i];
                if (typeof raw !== "string" || !raw) continue;
                const validated = (typeof context.safeImageUrl === "function")
                    ? context.safeImageUrl(raw)
                    : "";
                if (!validated) continue;
                const proxyUrl = (typeof context.optimizedImage === "function")
                    ? context.optimizedImage(validated, { width: 800, useProxy: true })
                    : validated;
                if (
                    proxyUrl &&
                    proxyUrl !== validated &&
                    typeof context.fetchProxyImageObjectUrl === "function"
                ) {
                    void context.fetchProxyImageObjectUrl(proxyUrl).catch(() => {});
                    continue;
                }
                const url = (typeof context.optimizedImage === "function")
                    ? context.optimizedImage(validated, { width: 800 })
                    : validated;
                const ghost = new Image();
                ghost.src = url;
                _preloadCache.push(ghost);
            }
        }

        async function navigateDetailImage(direction) {
            const images = state.detail.data?.images;
            if (!images || images.length <= 1) return;
            if (isAnimating) return;

            const total = images.length;
            const newIndex = direction > 0
                ? Math.min(total - 1, state.detail.imageIndex + 1)
                : Math.max(0, state.detail.imageIndex - 1);
            if (newIndex === state.detail.imageIndex) return;

            const img = elements.detailMainImage;
            if (!img) {
                state.detail.imageIndex = newIndex;
                context.renderDetailModal();
                return;
            }
            if (img._pinchController) {
                img._pinchController.reset(false);
            }

            isAnimating = true;

            // Phase 1 — fade old image out + nudge 8 px in swipe dir.
            const exitX = direction > 0 ? -8 : 8;
            img.style.transition = "opacity 140ms ease-out, transform 140ms ease-out";
            img.style.opacity = "0";
            img.style.transform = `translate3d(${exitX}px, 0, 0)`;

            await new Promise((resolve) => setTimeout(resolve, 140));

            // Phase 2 — swap src, pre-position 8 px on the opposite
            // side, then animate to (0, 0) with opacity 1. The
            // opposite-side enter sells the direction of travel; the
            // 8 px is small enough that the composite is trivially
            // cheap on any device.
            state.detail.imageIndex = newIndex;
            context.renderDetailModal();
            _preloadAdjacent();

            const enterX = direction > 0 ? 8 : -8;
            img.style.transition = "none";
            img.style.opacity = "0";
            img.style.transform = `translate3d(${enterX}px, 0, 0)`;
            // Force style flush so the next frame's transition starts
            // from the pre-positioned offset, not the previous one.
            void img.offsetWidth;

            requestAnimationFrame(() => {
                img.style.transition = "opacity 200ms ease-out, transform 220ms ease-out";
                img.style.opacity = "1";
                img.style.transform = "translate3d(0, 0, 0)";
                const release = () => {
                    img.style.transition = "";
                    img.style.opacity = "";
                    img.style.transform = "";
                    isAnimating = false;
                };
                setTimeout(release, 240);
            });
        }

        // ── Detect-on-release swipe ──────────────────────────────────
        // No live drag — we just record the start and check the delta
        // on touchend. Browser scrolls vertically without our
        // interference; we only fire navigateDetailImage when the
        // gesture was clearly horizontal AND past the threshold.
        // Scoped to .detail-media so swiping the thumbnail strip
        // (or any other modal content) never moves the hero.
        const SWIPE_THRESHOLD_PX = 50;
        let touchStartX = 0;
        let touchStartY = 0;
        let touchSkip = false;

        elements.detailMedia?.addEventListener("touchstart", (e) => {
            if (isAnimating) {
                touchSkip = true;
                return;
            }
            if (e.touches && e.touches.length > 1) {
                touchSkip = true;
                return;
            }
            if (elements.detailMainImage?.classList.contains("is-zoomed")) {
                touchSkip = true;
                return;
            }
            touchSkip = false;
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
        }, { passive: true });

        elements.detailMedia?.addEventListener("touchend", (e) => {
            if (touchSkip) {
                touchSkip = false;
                return;
            }
            const dx = e.changedTouches[0].clientX - touchStartX;
            const dy = e.changedTouches[0].clientY - touchStartY;
            if (Math.abs(dx) < SWIPE_THRESHOLD_PX) return;
            // Vertical swipe wins → don't page the photo.
            if (Math.abs(dy) > Math.abs(dx) * 0.8) return;
            void navigateDetailImage(dx < 0 ? 1 : -1);
        }, { passive: true });

        // Preload adjacent images when the modal first becomes active
        // so the first swipe doesn't show the network delay.
        const _preloadOnOpen = () => {
            if (state.detail.data) _preloadAdjacent();
        };
        elements.detailModal?.addEventListener("transitionend", _preloadOnOpen, { passive: true });

        // Keyboard arrows — only react when the detail modal is open.
        // FE-04: dedupe via the same sentinel-attribute trick used for
        // the global Escape handler — a second bindEvents() must not
        // pile up arrow handlers.
        if (document._kufarArrowHandler) {
            document.removeEventListener("keydown", document._kufarArrowHandler);
        }
        const arrowHandler = (event) => {
            if (!state.detail.data) return;
            if (event.key === "ArrowLeft") {
                void navigateDetailImage(-1);
            } else if (event.key === "ArrowRight") {
                void navigateDetailImage(1);
            }
        };
        document._kufarArrowHandler = arrowHandler;
        document.addEventListener("keydown", arrowHandler);
    }

    function bindExpenseEvents() {
        // ── Expenses modal ───────────────────────────────────────────
        elements.expensesClose?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.expensesOverlay?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.saveExpenseButton?.addEventListener("click", () => {
            const leadId = state.expenses.currentLeadId;
            if (!leadId) return;
            const type = elements.expenseTypeSelect?.value || "other";
            const rawAmount = elements.expenseAmountInput?.value?.trim();
            const displayAmount = rawAmount ? Number(rawAmount) : null;
            const notes = elements.expenseNotesInput?.value?.trim() || "";
            if (!displayAmount || displayAmount <= 0) {
                showToast("Введите корректную сумму");
                return;
            }
            
            const button = elements.saveExpenseButton;
            button.disabled = true;
            button.classList.add('is-loading');
            const originalText = button.textContent;
            button.textContent = 'Сохраняю...';
            void (async () => {
                try {
                    await createExpense(leadId, { expense_type: type, amount_byn: displayAmount, notes });
                    if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
                    if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
                } finally {
                    button.disabled = false;
                    button.classList.remove('is-loading');
                    button.textContent = originalText;
                }
            })().catch((err) => { logClientError("save expense failed", err); });
        });

        elements.cancelExpenseButton?.addEventListener("click", () => {
            closeExpensesModal();
        });
    }

    function bindVisibilityEvents() {
        // ── External link interception (Telegram Mini App mobile) ────
        document.addEventListener("click", (event) => {
            const link = event.target?.closest?.("a[target='_blank']");
            const rawHref = (link?.getAttribute("href") || "").trim();
            if (link && rawHref && !rawHref.startsWith("#") && !rawHref.startsWith("javascript:")) {
                event.preventDefault();
                event.stopPropagation();
                openExternalLink(rawHref);
            }
        });

        // ── Visibility change (pause/resume tracker refresh) ─────────
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) {
                stopTrackerRefresh();
            } else if (state.ui.activeView === "tracking") {
                startTrackerRefresh();
            }
        });
    }

    function bindEvents() {
        bindSearchEvents();
        bindRecentSearchEvents();
        bindViewTabEvents();
        bindProfileAdminEvents();
        bindFilterEvents();
        bindDiscountEvents();
        bindTrackerEvents();
        bindModalEvents();
        bindCarouselEvents();
        bindExpenseEvents();
        bindVisibilityEvents();

        // Lazy-load AI modules on first interaction with listing-assistant.
        // The module itself binds its own click handler inside
        // createApiListingAssistant; we intercept the first click to
        // load the scripts, then re-dispatch so the module's handler
        // fires on the next tick.
        const laOpenBtn = document.getElementById("listing-assistant-open-btn");
        laOpenBtn?.addEventListener("click", async function onFirstLaClick(e) {
            e.stopImmediatePropagation();
            if (typeof canUseAiFeature === "function" && !canUseAiFeature("assistant")) return;
            await context.ensureAiLoaded();
            laOpenBtn.removeEventListener("click", onFirstLaClick);
            laOpenBtn.click();
        });
    }

    return {
        bindEvents,
    };
}
