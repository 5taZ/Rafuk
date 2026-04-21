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
        renderComparison,
        renderHistory,
        renderTrackerEventFilters,
        renderTrackerEvents,
        renderWatchlist,
        renderMonitoringHeroStats,
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
        loadComparison,
        swapComparisonQueries,
        loadHistory,
        loadTrackers,
        loadLeads,
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
        openOpportunityQuery,
        openOpportunityDetail,
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeadsCSV,
        loadDetailRisks,
        loadAIAnalysis,
        searchByPhoto,
        startTrackerRefresh,
        stopTrackerRefresh,
        parseComparisonQueries,
    } = context;

    // ── Event binding ────────────────────────────────────────────────────
    function bindEvents() {
        let searchDebounceTimer = null;

        // ── Search input ─────────────────────────────────────────────
        elements.searchInput?.addEventListener("input", () => {
            state.query = elements.searchInput.value.trim();
            renderLoading();

            clearTimeout(searchDebounceTimer);
            searchDebounceTimer = setTimeout(() => {
                if (state.query.length >= 2) {
                    void search("overview");

                    if (window.Telegram?.WebApp?.HapticFeedback) {
                        Telegram.WebApp.HapticFeedback.impactOccurred("light");
                    }
                }
            }, 1200); // 1.2s debounce — gives users time to finish typing
        });

        elements.searchInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                clearTimeout(searchDebounceTimer);
                void search("overview");

                if (window.Telegram?.WebApp?.HapticFeedback) {
                    Telegram.WebApp.HapticFeedback.impactOccurred("medium");
                }
            }
        });

        elements.searchButton?.addEventListener("click", () => {
            clearTimeout(searchDebounceTimer);
            void search("overview");

            if (window.Telegram?.WebApp?.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.impactOccurred("medium");
            }
        });

        // ── Strict search toggle ─────────────────────────────────────
        elements.strictSearchToggle?.addEventListener("change", () => {
            state.strictSearch = Boolean(elements.strictSearchToggle.checked);
            renderStrictSearch();
            if (state.query.trim()) {
                void search(state.activeView);
            }
        });

        // ── Recent searches (chips + clear button) ────────────────────
        elements.recentSection?.addEventListener("click", (event) => {
            if (event.target.closest("#recent-clear-btn")) {
                // 1. Instant visual feedback — clear state and hide immediately
                state.recentSearches = [];

                // 2. Defer localStorage write to next tick (non-blocking)
                requestAnimationFrame(() => {
                    context.saveRecentSearches();
                });

                // 3. Don't call renderRecentSearches() — we handle it directly
                // to avoid double-render and ensure instant response
                if (elements.recentSection) elements.recentSection.hidden = true;
                if (elements.recentList) elements.recentList.innerHTML = "";

                // 4. Optional haptic feedback
                if (window.Telegram?.WebApp?.HapticFeedback) {
                    Telegram.WebApp.HapticFeedback.impactOccurred("light");
                }

                return;
            }
            const chip = event.target.closest("[data-recent-query]");
            if (chip) {
                const query = chip.dataset.recentQuery || "";
                elements.searchInput.value = query;
                state.query = query;
                renderLoading();
                clearTimeout(searchDebounceTimer);
                void search("overview");
            }
        });

        // ── View tabs ────────────────────────────────────────────────
        for (const button of elements.viewTabs || []) {
            button.addEventListener("click", () => {
                const view = button.dataset.view;
                if (!view) {
                    return;
                }
                setActiveView(view);
                renderAll();
                if (view === "tracking") {
                    void loadTrackers();
                    startTrackerRefresh();
                } else {
                    stopTrackerRefresh();
                }
                if (view === "monitoring") {
                    void loadWatchlist();
                }
                if (view === "deals") {
                    void loadLeads();
                }
                if (view === "cheap") {
                    if (state.query.trim()) {
                        void loadDeals();
                    }
                }
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
                if (!nextDays || nextDays === state.historyDays) {
                    return;
                }
                state.historyDays = nextDays;
                renderHistory();
                void loadHistory();
            });
        }

        // ── Comparison input ─────────────────────────────────────────
        elements.compareInput?.addEventListener("input", () => {
            state.comparisonQuery = elements.compareInput.value;
            renderComparison();
        });

        elements.compareInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                state.comparisonQuery = elements.compareInput.value;
                void loadComparison();
            }
        });

        elements.compareButton?.addEventListener("click", () => {
            state.comparisonQuery = elements.compareInput.value;
            const button = elements.compareButton;
            button.disabled = true;
            button.classList.add('is-loading');
            const originalText = button.textContent;
            button.textContent = 'Сравниваю...';
            void (async () => {
                try {
                    await loadComparison();
                } finally {
                    button.disabled = false;
                    button.classList.remove('is-loading');
                    button.textContent = originalText;
                }
            })();
        });

        elements.compareSwapButton?.addEventListener("click", () => {
            void swapComparisonQueries();
        });

        // ── Compare quick chips ──────────────────────────────────────
        for (const chip of elements.compareQuickChips || []) {
            chip.addEventListener("click", () => {
                const query = chip.dataset.compareQuery || "";
                const existing = parseComparisonQueries(state.comparisonQuery);
                const nextValues = Array.from(new Set([...existing, query])).slice(0, 2);
                state.comparisonQuery = nextValues.join(", ");
                elements.compareInput.value = state.comparisonQuery;
                setPanelOpen("comparison", true);
                renderComparison();
                void loadComparison();
            });
        }

        // ── Sort buttons ─────────────────────────────────────────────
        for (const button of elements.sortButtons || []) {
            button.addEventListener("click", () => {
                const sort = button.dataset.sort || "newest";
                if (sort === state.sort) {
                    return;
                }
                state.sort = sort;
                renderSortButtons();
                if (state.query.trim()) {
                    void loadListings(true);
                }
            });
        }

        // ── Filter button (toggle dropdown) ───────────────────────────
        elements.filterBtn?.addEventListener("click", () => {
            const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (!state.filterDropdownOpen) {
                // Opening — initialize pending values with current applied values
                state.pendingCategory = state.category;
                state.pendingCondition = state.condition;
                state.pendingSellerType = state.sellerType;
                state.pendingMinPrice = state.minPrice;
                state.pendingMaxPrice = state.maxPrice;
                state.pendingRegionName = state.regionName;
                state.filterDropdownOpen = true;
                renderAll();
            } else if (prefersReducedMotion) {
                // Closing without animation for users who prefer reduced motion
                state.filterDropdownOpen = false;
                renderAll();
            } else {
                // Closing — add closing class for animation, then hide
                elements.filterDropdown?.classList.add("closing");
                setTimeout(() => {
                    elements.filterDropdown?.classList.remove("closing");
                    state.filterDropdownOpen = false;
                    renderAll();
                }, 150);
            }

            if (window.Telegram?.WebApp?.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.impactOccurred("light");
            }
        });

        // Close filter dropdown when clicking outside
        document.addEventListener("click", (event) => {
            if (!state.filterDropdownOpen) return;
            const dropdown = elements.filterDropdown;
            const btn = elements.filterBtn;
            const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (dropdown && !dropdown.hidden && !dropdown.contains(event.target) && btn && !btn.contains(event.target)) {
                if (prefersReducedMotion) {
                    state.filterDropdownOpen = false;
                    renderAll();
                } else {
                    // Add closing class for animation
                    dropdown.classList.add("closing");
                    setTimeout(() => {
                        dropdown.classList.remove("closing");
                        state.filterDropdownOpen = false;
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
            state.pendingCategory = newCategory;
            renderAll();

            if (window.Telegram?.WebApp?.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.impactOccurred("light");
            }
        });

        // ── Filter dropdown: condition chips (event delegation) ───────
        elements.filterConditions?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-condition]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.pendingCondition = button.dataset.condition;
            renderAll();

            if (window.Telegram?.WebApp?.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.impactOccurred("light");
            }
        });

        // ── Filter dropdown: seller chips (event delegation) ──────────
        elements.filterSellers?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-seller]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.pendingSellerType = button.dataset.seller;
            renderAll();

            if (window.Telegram?.WebApp?.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.impactOccurred("light");
            }
        });

        // ── Filter dropdown: price range inputs ──────────────────────
        elements.filterMinPrice?.addEventListener("input", () => {
            const value = elements.filterMinPrice.value.trim();
            state.pendingMinPrice = value === "" ? null : Math.max(0, Number(value));
        });

        elements.filterMinPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        elements.filterMaxPrice?.addEventListener("input", () => {
            const value = elements.filterMaxPrice.value.trim();
            state.pendingMaxPrice = value === "" ? null : Math.max(0, Number(value));
        });

        elements.filterMaxPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: region select ─────────────────────────────
        elements.filterRegion?.addEventListener("change", () => {
            state.pendingRegionName = elements.filterRegion.value;
        });

        elements.filterRegion?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: Apply button ─────────────────────────────
        elements.filterApplyBtn?.addEventListener("click", () => {
            // Check if category changed to trigger search
            const categoryChanged = state.pendingCategory !== state.category;
            
            // Apply pending filter values
            state.category = state.pendingCategory;
            state.condition = state.pendingCondition;
            state.sellerType = state.pendingSellerType;
            state.minPrice = state.pendingMinPrice;
            state.maxPrice = state.pendingMaxPrice;
            state.regionName = state.pendingRegionName;
            state.filterDropdownOpen = false;
            renderAll();

            // If category changed, trigger new search (server-side filter)
            if (categoryChanged && state.query.trim()) {
                void search(state.activeView);
            }

            if (window.Telegram?.WebApp?.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.impactOccurred("medium");
            }
        });

        // ── Filter dropdown: Cancel button ────────────────────────────
        elements.filterCancelBtn?.addEventListener("click", () => {
            // Reset pending values to current applied values
            state.pendingCategory = state.category;
            state.pendingCondition = state.condition;
            state.pendingSellerType = state.sellerType;
            state.pendingMinPrice = state.minPrice;
            state.pendingMaxPrice = state.maxPrice;
            state.pendingRegionName = state.regionName;
            state.filterDropdownOpen = false;
            renderAll();

            if (window.Telegram?.WebApp?.HapticFeedback) {
                Telegram.WebApp.HapticFeedback.impactOccurred("light");
            }
        });

        // ── Discount buttons ─────────────────────────────────────────
        for (const button of elements.discountButtons || []) {
            button.addEventListener("click", () => {
                const from = Number(button.dataset.discountFrom);
                const to = Number(button.dataset.discountTo);
                if (!Number.isFinite(from) || !Number.isFinite(to)) {
                    return;
                }
                state.discountFromPercent = Math.min(from, to);
                state.discountToPercent = Math.max(from, to);
                renderDiscountButtons();
                renderDealInputs();
                setActiveView("cheap");
                if (state.query.trim()) {
                    void loadDeals();
                }
            });
        }

        // ── Discount apply button ────────────────────────────────────
        elements.dealApplyButton?.addEventListener("click", () => {
            const from = Math.abs(Number(elements.dealFromInput?.value || state.discountFromPercent));
            const to = Math.abs(Number(elements.dealToInput?.value || state.discountToPercent));
            state.discountFromPercent = Math.min(from, to);
            state.discountToPercent = Math.max(from, to);
            renderDiscountButtons();
            renderDealInputs();
            setActiveView("cheap");
            if (state.query.trim()) {
                void loadDeals();
            }
        });

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
            })();
        });

        // ── Clear events button (double-confirm) ─────────────────────
        let clearEventsConfirmed = false;
        elements.clearEventsButton?.addEventListener("click", () => {
            void (async () => {
                if (state.trackerEvents.length === 0) {
                    showToast("Нет событий для удаления");
                    return;
                }

                if (!clearEventsConfirmed) {
                    clearEventsConfirmed = true;
                    elements.clearEventsButton.textContent = "Удалить все?";
                    setTimeout(() => {
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
                state.trackerEvents = [];
                showToast("События очищены");
                renderTrackerEvents();
            })();
        });

        // ── Clear all leads button (double-confirm) ──────────────────
        let clearLeadsConfirmed = false;
        elements.clearAllLeadsButton?.addEventListener("click", () => {
            void (async () => {
                const activeLeads = state.leads.filter((l) => l.status !== "closed");
                if (activeLeads.length === 0) {
                    showToast("Нет активных сделок для удаления");
                    return;
                }
                if (!clearLeadsConfirmed) {
                    clearLeadsConfirmed = true;
                    elements.clearAllLeadsButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    setTimeout(() => {
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
                if (state.watchlist.length === 0) {
                    showToast("Список уже пуст");
                    return;
                }
                if (!clearWatchlistConfirmed) {
                    clearWatchlistConfirmed = true;
                    elements.deleteAllWatchlistButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    setTimeout(() => {
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
            state.trackerMinDiscountPercent = Number.isFinite(nextValue) ? Math.abs(nextValue) : 10;
        });

        elements.trackerMaxPriceInput?.addEventListener("input", () => {
            const rawValue = elements.trackerMaxPriceInput.value.trim();
            if (!rawValue) {
                state.trackerMaxPriceByn = null;
                return;
            }
            const nextValue = Number(rawValue);
            state.trackerMaxPriceByn = Number.isFinite(nextValue) ? Math.abs(nextValue) : null;
        });

        elements.trackerSellerSelect?.addEventListener("change", () => {
            state.trackerSellerType = elements.trackerSellerSelect.value;
        });

        elements.trackerConditionSelect?.addEventListener("change", () => {
            state.trackerCondition = elements.trackerConditionSelect.value;
        });

        elements.trackerRegionSelect?.addEventListener("change", () => {
            state.trackerRegionName = elements.trackerRegionSelect.value;
        });

        elements.trackerConfigInput?.addEventListener("input", () => {
            state.trackerConfigKeyword = elements.trackerConfigInput.value.trim();
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
            })();
        });

        elements.editTrackerModal?.addEventListener("click", (event) => {
            if (event.target === elements.editTrackerModal) {
                closeEditTrackerAction();
            }
        });

        // ── Tracker event filter buttons ─────────────────────────────
        for (const button of elements.trackerEventFilterButtons || []) {
            button.addEventListener("click", () => {
                state.trackerEventFilter = button.dataset.eventFilter || "all";
                renderTrackerEventFilters();
                renderTrackerEvents();
            });
        }

        // ── Tracker event tracker dropdown ──────────────────────────
        if (elements.trackerEventTrackerSelect) {
            elements.trackerEventTrackerSelect.addEventListener("change", () => {
                const val = elements.trackerEventTrackerSelect.value;
                state.trackerEventFilterTrackerId = val ? Number(val) : null;
                renderTrackerEvents();
            });
        }

        // ── Watchlist filter buttons ─────────────────────────────────
        for (const button of elements.watchlistFilterButtons || []) {
            button.addEventListener("click", () => {
                state.watchlistFilter = button.dataset.watchFilter || "all";
                renderWatchlist();
            });
        }

        // ── Detail modal ─────────────────────────────────────────────
        elements.detailClose?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailOverlay?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailAddLeadButton?.addEventListener("click", () => {
            if (state.detail) {
                void context.addLeadFromListing(state.detail, "detail_modal", state.detail.query || state.query);
            }
        });

        elements.detailAddWatchlistButton?.addEventListener("click", () => {
            if (state.detail) {
                void context.addWatchlistFromListing(state.detail, state.detail.query || state.query);
            }
        });

        elements.detailAiBtn?.addEventListener("click", () => {
            if (state.detail?.ad_id) {
                void loadAIAnalysis(state.detail.ad_id);
            }
        });

        // ── Photo search ────────────────────────────────────────────
        elements.photoSearchBtn?.addEventListener("click", () => {
            elements.photoFileInput?.click();
        });

        elements.photoFileInput?.addEventListener("change", () => {
            const file = elements.photoFileInput?.files?.[0];
            if (file) {
                void searchByPhoto(file);
                elements.photoFileInput.value = "";
            }
        });

        elements.photoResultsClose?.addEventListener("click", () => {
            if (elements.photoResultsSection) elements.photoResultsSection.hidden = true;
            const listingsSection = document.getElementById("listings-section");
            if (listingsSection && state.listings?.length) listingsSection.hidden = false;
        });

        // ── Escape key (modal close) ─────────────────────────────────
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                if (!state.detail && !elements.editTrackerModal?.hidden) {
                    closeEditTrackerAction();
                } else if (!elements.expensesModal?.hidden) {
                    closeExpensesModal();
                } else if (state.detail) {
                    closeDetailModal();
                }
            }
        });

        // ── Swipe support for detail modal photos ────────────────────
        let touchStartX = 0;
        let touchEndX = 0;
        let isSwiping = false;
        elements.detailModal?.addEventListener("touchstart", (e) => {
            touchStartX = e.changedTouches[0].screenX;
            isSwiping = false;
        }, { passive: true });
        elements.detailModal?.addEventListener("touchend", (e) => {
            if (isSwiping) return;
            touchEndX = e.changedTouches[0].screenX;
            const swipeDistance = touchStartX - touchEndX;
            if (Math.abs(swipeDistance) > 50 && state.detail?.images?.length > 1) {
                isSwiping = true;
                const mediaEl = elements.detailModal?.querySelector(".detail-media");
                if (mediaEl) {
                    mediaEl.classList.add("swipe-anim");
                    setTimeout(() => {
                        if (swipeDistance > 0) {
                            state.detailImageIndex = Math.min(state.detail.images.length - 1, state.detailImageIndex + 1);
                        } else {
                            state.detailImageIndex = Math.max(0, state.detailImageIndex - 1);
                        }
                        context.renderDetailModal();
                        requestAnimationFrame(() => {
                            setTimeout(() => {
                                mediaEl.classList.remove("swipe-anim");
                                isSwiping = false;
                            }, 50);
                        });
                    }, 150);
                }
            }
        }, { passive: true });

        // ── Keyboard arrow navigation for photos ─────────────────────
        document.addEventListener("keydown", (event) => {
            if (!state.detail || !(state.detail?.images?.length > 1)) return;
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                const mediaEl = elements.detailModal?.querySelector(".detail-media");
                if (mediaEl) {
                    mediaEl.classList.add("swipe-anim");
                    setTimeout(() => {
                        if (event.key === "ArrowLeft") {
                            state.detailImageIndex = Math.max(0, state.detailImageIndex - 1);
                        } else {
                            state.detailImageIndex = Math.min(state.detail.images.length - 1, state.detailImageIndex + 1);
                        }
                        context.renderDetailModal();
                        requestAnimationFrame(() => {
                            setTimeout(() => mediaEl.classList.remove("swipe-anim"), 50);
                        });
                    }, 150);
                }
            }
        });

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

        // ── Expenses modal ───────────────────────────────────────────
        elements.expensesClose?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.expensesOverlay?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.saveExpenseButton?.addEventListener("click", () => {
            const leadId = state.currentExpenseLeadId;
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
            })();
        });

        elements.cancelExpenseButton?.addEventListener("click", () => {
            closeExpensesModal();
        });

        // ── Visibility change (pause/resume tracker refresh) ─────────
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) {
                stopTrackerRefresh();
            } else if (state.activeView === "tracking") {
                startTrackerRefresh();
            }
        });
    }

    return {
        bindEvents,
    };
}
