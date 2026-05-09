/**
 * api_events.js — All DOM event binding for the application.
 *
 * This module wires up every interactive element: search inputs, buttons,
 * chips, tabs, toggles, modals, filters, swipes, keyboard shortcuts, and
 * cross-app lifecycle triggers. It delegates all business logic to the action
 * functions passed through context.
 */

function createApiEvents(context) {
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
        buildCommonQuery,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        // Action functions delegated from other modules
        search,
        loadListings,
        loadDeals,
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
        loadAIAnalysis,
        closeAIModal,
        startTrackerRefresh,
        stopTrackerRefresh,
    } = context;

    // ── Event binding ────────────────────────────────────────────────────
    const _searchDebounce = { timer: null };
    const _confirmTimers = {};
    const _preloadCache = [];

    function bindSearchEvents() {
        // ── Search input ─────────────────────────────────────────────
        elements.searchInput?.addEventListener("input", () => {
            state.search.query = elements.searchInput.value.trim();
            renderLoading();

            clearTimeout(_searchDebounce.timer);
            _searchDebounce.timer = setTimeout(() => {
                if (state.search.query.length >= 2) {
                    void search("overview");

                    try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
                }
            }, 1200); // 1.2s debounce — gives users time to finish typing
        });

        elements.searchInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                clearTimeout(_searchDebounce.timer);
                void search("overview");

                try { _tgHaptic()?.impactOccurred?.("medium"); } catch (_) {}
            }
        });

        elements.searchButton?.addEventListener("click", () => {
            clearTimeout(_searchDebounce.timer);
            void search("overview");

            try { _tgHaptic()?.impactOccurred?.("medium"); } catch (_) {}
        });

        // ── Error bar retry ────────────────────────────────────────────
        elements.errorRetry?.addEventListener("click", () => {
            state.ui.error = null;
            void search(state.ui.activeView || "overview");
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
                try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}

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
            const lowerCurrent = current.toLowerCase();
            const lowerToken = token.toLowerCase();
            // Avoid duplicating the token if it's already in the query.
            const merged = lowerCurrent.includes(lowerToken)
                ? current
                : `${current} ${token}`.trim();
            if (merged === current) return;
            elements.searchInput.value = merged;
            state.search.query = merged;
            renderLoading();
            clearTimeout(_searchDebounce.timer);
            try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
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
        if (tablist) {
            tablist.addEventListener("keydown", (event) => {
                const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
                if (!tabs.length) return;
                const idx = tabs.indexOf(document.activeElement);
                if (idx === -1) return;
                let next = -1;
                if (event.key === "ArrowRight") next = (idx + 1) % tabs.length;
                else if (event.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = tabs.length - 1;
                if (next === -1) return;
                event.preventDefault();
                tabs[next].focus();
                tabs[next].click();
            });
        }
        for (const button of elements.viewTabs || []) {
            button.addEventListener("click", () => {
                const view = button.dataset.view;
                if (!view) {
                    return;
                }
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
        for (const button of elements.itemsFilterButtons || []) {
            button.addEventListener("click", () => {
                const filter = button.dataset.itemsFilter;
                if (!filter) return;
                state.leads.itemsFilter = filter;
                renderLeads();

                try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
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

        // ── Analytics period chips (30 дн / 90 дн / Год) ────────────
        for (const button of elements.analyticsPeriodButtons || []) {
            button.addEventListener("click", () => {
                const nextDays = Number(button.dataset.analyticsPeriod);
                if (!nextDays || nextDays === state.analytics.periodDays) {
                    return;
                }
                state.analytics.periodDays = nextDays;
                renderProfitDashboard();
                void loadAnalytics();
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

    function bindFilterEvents() {
        // ── Filter button (toggle dropdown) ───────────────────────────
        elements.filterBtn?.addEventListener("click", () => {
            const prefersReducedMotion = _prefersReducedMotion();
            if (!state.filters.filterDropdownOpen) {
                // Opening — initialize pending values with current applied values
                state.filters.pendingCategory = state.filters.category;
                state.filters.pendingCondition = state.filters.condition;
                state.filters.pendingSellerType = state.filters.sellerType;
                state.filters.pendingMinPrice = state.filters.minPrice;
                state.filters.pendingMaxPrice = state.filters.maxPrice;
                state.filters.pendingRegionName = state.filters.regionName;
                state.filters.filterDropdownOpen = true;
                renderAll();
            } else if (prefersReducedMotion) {
                // Closing without animation for users who prefer reduced motion
                state.filters.filterDropdownOpen = false;
                renderAll();
            } else {
                // Closing — add closing class for animation, then hide
                elements.filterDropdown?.classList.add("closing");
                setTimeout(() => {
                    elements.filterDropdown?.classList.remove("closing");
                    state.filters.filterDropdownOpen = false;
                    renderAll();
                }, 150);
            }

            try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
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
                    // Add closing class for animation
                    dropdown.classList.add("closing");
                    setTimeout(() => {
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

            try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
        });

        // ── Filter dropdown: condition chips (event delegation) ───────
        elements.filterConditions?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-condition]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.filters.pendingCondition = button.dataset.condition;
            renderAll();

            try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
        });

        // ── Filter dropdown: seller chips (event delegation) ──────────
        elements.filterSellers?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-seller]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.filters.pendingSellerType = button.dataset.seller;
            renderAll();

            try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
        });

        // ── Filter dropdown: price range inputs ──────────────────────
        elements.filterMinPrice?.addEventListener("input", () => {
            const value = elements.filterMinPrice.value.trim();
            state.filters.pendingMinPrice = value === "" ? null : Math.max(0, Number(value));
        });

        elements.filterMinPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        elements.filterMaxPrice?.addEventListener("input", () => {
            const value = elements.filterMaxPrice.value.trim();
            state.filters.pendingMaxPrice = value === "" ? null : Math.max(0, Number(value));
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
            }

            try { _tgHaptic()?.impactOccurred?.("medium"); } catch (_) {}
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

            try { _tgHaptic()?.impactOccurred?.("light"); } catch (_) {}
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
            })().catch((err) => { console.error("tracker create failed", err); });
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
            })().catch((err) => { console.error("save tracker failed", err); });
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
        elements.detailClose?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailOverlay?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailAddLeadButton?.addEventListener("click", () => {
            if (state.detail.data) {
                void context.addLeadFromListing(state.detail.data, "detail_modal", state.detail.data.query || state.search.query);
            }
        });

        elements.detailAddWatchlistButton?.addEventListener("click", () => {
            if (state.detail.data) {
                void context.addWatchlistFromListing(state.detail.data, state.detail.data.query || state.search.query);
            }
        });

        elements.detailAiBtn?.addEventListener("click", () => {
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
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                // Close the topmost modal first — LA > AI > expenses > detail > edit tracker.
                const laModal = document.getElementById("la-modal");
                if (laModal && !laModal.hidden) {
                    laModal.hidden = true;
                } else if (!elements.aiModal?.hidden) {
                    closeAIModal();
                } else if (!elements.expensesModal?.hidden) {
                    closeExpensesModal();
                } else if (state.detail.data) {
                    closeDetailModal();
                } else if (!elements.editTrackerModal?.hidden) {
                    closeEditTrackerAction();
                }
            }
        });
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
                const validated = (typeof context.safeUrl === "function")
                    ? context.safeUrl(raw)
                    : raw;
                if (!validated) continue;
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
        document.addEventListener("keydown", (event) => {
            if (!state.detail.data) return;
            if (event.key === "ArrowLeft") {
                void navigateDetailImage(-1);
            } else if (event.key === "ArrowRight") {
                void navigateDetailImage(1);
            }
        });
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
            })().catch((err) => { console.error("save expense failed", err); });
        });

        elements.cancelExpenseButton?.addEventListener("click", () => {
            closeExpensesModal();
        });
    }

    function bindVisibilityEvents() {
        // ── External link interception (Telegram Mini App mobile) ────
        document.addEventListener("click", (event) => {
            const link = event.target.closest("a[target='_blank']");
            if (link && link.href && !link.href.startsWith("#") && !link.href.startsWith("javascript:")) {
                event.preventDefault();
                event.stopPropagation();

                if (window.Telegram?.WebApp?.openLink) {
                    window.Telegram.WebApp.openLink(link.href);
                } else {
                    window.open(link.href, "_blank", "noopener,noreferrer");
                }
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
        bindFilterEvents();
        bindDiscountEvents();
        bindTrackerEvents();
        bindModalEvents();
        bindCarouselEvents();
        bindExpenseEvents();
        bindVisibilityEvents();
    }

    return {
        bindEvents,
    };
}
