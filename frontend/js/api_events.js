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
        renderHistory,
        renderProfitDashboard,
        renderLeads,
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
        openEditTracker,
        closeEditTracker: closeEditTrackerAction,
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
        refreshTrackerEvents,
        clearTrackerEvents,
        startTrackerRefresh,
        stopTrackerRefresh,
    } = context;

    // ── Event binding ────────────────────────────────────────────────────
    const _searchDebounce = { timer: null };
    const _preloadCache = [];
    // FE-NEW-8: keep dedup state in module scope, not on document.
    let _escapeHandler = null;

    function _parseFilterPrice(value) {
        const trimmed = value == null ? "" : String(value).trim();
        if (trimmed === "") {
            return null;
        }
        const numeric = Number(trimmed);
        return Number.isFinite(numeric) ? numeric : null;
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
            // RV-FE-03: typing only edits the draft; the results on
            // screen keep their own activeQuery until search() runs.
            state.search.draft = elements.searchInput.value.trim();
            renderLoading();

            clearTimeout(_searchDebounce.timer);
            _searchDebounce.timer = setTimeout(() => {
                if (state.search.draft.length >= 2) {
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

        elements.searchClearButton?.addEventListener("click", () => {
            clearTimeout(_searchDebounce.timer);
            elements.searchInput.value = "";
            void search("overview");
            elements.searchInput.focus({ preventScroll: true });
            _haptic("light");
        });

        elements.searchButton?.addEventListener("click", () => {
            clearTimeout(_searchDebounce.timer);
            void search("overview");

            _haptic("medium");
        });

        // ── Error bar retry ────────────────────────────────────────────
        elements.errorRetry?.addEventListener("click", () => {
            state.ui.error = null;
            void search(state.ui.activeView || "overview", {
                forceRefresh: true,
                query: state.search.activeQuery || undefined,
            });
        });

    }

    function bindRecentSearchEvents() {
        // ── Recent searches (chips + clear button) ────────────────────
        elements.recentSection?.addEventListener("click", async (event) => {
            if (event.target.closest("#recent-clear-btn")) {
                if (!state.search.recentSearches.length || !await context.confirmAction({
                    title: "Очистить недавние запросы?",
                    message: "История поисковых запросов на этом устройстве будет удалена.",
                    confirmLabel: "Очистить историю",
                    destructive: true,
                })) return;
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
                state.search.draft = query;
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
                state.search.draft = query;
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
                    // FE-NEW-7: setActiveView already calls markDirty + renderAll
                    requestAnimationFrame(() => {
                        document
                            .getElementById("listing-assistant-section")
                            ?.scrollIntoView({ behavior: "smooth", block: "start" });
                    });
                }
            }
        });

        // ── Refinement chips (summary strip) ─────────────────────────
        elements.categoryPicker?.addEventListener("click", (event) => {
            const category = event.target.closest("[data-summary-category]");
            if (!category) return;
            _haptic("light");
            state.filters.category = Number(category.dataset.summaryCategory);
            state.filters.pendingCategory = state.filters.category;
            void search(state.ui.activeView, { keepFilters: true, query: state.search.activeQuery });
        });
        elements.summaryRefinements?.addEventListener("click", (event) => {
            const chip = event.target.closest("[data-refinement]");
            if (!chip) return;
            const token = chip.dataset.refinement || "";
            if (!token) return;
            // RV-FE-03: refinement chips belong to the results on screen,
            // so they extend the active query, not whatever is typed.
            const current = (state.search.activeQuery || "").trim();
            // Avoid duplicating the token if it's already in the query.
            const merged = _queryContainsRefinement(current, token)
                ? current
                : `${current} ${token}`.trim();
            if (merged === current) return;
            elements.searchInput.value = merged;
            state.search.draft = merged;
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
            if (state.search.activeQuery.trim()) {
                void search(state.ui.activeView, { query: state.search.activeQuery });
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
                setActiveView(view);
                // FE-NEW-7: setActiveView already calls markDirty + renderAll
                if (view === "tracking") {
                    void loadTrackers({ skipIfLoading: true });
                    if (state.trackers.pane === "events") void refreshTrackerEvents();
                    startTrackerRefresh();
                } else {
                    stopTrackerRefresh();
                }
                if (view === "deals") {
                    // Unified "Мои объявления" — load both leads and
                    // watchlist (formerly the Избранное view) in parallel
                    // so any filter chip ("Слежу" / "В работе" / …)
                    // has data to render against.
                    void loadLeads({ skipIfLoading: true });
                    void loadWatchlist({ skipIfLoading: true });
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
                if (state.search.activeQuery.trim()) {
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
            } else if (["export-data", "delete-account", "reload-consents"].includes(action) || action.startsWith("revoke-")) {
                const button = event.target.closest("button");
                if (button.disabled) return;
                button.disabled = true;
                const task = action === "export-data" ? context.exportAccountData()
                    : action === "delete-account" ? context.deleteAccount()
                    : action === "reload-consents" ? context.loadConsentSettings()
                    : context.revokeConsent(action.slice(7));
                void Promise.resolve(task).finally(() => { button.disabled = false; });
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
            const weeklyLimit = Number(card.querySelector("[data-admin-status-weekly]")?.value || 0);
            const trackerLimit = Number(card.querySelector("[data-admin-status-trackers]")?.value || 0);
            if (![aiLimit, weeklyLimit, trackerLimit].every((limit) => Number.isInteger(limit) && limit >= 0)
                || aiLimit > 10000 || trackerLimit > 10000 || weeklyLimit > 100000) {
                showToast("Лимиты должны быть целыми: 0–10000 за 5 часов и для трекеров, 0–100000 за неделю", "error");
                return;
            }
            if (Boolean(aiLimit) !== Boolean(weeklyLimit) || weeklyLimit < aiLimit) {
                showToast("Оба AI-лимита должны быть положительными или нулевыми. Недельный — не меньше 5-часового.", "error");
                return;
            }
            void updateStatusLimits(code, {
                ai_5h_limit: aiLimit,
                ai_weekly_limit: weeklyLimit,
                max_trackers: trackerLimit,
            });
        });
    }

    function bindFilterEvents() {
        const errorEl = document.getElementById("filter-error");
        function clearPriceError() {
            if (errorEl) errorEl.hidden = true;
            elements.filterMinPrice?.removeAttribute("aria-invalid");
            elements.filterMaxPrice?.removeAttribute("aria-invalid");
        }
        // ── Filter button (toggle dropdown) ───────────────────────────
        elements.filterBtn?.addEventListener("click", () => {
            if (!state.filters.filterDropdownOpen) {
                clearPriceError();
                state.filters.pendingCategory = state.filters.category;
                state.filters.pendingCondition = state.filters.condition;
                state.filters.pendingSellerType = state.filters.sellerType;
                state.filters.pendingMinPrice = state.filters.minPrice;
                state.filters.pendingMaxPrice = state.filters.maxPrice;
                state.filters.pendingRegionName = state.filters.regionName;
                state.filters.filterDropdownOpen = true;
                renderAll();
            } else {
                state.filters.filterDropdownOpen = false;
                renderAll();
            }

            _haptic("light");
        });

        // Close filter dropdown when clicking outside
        document.addEventListener("click", (event) => {
            if (!state.filters.filterDropdownOpen) return;
            const dropdown = elements.filterDropdown;
            const btn = elements.filterBtn;
            if (dropdown && !dropdown.hidden && !dropdown.contains(event.target) && btn && !btn.contains(event.target)) {
                state.filters.filterDropdownOpen = false;
                renderAll();
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
            clearPriceError();
            state.filters.pendingMinPrice = _parseFilterPrice(elements.filterMinPrice.value);
        });

        elements.filterMinPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        elements.filterMaxPrice?.addEventListener("input", () => {
            clearPriceError();
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
            clearPriceError();
            const minInput = elements.filterMinPrice;
            const maxInput = elements.filterMaxPrice;
            const invalid = [minInput, maxInput].find(input => input &&
                (input.validity?.badInput || (input.value !== "" && (!Number.isFinite(Number(input.value)) || Number(input.value) < 0))));
            const reversed = minInput && maxInput && minInput.value !== "" && maxInput.value !== "" && Number(minInput.value) > Number(maxInput.value);
            if (invalid || reversed) {
                const target = invalid || maxInput;
                errorEl.textContent = reversed ? "Цена «До» должна быть не меньше цены «От»." : "Введите цену от 0 BYN.";
                errorEl.hidden = false;
                target.setAttribute("aria-invalid", "true");
                target.focus();
                return;
            }
            const marketChanged = state.filters.pendingCategory !== state.filters.category
                || state.filters.pendingCondition !== state.filters.condition
                || state.filters.pendingSellerType !== state.filters.sellerType
                || state.filters.pendingRegionName !== state.filters.regionName;
            const filtersChanged = marketChanged
                || state.filters.pendingMinPrice !== state.filters.minPrice
                || state.filters.pendingMaxPrice !== state.filters.maxPrice;
            
            // Apply pending filter values
            state.filters.category = state.filters.pendingCategory;
            state.filters.condition = state.filters.pendingCondition;
            state.filters.sellerType = state.filters.pendingSellerType;
            state.filters.minPrice = state.filters.pendingMinPrice;
            state.filters.maxPrice = state.filters.pendingMaxPrice;
            state.filters.regionName = state.filters.pendingRegionName;
            state.filters.filterDropdownOpen = false;
            renderAll();

            if (marketChanged && state.search.activeQuery.trim()) {
                void search(state.ui.activeView, {
                    keepFilters: true,
                    query: state.search.activeQuery,
                });
            } else if (filtersChanged && state.search.activeQuery.trim()) {
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
        const errorEl = document.getElementById("discount-error");
        const clearError = () => {
            if (errorEl) errorEl.hidden = true;
            elements.dealFromInput?.removeAttribute("aria-invalid");
            elements.dealToInput?.removeAttribute("aria-invalid");
        };
        elements.dealFromInput?.addEventListener("input", clearError);
        elements.dealToInput?.addEventListener("input", clearError);
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
            if (state.search.activeQuery.trim()) {
                void loadListings(true);
            }
        }

        for (const button of elements.discountButtons || []) {
            button.addEventListener("click", () => {
                clearError();
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
            clearError();
            const from = Number(elements.dealFromInput.value);
            const to = Number(elements.dealToInput.value);
            const invalid = [elements.dealFromInput, elements.dealToInput].find(input =>
                input.value === "" || !input.validity.valid || !Number.isInteger(Number(input.value)) || Number(input.value) > 100);
            if (invalid || from > to) {
                const target = invalid || elements.dealToInput;
                errorEl.textContent = from > to ? "Скидка «До» должна быть не меньше скидки «От»." : "Введите целую скидку от 0 до 100 %.";
                errorEl.hidden = false;
                target.setAttribute("aria-invalid", "true");
                target.focus();
                return;
            }
            state.filters.discountFromPercent = from;
            state.filters.discountToPercent = to;
            renderDiscountButtons();
            renderDealInputs();
            _activateCheapSort();
        });
    }

    function selectTrackingPane(pane) {
        state.trackers.pane = pane;
        const tabs = elements.trackingTabs?.querySelectorAll("[data-tracking-pane]") || [];
        for (const tab of tabs) {
            const selected = tab.dataset.trackingPane === pane;
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
        }
        if (elements.trackerPanel) elements.trackerPanel.hidden = pane !== "trackers";
        if (elements.trackerEventsSection) elements.trackerEventsSection.hidden = pane !== "events";
    }
    context.selectTrackingPane = selectTrackingPane;

    function showTrackerEvents(trackerId = null) {
        state.trackers.eventFilterTrackerId = trackerId;
        state.trackers.eventFilter = "all";
        selectTrackingPane("events");
        elements.trackingTabs?.querySelector('[data-tracking-pane="events"]')?.focus();
        void refreshTrackerEvents();
    }

    function bindTrackerEvents() {
        elements.trackQueryButton?.addEventListener("click", () => { void openEditTracker(); });
        if (elements.trackingTabs) bindRovingTablist(elements.trackingTabs);
        for (const tab of elements.trackingTabs?.querySelectorAll("[data-tracking-pane]") || []) {
            tab.addEventListener("click", () => {
                const pane = tab.dataset.trackingPane;
                if (state.trackers.pane === pane) return;
                selectTrackingPane(pane);
                if (pane === "events") void refreshTrackerEvents();
                else void loadTrackers({ skipIfLoading: true });
            });
        }

        elements.trackerEventTrackerSelect?.addEventListener("change", (event) => {
            const value = event.currentTarget.value;
            state.trackers.eventFilterTrackerId = value ? Number(value) : null;
            void refreshTrackerEvents();
        });
        elements.trackerEventTypeSelect?.addEventListener("change", (event) => {
            state.trackers.eventFilter = event.currentTarget.value;
            void refreshTrackerEvents();
        });
        elements.refreshTrackerEventsButton?.addEventListener("click", () => {
            void refreshTrackerEvents();
        });
        elements.clearEventsButton?.addEventListener("click", () => {
            const button = elements.clearEventsButton;
            if (button.hidden || button.disabled) return;
            void clearTrackerEvents();
        });

        elements.clearAllLeadsButton?.addEventListener("click", () => {
            void clearAllLeads();
        });

        elements.deleteAllWatchlistButton?.addEventListener("click", () => {
            void clearAllWatchlist();
        });

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
                    context.addLeadFromListing(state.detail.data, "detail_modal", state.detail.data.query || state.search.activeQuery),
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
                    context.addWatchlistFromListing(state.detail.data, state.detail.data.query || state.search.activeQuery),
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
            } else if (elements.remindersModal && !elements.remindersModal.hidden) {
                context.closeRemindersModal();
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
        _bindCarouselEvents({
            state,
            elements,
            safeImageUrl: context.safeImageUrl,
            optimizedImage: context.optimizedImage,
            fetchProxyImageObjectUrl: context.fetchProxyImageObjectUrl,
            renderDetailModal: context.renderDetailModal,
        });
    }

    function bindExpenseEvents() {
        _bindExpenseEvents({
            state,
            elements,
            closeExpensesModal,
            showToast,
            createExpense,
            createReminder: context.createReminder,
            closeRemindersModal: context.closeRemindersModal,
            logClientError,
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
            try {
                await context.ensureAiLoaded();
            } catch (err) {
                // RV-FE-02: keep this interceptor so the next tap retries.
                logClientError("listing assistant load failed", err);
                showToast("Не удалось загрузить AI-помощника. Проверьте соединение и попробуйте ещё раз.", "error", 3600);
                return;
            }
            laOpenBtn.removeEventListener("click", onFirstLaClick);
            laOpenBtn.click();
        });
    }

    return {
        bindEvents,
        showTrackerEvents,
    };
}
