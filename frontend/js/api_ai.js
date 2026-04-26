/**
 * api_ai.js — AI listing analysis with dedicated modal + PDF export.
 */
function createApiAi(context) {
    const { state, elements, postJson, getJson, safeUrl, formatPrice } = context;

    let _aiLoading = false;
    let _aiProgress = 0;
    let _lastAiData = null;
    let _progressFrame = null;

    const LOADING_STEPS = [
        "Загружаю данные объявления...",
        "Анализирую фотографии...",
        "Сравниваю с рынком...",
        "Подбираю альтернативы...",
        "Формирую рекомендации...",
        "Осталось немного...",
    ];
    const AI_PROGRESS_EXPECTED_MS = 26000;
    const AI_PROGRESS_SOFT_CAP = 96;
    const STAGE_LABELS = {
        queued: "Ставлю задачу в очередь...",
        loading_market_data: "Загружаю данные объявления...",
        search_ready: "Подбираю рынок и аналоги...",
        photo_precheck: "Быстро оцениваю фото...",
        calling_ai: "AI анализирует цену и состояние...",
        building_response: "Собираю итоговый анализ...",
        done: "Анализ завершён!",
        timeout: "AI анализ занял слишком долго...",
        error: "Не удалось завершить AI анализ...",
        target_missing: "Объявление не найдено...",
    };

    function _updateProgressDisplay(pct) {
        _aiProgress = Math.max(0, Math.min(100, pct));
        const barEl = elements.aiProgressBar;
        const pctEl = elements.aiProgressPct;
        if (barEl) barEl.style.width = _aiProgress + "%";
        if (pctEl) pctEl.textContent = Math.round(_aiProgress) + "%";
    }

    function _updateProgressDisplayInstant(pct) {
        const barEl = elements.aiProgressBar;
        if (!barEl) {
            _updateProgressDisplay(pct);
            return;
        }
        const previousTransition = barEl.style.transition;
        barEl.style.transition = "none";
        _updateProgressDisplay(pct);
        barEl.offsetHeight;
        barEl.style.transition = previousTransition;
    }

    function _setStageLabel(stage) {
        const textEl = elements.aiLoaderText;
        if (!textEl || !stage || !STAGE_LABELS[stage]) return;
        textEl.textContent = STAGE_LABELS[stage];
    }

    let _progressStartedAt = 0;

    function _prefersReducedMotion() {
        return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    }

    function _cancelProgressFrame() {
        if (_progressFrame) {
            cancelAnimationFrame(_progressFrame);
            _progressFrame = null;
        }
    }

    function _startLoadingAnimation() {
        let step = 0;
        _aiProgress = 0;
        _progressStartedAt = performance.now();
        const textEl = elements.aiLoaderText;

        if (_prefersReducedMotion()) {
            if (textEl) textEl.textContent = LOADING_STEPS[0];
            _updateProgressDisplay(12);
            return;
        }

        function tick(now) {
            const elapsed = Math.max(0, now - _progressStartedAt);
            const normalized = Math.min(elapsed / AI_PROGRESS_EXPECTED_MS, 1);
            const target = 3 + normalized * (AI_PROGRESS_SOFT_CAP - 3);
            _updateProgressDisplay(Math.max(_aiProgress, target));

            const nextStep = Math.min(
                LOADING_STEPS.length - 1,
                Math.floor(normalized * LOADING_STEPS.length),
            );
            if (nextStep !== step) {
                step = nextStep;
                if (textEl) textEl.textContent = LOADING_STEPS[step];
            }

            _progressFrame = requestAnimationFrame(tick);
        }

        if (textEl) textEl.textContent = LOADING_STEPS[0];
        _updateProgressDisplay(3);
        _cancelProgressFrame();
        _progressFrame = requestAnimationFrame(tick);
    }

    function _stopLoadingAnimation(success = true) {
        _cancelProgressFrame();
        if (success) {
            // Smooth transition from current progress to 100%
            const startPct = _aiProgress;
            const targetPct = 100;
            if (_prefersReducedMotion() || startPct >= targetPct - 1) {
                _updateProgressDisplay(targetPct);
                const barEl = elements.aiProgressBar;
                if (barEl) barEl.classList.add("ai-progress-bar--done");
            } else {
                const duration = Math.min(1800, Math.max(900, (targetPct - startPct) * 24));
                const startTime = performance.now();
                function animateStep(now) {
                    const elapsed = now - startTime;
                    const t = Math.min(elapsed / duration, 1);
                    // Ease-out cubic
                    const eased = 1 - Math.pow(1 - t, 3);
                    const pct = startPct + (targetPct - startPct) * eased;
                    _updateProgressDisplay(pct);
                    if (t < 1) {
                        _progressFrame = requestAnimationFrame(animateStep);
                    } else {
                        _progressFrame = null;
                        const barEl = elements.aiProgressBar;
                        if (barEl) barEl.classList.add("ai-progress-bar--done");
                    }
                }
                _progressFrame = requestAnimationFrame(animateStep);
            }
        }
    }

    function _showCompletionThen(callback) {
        _stopLoadingAnimation();

        const loadingEl = elements.aiModalLoading;
        if (!loadingEl) { callback(); return; }

        const wrap = loadingEl.querySelector(".ai-loader-wrap") || loadingEl.querySelector(".ai-loader");
        const ring = wrap?.querySelector(".ai-loader-ring") || loadingEl.querySelector(".ai-loader-ring");
        const icon = wrap?.querySelector(".ai-loader-icon") || loadingEl.querySelector(".ai-loader-icon");
        if (ring) ring.classList.add("ai-loader-ring--done");
        if (icon) {
            icon.textContent = "✓";
            icon.classList.add("ai-loader-icon--done");
        }
        if (elements.aiLoaderText) elements.aiLoaderText.textContent = "Анализ завершён!";

        setTimeout(() => {
            try {
                callback();
            } catch (e) {
                console.error("AI render error:", e);
                if (elements.aiModalResult) {
                    elements.aiModalResult.hidden = false;
                    elements.aiModalResult.replaceChildren(
                        domEl("div", { className: "ai-error", text: "Ошибка отображения результата" })
                    );
                }
            }
        }, _prefersReducedMotion() ? 150 : 950);
    }

    function openAIModal(subtitle, options = {}) {
        const { startLoading = true } = options;
        if (elements.aiModalSubtitle && subtitle) {
            elements.aiModalSubtitle.textContent = subtitle;
        }

        // Hide PDF button until results
        const pdfBtn = document.getElementById("ai-export-pdf");
        if (pdfBtn) pdfBtn.hidden = true;

        // Show time estimate notice
        const bodyEl = elements.aiModal?.querySelector(".ai-modal-body");
        if (bodyEl) {
            let notice = bodyEl.querySelector(".ai-time-notice");
            if (!notice) {
                notice = document.createElement("p");
                notice.className = "ai-time-notice";
                const loadingEl = elements.aiModalLoading;
                if (loadingEl) {
                    loadingEl.parentNode.insertBefore(notice, loadingEl.nextSibling);
                }
            }
            notice.textContent = "Обычно 20–60 секунд, сложные объявления — дольше";
            notice.hidden = !startLoading;
        }

        // Reset completion classes from previous run
        const loadingEl = elements.aiModalLoading;
        if (loadingEl) {
            loadingEl.hidden = false;
            const ring = loadingEl.querySelector(".ai-loader-ring");
            const icon = loadingEl.querySelector(".ai-loader-icon");
            if (ring) {
                ring.classList.remove("ai-loader-ring--done");
                ring.classList.remove("ai-loader-ring--error");
            }
            if (icon) {
                icon.classList.remove("ai-loader-icon--done");
                icon.classList.remove("ai-loader-icon--error");
                icon.textContent = "AI";
            }
        }
        const barEl = elements.aiProgressBar;
        if (barEl) {
            barEl.classList.remove("ai-progress-bar--done");
            barEl.classList.remove("ai-progress-bar--error");
        }
        if (elements.aiModalError) elements.aiModalError.hidden = true;
        if (elements.aiModalResult) elements.aiModalResult.hidden = true;
        _cancelProgressFrame();
        _updateProgressDisplayInstant(startLoading ? 3 : 0);
        if (elements.aiModal) elements.aiModal.hidden = false;
        document.body.classList.add("modal-open");

        // Scroll to top
        const scrollBody = elements.aiModal?.querySelector(".ai-modal-body");
        if (scrollBody) scrollBody.scrollTop = 0;

        if (startLoading) {
            _startLoadingAnimation();
        } else if (loadingEl) {
            loadingEl.hidden = true;
        }
    }

    function closeAIModal() {
        _stopLoadingAnimation(false);
        if (elements.aiModal) elements.aiModal.hidden = true;
        document.body.classList.remove("modal-open");
    }

    async function loadAIAnalysis(adId) {
        const query = (state.detail?.query || state.query || "").trim();
        if (!adId || !query) return;

        const cached = state.detailAi;
        if (cached && cached.adId === adId && cached.result && !cached.error) {
            openAIModal(state.detail?.title || "", { startLoading: false });
            _renderAIModalResult(cached.result);
            return;
        }

        if (_aiLoading) return;
        _aiLoading = true;
        state.detailAi = {
            adId,
            loading: true,
            result: null,
            error: "",
            source: "ai",
        };

        openAIModal(state.detail?.title || "");

        try {
            // POST starts async analysis, returns task_id immediately
            const startResp = await postJson("/api/v1/ai/analyze", {
                ad_id: adId,
                query,
                category: state.category || undefined,
            });

            // Cached result returned immediately
            if (startResp.cached && startResp.result) {
                console.log("[AI] Cached result received");
                const result = startResp.result;
                state.detailAi = { adId, loading: false, result, error: "", source: "ai" };
                _stopLoadingAnimation(false);
                _renderAIModalResult(result);
                return;
            }

            const taskId = startResp.task_id;
            if (!taskId) {
                throw new Error("Сервер не вернул идентификатор задачи");
            }

            console.log("[AI] Task started:", taskId);

            // Poll for result every 3 seconds
            const POLL_INTERVAL = 3000;
            const POLL_TIMEOUT = 12000;
            const MAX_POLLS = 120; // 6 minutes max
            const MAX_CONSECUTIVE_POLL_ERRORS = 4;
            let pollCount = 0;
            let consecutivePollErrors = 0;

            await new Promise((resolve, reject) => {
                const poll = async () => {
                    pollCount++;
                    if (pollCount > MAX_POLLS) {
                        reject(new Error("Анализ занял слишком долго. Попробуйте ещё раз."));
                        return;
                    }
                    try {
                        const status = await getJson(`/api/v1/ai/task/${taskId}`, { timeout: POLL_TIMEOUT });
                        consecutivePollErrors = 0;
                        if (status.stage) {
                            _setStageLabel(status.stage);
                            console.log("[AI] stage:", status.stage, "progress:", status.progress);
                        }
                        if (status.status === "done") {
                            resolve(status.result);
                        } else if (status.status === "error") {
                            reject(new Error(status.error || "Ошибка AI анализа"));
                        } else {
                            // Still pending/processing — poll again
                            setTimeout(poll, POLL_INTERVAL);
                        }
                    } catch (err) {
                        const message = String(err?.message || "");
                        const isTaskMissing = message.includes("не найдена");
                        consecutivePollErrors += 1;

                        if (isTaskMissing && pollCount > 5) {
                            reject(new Error("Связь с задачей AI-анализа потеряна. Попробуйте открыть анализ ещё раз."));
                            return;
                        }

                        if (consecutivePollErrors >= MAX_CONSECUTIVE_POLL_ERRORS) {
                            reject(new Error("Не удалось стабильно получить статус AI-анализа. Проверьте соединение и попробуйте ещё раз."));
                            return;
                        }

                        setTimeout(poll, POLL_INTERVAL);
                    }
                };
                setTimeout(poll, POLL_INTERVAL);
            }).then((result) => {
                console.log("[AI] Analysis complete");
                state.detailAi = { adId, loading: false, result, error: "", source: "ai" };
                _showCompletionThen(() => _renderAIModalResult(result));
            }).catch((err) => {
                throw err;
            });
        } catch (err) {
            console.error("[AI] Request failed:", err);
            const message = err.message || "Не удалось выполнить анализ. Проверьте интернет-соединение.";
            state.detailAi = {
                adId,
                loading: false,
                result: null,
                error: message,
                source: "ai",
            };
            _showAIError(message, adId);
        } finally {
            _aiLoading = false;
        }
    }

    function _renderAiErrorState(container, message, onRetry) {
        const retryBtn = domEl("button", {
            className: "ai-retry-btn",
            type: "button",
            text: "Повторить",
        });
        retryBtn.addEventListener("click", onRetry);
        container.replaceChildren(
            domEl("div", { className: "ai-error", text: message }),
            retryBtn,
        );
    }

    function _buildAiSection(label, children, extraClass = "") {
        return domEl(
            "div",
            { className: `ai-section${extraClass ? ` ${extraClass}` : ""}` },
            domEl("span", { className: "ai-label", text: label }),
            children,
        );
    }

    function _buildAiList(tagName, className, items) {
        return domEl(
            tagName,
            { className },
            items.map((item) => domEl("li", { text: item })),
        );
    }

    function _splitAiSentences(text) {
        const normalized = String(text || "").replace(/\s+/g, " ").trim();
        if (!normalized) {
            return { lead: "", details: [] };
        }

        const parts = normalized
            .match(/[^.!?]+[.!?]?/gu)
            ?.map((part) => part.trim())
            .filter(Boolean) || [normalized];

        return {
            lead: parts[0] || normalized,
            details: parts.slice(1, 4),
        };
    }

    function _buildAiMetaPill(text, extraClass = "") {
        return domEl("span", {
            className: `ai-meta-pill${extraClass ? ` ${extraClass}` : ""}`,
            text,
        });
    }

    function _buildAiResultNodes(data) {
        const nodes = [];

        if (data.recommendation) {
            const verdictMap = {
                worth_it: { text: "Стоит брать", cls: "ai-badge--good" },
                think_twice: { text: "Подумай", cls: "ai-badge--warn" },
                overpriced: { text: "Дорого", cls: "ai-badge--bad" },
            };
            const verdict = verdictMap[data.recommendation.verdict] || verdictMap.think_twice;
            nodes.push(
                domEl(
                    "div",
                    { className: "ai-modal-verdict" },
                    domEl("span", { className: `ai-badge ${verdict.cls} ai-badge--lg`, text: verdict.text }),
                    data.summary ? domEl("p", { className: "ai-modal-summary", text: data.summary }) : null,
                )
            );
        }

        if (data.red_flags?.length) {
            nodes.push(
                _buildAiSection(
                    "Красные флаги",
                    domEl(
                        "div",
                        { className: "ai-flags" },
                        data.red_flags.map((flag, index) => domEl(
                            "div",
                            { className: "ai-flag-item" },
                            domEl("span", { className: "ai-flag-index mono", text: String(index + 1).padStart(2, "0") }),
                            domEl("span", { className: "ai-flag-text", text: flag }),
                        )),
                    ),
                    "ai-section--flags",
                )
            );
        }

        if (data.condition) {
            const cond = data.condition;
            // Defensive: condition might be a string from cached/poll result
            const condObj = typeof cond === "string" ? { label: cond } : cond;
            const condLabelMap = {
                "Отличное": { text: "Отличное", cls: "ai-badge--good" },
                "Хорошее": { text: "Хорошее", cls: "ai-badge--ok" },
                "Удовлетворительное": { text: "Удовлетв.", cls: "ai-badge--warn" },
                "Требует внимания": { text: "Внимание", cls: "ai-badge--bad" },
                "Плохое": { text: "Плохое", cls: "ai-badge--bad" },
                "Excellent": { text: "Отличное", cls: "ai-badge--good" },
                "Good": { text: "Хорошее", cls: "ai-badge--ok" },
                "Fair": { text: "Удовлетв.", cls: "ai-badge--warn" },
                "Poor": { text: "Плохое", cls: "ai-badge--bad" },
            };
            const mapped = condLabelMap[condObj.label] || { text: condObj.label || "—", cls: "ai-badge--ok" };
            nodes.push(
                _buildAiSection(
                    "Состояние по фото",
                    domFragment(
                        domEl("span", { className: `ai-badge ${mapped.cls}`, text: mapped.text }),
                        condObj.confidence
                            ? domEl("span", { className: "ai-confidence", text: `уверенность ${Math.round(condObj.confidence * 100)}%` })
                            : null,
                        condObj.notes?.length
                            ? domEl("ul", { className: "ai-notes" }, condObj.notes.map((note) => domEl("li", { text: note })))
                            : null,
                    ),
                )
            );
        }

        if (data.fair_price) {
            const fairPrice = data.fair_price;
            const fromPrice = fairPrice.from || fairPrice.from_price;
            const toPrice = fairPrice.to || fairPrice.to_price;
            let priceText = "";
            if (fromPrice != null && toPrice != null) {
                priceText = `${Math.round(fromPrice)} — ${Math.round(toPrice)} BYN`;
            } else if (fromPrice != null) {
                priceText = `~${Math.round(fromPrice)} BYN`;
            }
            nodes.push(
                _buildAiSection(
                    "Справедливая цена",
                    domFragment(
                        priceText ? domEl("div", { className: "ai-fair-price", text: priceText }) : null,
                        fairPrice.reasoning ? domEl("p", { className: "ai-reasoning", text: fairPrice.reasoning }) : null,
                    ),
                )
            );
        }

        if (data.resale_potential) {
            const resale = data.resale_potential;
            const prices = [resale.fast_price, resale.market_price, resale.optimal_price].filter(Boolean);
            if (prices.length) {
                nodes.push(
                    _buildAiSection(
                        "Потенциал перепродажи",
                        domFragment(
                            domEl(
                                "div",
                                { className: "ai-resale-prices" },
                                prices.map((price) => domEl(
                                    "div",
                                    { className: "ai-resale-row" },
                                    domEl("span", { className: "ai-resale-label", text: price.label }),
                                    domEl("span", { className: "ai-resale-price mono", text: `${Math.round(price.price_byn)} BYN` }),
                                    price.reasoning ? domEl("span", { className: "ai-resale-note", text: price.reasoning }) : null,
                                ))
                            ),
                            resale.reasoning ? domEl("p", { className: "ai-reasoning", text: resale.reasoning }) : null,
                        ),
                    )
                );
            }
        }

        if (data.market_context) {
            const marketContext = _splitAiSentences(data.market_context);
            const contextPills = [];
            if (data.similar_listings?.length) {
                contextPills.push(_buildAiMetaPill(`Аналогов: ${data.similar_listings.length}`, "ai-meta-pill--accent"));
            }
            if (data.best_alternative?.price_byn) {
                contextPills.push(_buildAiMetaPill(`Ориентир: ${formatPrice(data.best_alternative.price_byn)}`));
            }
            nodes.push(
                _buildAiSection(
                    "Контекст рынка",
                    domEl(
                        "div",
                        { className: "ai-market-context" },
                        domEl("p", { className: "ai-market-lead", text: marketContext.lead || data.market_context }),
                        marketContext.details.length
                            ? domEl(
                                "div",
                                { className: "ai-market-points" },
                                marketContext.details.map((item) => domEl("div", { className: "ai-market-point", text: item })),
                            )
                            : null,
                        contextPills.length
                            ? domEl("div", { className: "ai-meta-pills" }, contextPills)
                            : null,
                    ),
                    "ai-section--market",
                )
            );
        }

        if (data.best_alternative) {
            const bestAlternative = data.best_alternative;
            const bestPills = [];
            if (bestAlternative.price_byn) {
                bestPills.push(_buildAiMetaPill(formatPrice(bestAlternative.price_byn), "ai-meta-pill--good"));
            }
            if (bestAlternative.condition) {
                bestPills.push(_buildAiMetaPill(bestAlternative.condition, "ai-meta-pill--muted"));
            }
            nodes.push(
                _buildAiSection(
                    "Лучший вариант",
                    domFragment(
                        domEl(
                            "div",
                            { className: "ai-best-caption" },
                            domEl("span", { className: "ai-best-kicker", text: "Самый близкий аналог из найденных" }),
                            data.best_pick_reason ? domEl("p", { className: "ai-best-reason", text: data.best_pick_reason }) : null,
                        ),
                        domEl(
                            "a",
                            {
                                className: "ai-best-link",
                                attrs: { href: safeUrl(bestAlternative.link), target: "_blank", rel: "noreferrer noopener" },
                            },
                            bestAlternative.image_url
                                ? domEl("img", {
                                    className: "ai-best-thumb",
                                    attrs: { src: safeUrl(bestAlternative.image_url), alt: "", loading: "lazy" },
                                })
                                : null,
                            domEl(
                                "div",
                                { className: "ai-best-info" },
                                domEl("span", { className: "ai-best-title", text: bestAlternative.title }),
                                bestPills.length ? domEl("div", { className: "ai-meta-pills" }, bestPills) : null,
                            ),
                            domEl("span", { className: "ai-best-cta", text: "Открыть" }),
                        ),
                    ),
                    "ai-section--best",
                )
            );
        }

        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter((item) => item.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                nodes.push(
                    _buildAiSection(
                        `Другие варианты (${others.length})`,
                        domEl(
                            "div",
                            { className: "ai-similar" },
                            others.map((item) => domEl(
                                "a",
                                {
                                    className: "ai-similar-item",
                                    attrs: { href: safeUrl(item.link), target: "_blank", rel: "noreferrer noopener" },
                                },
                                item.image_url
                                    ? domEl("img", {
                                        className: "ai-similar-thumb",
                                        attrs: { src: safeUrl(item.image_url), alt: "", loading: "lazy" },
                                    })
                                    : null,
                                domEl(
                                    "div",
                                    { className: "ai-similar-info" },
                                    domEl("span", { className: "ai-similar-title", text: item.title }),
                                    domEl(
                                        "span",
                                        {
                                            className: "ai-similar-price mono",
                                            text: `${Math.round(item.price_byn)} BYN${item.condition ? ` · ${item.condition}` : ""}`,
                                        },
                                    ),
                                ),
                            ))
                        ),
                    )
                );
            }
        }

        if (data.watch_out?.length) {
            nodes.push(
                _buildAiSection(
                    "На что обратить внимание",
                    domEl(
                        "div",
                        { className: "ai-watch-list" },
                        data.watch_out.map((item) => {
                            // Defensive: item might be a string instead of {point, why}
                            const point = typeof item === "string" ? item : (item.point || "");
                            const why = typeof item === "string" ? "" : (item.why || "");
                            return domEl(
                                "div",
                                { className: "ai-watch-item" },
                                domEl("strong", { text: point }),
                                why ? domEl("span", { text: why }) : null,
                            );
                        })
                    ),
                )
            );
        }

        if (data.meeting_checklist?.length) {
            nodes.push(
                _buildAiSection(
                    "Чек-лист для встречи",
                    _buildAiList("ol", "ai-checklist", data.meeting_checklist),
                )
            );
        }

        if (data.negotiation_tips?.length) {
            nodes.push(
                _buildAiSection(
                    "Как торговаться",
                    _buildAiList("ul", "ai-tips", data.negotiation_tips),
                )
            );
        }

        if (data.recommendation?.text) {
            nodes.push(
                _buildAiSection(
                    "Рекомендация",
                    domEl("p", { className: "ai-recommendation-text", text: data.recommendation.text }),
                    "ai-section--recommendation",
                )
            );
        }

        nodes.push(
            domEl("p", {
                className: "ai-disclaimer",
                text: data.disclaimer || "Анализ носит информационный характер. Результаты не являются гарантией.",
            })
        );
        return nodes;
    }

    function _showAIError(message, adId) {
        _stopLoadingAnimation(false);

        const loadingEl = elements.aiModalLoading;
        if (loadingEl) {
            const ring = loadingEl.querySelector(".ai-loader-ring");
            const icon = loadingEl.querySelector(".ai-loader-icon");
            if (ring) ring.classList.add("ai-loader-ring--error");
            if (icon) {
                icon.textContent = "✕";
                icon.classList.add("ai-loader-icon--error");
            }
            if (elements.aiLoaderText) elements.aiLoaderText.textContent = "Ошибка анализа";
        }
        const barEl = elements.aiProgressBar;
        if (barEl) barEl.classList.add("ai-progress-bar--error");

        setTimeout(() => {
            if (elements.aiModalLoading) elements.aiModalLoading.hidden = true;
            if (elements.aiModalResult) elements.aiModalResult.hidden = true;
            if (elements.aiModalError) {
                elements.aiModalError.hidden = false;
                _renderAiErrorState(elements.aiModalError, message, () => loadAIAnalysis(adId));
            }
        }, 700);
    }

    function _renderAIModalResult(data) {
        console.log("[AI] Rendering result, data keys:", data ? Object.keys(data).join(",") : "null");
        _lastAiData = data;

        if (elements.aiModalLoading) elements.aiModalLoading.hidden = true;
        if (elements.aiModalError) elements.aiModalError.hidden = true;

        const container = elements.aiModalResult;
        if (!container) return;
        container.hidden = false;

        // Show PDF export button
        const pdfBtn = document.getElementById("ai-export-pdf");
        if (pdfBtn) pdfBtn.hidden = false;

        // Hide time notice when results appear
        const notice = elements.aiModal?.querySelector(".ai-time-notice");
        if (notice) notice.hidden = true;

        container.replaceChildren(domFragment(_buildAiResultNodes(data)));
    }

    /* ===== PDF Export ===== */

    function _buildPdfSections(data) {
        const sections = [];

        // Verdict
        if (data.recommendation) {
            const verdictMap = {
                worth_it: "Стоит брать",
                think_twice: "Подумай",
                overpriced: "Дорого",
            };
            sections.push({
                title: "Вердикт",
                body: (verdictMap[data.recommendation.verdict] || data.recommendation.verdict)
                    + (data.summary ? "\n\n" + data.summary : ""),
            });
        }

        // Condition
        if (data.condition) {
            const c = typeof data.condition === "string" ? { label: data.condition } : data.condition;
            let body = c.label || "";
            if (c.confidence) body += ` (уверенность ${Math.round(c.confidence * 100)}%)`;
            if (c.notes?.length) body += "\n\n" + c.notes.map(n => "• " + n).join("\n");
            sections.push({ title: "Состояние по фото", body });
        }

        // Fair price
        if (data.fair_price) {
            const fp = data.fair_price;
            const from = fp.from || fp.from_price;
            const to = fp.to || fp.to_price;
            let body = from != null && to != null
                ? `${Math.round(from)} — ${Math.round(to)} BYN`
                : from != null ? `~${Math.round(from)} BYN` : "";
            if (fp.reasoning) body += "\n\n" + fp.reasoning;
            sections.push({ title: "Справедливая цена", body });
        }

        // Resale
        if (data.resale_potential) {
            const rp = data.resale_potential;
            const prices = [rp.fast_price, rp.market_price, rp.optimal_price].filter(Boolean);
            if (prices.length) {
                let body = prices.map(p => `${p.label}: ${Math.round(p.price_byn)} BYN`).join("\n");
                if (rp.reasoning) body += "\n\n" + rp.reasoning;
                sections.push({ title: "Потенциал перепродажи", body });
            }
        }

        // Market context
        if (data.market_context) {
            sections.push({ title: "Контекст рынка", body: data.market_context });
        }

        // Best alternative
        if (data.best_alternative) {
            const ba = data.best_alternative;
            let body = `${ba.title} — ${Math.round(ba.price_byn)} BYN`;
            if (ba.condition) body += ` (${ba.condition})`;
            if (ba.link) body += "\n" + ba.link;
            if (data.best_pick_reason) body += "\n\n" + data.best_pick_reason;
            sections.push({ title: "Лучший вариант", body });
        }

        // Similar listings
        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter(s => s.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                const body = others.map(s => `${s.title} — ${Math.round(s.price_byn)} BYN${s.condition ? " (" + s.condition + ")" : ""}`).join("\n");
                sections.push({ title: `Другие варианты (${others.length})`, body });
            }
        }

        // Watch out
        if (data.watch_out?.length) {
            const body = data.watch_out.map(w => {
                const point = typeof w === "string" ? w : w.point;
                const why = typeof w === "string" ? "" : w.why;
                return why ? `${point}: ${why}` : point;
            }).join("\n\n");
            sections.push({ title: "На что обратить внимание", body });
        }

        // Meeting checklist
        if (data.meeting_checklist?.length) {
            const body = data.meeting_checklist.map((item, i) => `${i + 1}. ${item}`).join("\n");
            sections.push({ title: "Чек-лист для встречи", body });
        }

        // Negotiation tips
        if (data.negotiation_tips?.length) {
            const body = data.negotiation_tips.map(t => "• " + t).join("\n");
            sections.push({ title: "Как торговаться", body });
        }

        // Red flags
        if (data.red_flags?.length) {
            const body = data.red_flags.map(f => "⚠ " + f).join("\n");
            sections.push({ title: "Красные флаги", body });
        }

        // Recommendation text
        if (data.recommendation?.text) {
            sections.push({ title: "Рекомендация", body: data.recommendation.text });
        }

        return sections;
    }

    async function exportToPdf() {
        const data = _lastAiData;
        if (!data) return;

        const detail = state.detail || {};
        const title = detail.title || "Объявление";
        const price = detail.price ? formatPrice(detail.price) : "";
        const adId = data.ad_id || "";
        const link = detail.link || (adId ? `https://www.kufar.by/item/${adId}` : "");
        const dateStr = new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
        const sections = _buildPdfSections(data);

        // Listing images from detail
        const listingImages = (detail.images || []).slice(0, 4);

        // Listing parameters from detail
        const listingParams = detail.parameters || [];

        // Verdict section extracted for hero treatment
        const verdictSection = sections.find(s => s.title === "Вердикт");
        const otherSections = sections.filter(s => s.title !== "Вердикт");
        const vMap = { "Стоит брать": { cls: "good", icon: "&#10003;" }, "Подумай": { cls: "warn", icon: "&#9888;" }, "Дорого": { cls: "bad", icon: "&#10007;" } };
        const verdictLine = verdictSection ? verdictSection.body.split("\n")[0] : "";
        const verdictSummary = verdictSection ? verdictSection.body.split("\n").slice(1).join("\n").trim() : "";
        const vInfo = vMap[verdictLine] || { cls: "warn", icon: "&#9888;" };

        // Build best alternative card HTML
        let bestAltHtml = "";
        if (data.best_alternative) {
            const ba = data.best_alternative;
            bestAltHtml = `<div class="alt-card">
  ${ba.image_url ? `<img class="alt-thumb" src="${_safeXmlUrl(ba.image_url)}" alt="" />` : ""}
  <div class="alt-info">
    <div class="alt-title">${_escXml(ba.title)}</div>
    <div class="alt-meta">
      <span class="alt-price mono">${Math.round(ba.price_byn)} BYN</span>
      ${ba.condition ? `<span class="alt-cond">${_escXml(ba.condition)}</span>` : ""}
    </div>
    ${data.best_pick_reason ? `<div class="alt-reason">${_escXml(data.best_pick_reason)}</div>` : ""}
    ${ba.link ? `<a class="alt-link" href="${_safeXmlUrl(ba.link)}">Открыть на Kufar</a>` : ""}
  </div>
</div>`;
        }

        // Build similar listings cards HTML
        let similarHtml = "";
        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter(s => s.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                similarHtml = `<section class="section">
  <div class="section-header">
    <div class="section-dot"></div>
    <div class="section-title">Другие варианты (${others.length})</div>
  </div>
  <div class="similar-list">
${others.slice(0, 8).map(s => `    <div class="similar-row">
      ${s.image_url ? `<img class="similar-thumb" src="${_safeXmlUrl(s.image_url)}" alt="" />` : `<span></span>`}
      <div>
        <div class="similar-title">${_escXml(s.title)}</div>
        <div class="similar-meta">${s.condition ? _escXml(s.condition) : "Состояние не указано"}${s.link ? ` · <a href="${_safeXmlUrl(s.link)}">Открыть</a>` : ""}</div>
      </div>
      <div class="similar-price mono">${Math.round(s.price_byn)} BYN</div>
    </div>`).join("\n")}
  </div>
</section>`;
                // Remove "Другие варианты" from text sections so it doesn't duplicate
                const idx = otherSections.findIndex(s => s.title.startsWith("Другие варианты"));
                if (idx >= 0) otherSections.splice(idx, 1);
            }
        }

        // Remove "Лучший вариант" from text sections (rendered as card above)
        const bestIdx = otherSections.findIndex(s => s.title === "Лучший вариант");
        if (bestIdx >= 0) otherSections.splice(bestIdx, 1);

        const pdfHtml = `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>${_escXml(title)}</title>
<style>
  @page { margin: 14mm; size: A4; }
  * { box-sizing: border-box; }
  html { background: #eef2f7; }
  body { margin: 0; font-family: Arial, Helvetica, sans-serif; color: #111827; font-size: 9.6pt; line-height: 1.52; background: #f8fafc; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  a { color: #2563eb; text-decoration: none; word-break: break-word; }
  .mono { font-family: "Courier New", Courier, monospace; font-variant-numeric: tabular-nums; }
  .print-banner { display: none; padding: 12px 16px; background: #eff6ff; border-bottom: 1px solid #bfdbfe; color: #1e3a8a; font-size: 9pt; }
  .page-shell { max-width: 820px; margin: 0 auto; background: #ffffff; min-height: 100vh; }
  .report-head { padding: 24px 28px 18px; color: #f8fafc; background: #0f172a; border-radius: 0 0 22px 22px; position: relative; overflow: hidden; }
  .report-head::after { content: ""; position: absolute; right: -72px; top: -92px; width: 220px; height: 220px; border: 1px solid rgba(147, 197, 253, 0.25); border-radius: 999px; box-shadow: 0 0 0 22px rgba(59, 130, 246, 0.055); }
  .brand-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 22px; position: relative; z-index: 1; }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 800; letter-spacing: -0.03em; }
  .brand-mark { width: 32px; height: 32px; border-radius: 11px; display: grid; place-items: center; background: #0b1220; color: #bfdbfe; border: 1px solid rgba(147, 197, 253, 0.35); box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04); font-size: 11pt; }
  .report-meta { color: rgba(226, 232, 240, 0.72); font-size: 8.3pt; text-align: right; }
  .hero-grid { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 20px; align-items: start; position: relative; z-index: 1; }
  .eyebrow { display: inline-flex; margin-bottom: 9px; padding: 3px 9px; border-radius: 999px; background: rgba(59, 130, 246, 0.18); border: 1px solid rgba(147, 197, 253, 0.22); color: #bfdbfe; font-size: 7.8pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em; }
  h1 { margin: 0; max-width: 560px; font-size: 18pt; line-height: 1.16; letter-spacing: -0.045em; }
  .hero-price { margin-top: 10px; font-size: 19pt; line-height: 1; color: #93c5fd; font-weight: 800; }
  .hero-link { display: block; margin-top: 10px; max-width: 560px; color: rgba(219, 234, 254, 0.82); font-size: 8.2pt; }
  .photo-strip { display: grid; grid-template-columns: repeat(2, 58px); gap: 7px; }
  .photo-strip img { width: 58px; height: 58px; object-fit: cover; border-radius: 12px; border: 1px solid rgba(255,255,255,0.18); background: rgba(255,255,255,0.06); }
  .verdict-card { margin: -10px 28px 18px; padding: 15px 16px; display: grid; grid-template-columns: 42px 1fr; gap: 13px; align-items: center; border-radius: 17px; background: #ffffff; border: 1px solid #e5e7eb; box-shadow: 0 18px 45px rgba(15, 23, 42, 0.11); position: relative; z-index: 2; break-inside: avoid; }
  .verdict-card.good { border-left: 5px solid #16a34a; }
  .verdict-card.warn { border-left: 5px solid #d97706; }
  .verdict-card.bad { border-left: 5px solid #dc2626; }
  .verdict-icon { width: 42px; height: 42px; border-radius: 14px; display: grid; place-items: center; font-size: 16pt; font-weight: 900; }
  .good .verdict-icon { background: #dcfce7; color: #15803d; }
  .warn .verdict-icon { background: #fef3c7; color: #b45309; }
  .bad .verdict-icon { background: #fee2e2; color: #dc2626; }
  .verdict-text { font-size: 14pt; font-weight: 850; letter-spacing: -0.035em; }
  .verdict-summary { margin-top: 3px; color: #475569; font-size: 9.2pt; }
  .facts-grid { margin: 0 28px 18px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; break-inside: avoid; }
  .fact { padding: 10px 11px; border: 1px solid #e5e7eb; border-radius: 13px; background: #f8fafc; min-width: 0; }
  .fact-label { display: block; color: #64748b; font-size: 7.3pt; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
  .fact-value { display: block; margin-top: 2px; color: #111827; font-size: 10pt; font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .params-strip { margin: 0 28px 18px; display: flex; flex-wrap: wrap; gap: 6px; break-inside: avoid; }
  .param-chip { padding: 4px 8px; border-radius: 999px; background: #eff6ff; color: #334155; font-size: 7.8pt; border: 1px solid #dbeafe; }
  .param-chip b { color: #1d4ed8; }
  .content { padding: 0 28px 28px; display: grid; gap: 11px; }
  .section { padding: 13px 14px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 15px; break-inside: avoid; }
  .section--price { background: #eff6ff; border-color: #bfdbfe; }
  .section--flags { background: #fff1f2; border-color: #fecdd3; }
  .section-header { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
  .section-dot { width: 7px; height: 7px; border-radius: 99px; background: #2563eb; flex: 0 0 auto; }
  .section--flags .section-dot { background: #e11d48; }
  .section--best .section-dot { background: #16a34a; }
  .section-title { color: #1e3a8a; font-size: 8pt; font-weight: 850; text-transform: uppercase; letter-spacing: 0.08em; }
  .section--flags .section-title { color: #be123c; }
  .section--best .section-title { color: #166534; }
  .section-body { color: #334155; white-space: pre-wrap; }
  .section--price .section-body { color: #0f172a; font-size: 10.8pt; font-weight: 750; }
  .alt-card { display: grid; grid-template-columns: 68px 1fr; gap: 11px; padding: 10px; border-radius: 13px; background: #f0fdf4; border: 1px solid #bbf7d0; }
  .alt-thumb { width: 68px; height: 68px; object-fit: cover; border-radius: 10px; }
  .alt-title { color: #0f172a; font-weight: 800; line-height: 1.25; }
  .alt-meta { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 8px; color: #64748b; font-size: 8.5pt; }
  .alt-price { color: #15803d; font-size: 11pt; font-weight: 900; }
  .alt-reason { margin-top: 5px; color: #475569; font-size: 8.6pt; }
  .alt-link { display: inline-block; margin-top: 5px; font-size: 8.2pt; }
  .similar-list { display: grid; gap: 6px; }
  .similar-row { display: grid; grid-template-columns: 42px 1fr auto; gap: 9px; align-items: center; padding: 7px; border-radius: 11px; background: #f8fafc; border: 1px solid #e5e7eb; }
  .similar-thumb { width: 42px; height: 42px; object-fit: cover; border-radius: 9px; }
  .similar-title { color: #1f2937; font-weight: 750; font-size: 8.8pt; line-height: 1.25; max-height: 2.5em; overflow: hidden; }
  .similar-meta { color: #64748b; font-size: 7.8pt; }
  .similar-price { color: #111827; font-weight: 900; font-size: 9pt; white-space: nowrap; }
  .footer { padding: 14px 28px 18px; color: #94a3b8; font-size: 7.5pt; border-top: 1px solid #e5e7eb; }
  .footer-brand { color: #475569; font-weight: 850; }
  @media screen { body { padding: 24px 0; } .page-shell { box-shadow: 0 24px 80px rgba(15, 23, 42, 0.18); border-radius: 24px; overflow: hidden; } .print-banner { display: block; max-width: 820px; margin: 0 auto; border-radius: 16px 16px 0 0; } }
  @media print { html, body { background: #ffffff; } .page-shell { max-width: none; } .print-banner { display: none !important; } .report-head { border-radius: 0 0 18px 18px; } .content { gap: 8px; } .section { padding: 10px 11px; } }
</style>
</head>
<body>
<div class="print-banner">Если диалог печати не открылся автоматически, используйте печать из меню браузера и выберите «Сохранить как PDF».</div>
<div class="page-shell">
  <header class="report-head">
    <div class="brand-row">
      <div class="brand"><div class="brand-mark">RF</div><span>Rafuk</span></div>
      <div class="report-meta"><div>AI market memo</div><div>${dateStr}${adId ? ` · ID ${_escXml(String(adId))}` : ""}</div></div>
    </div>
    <div class="hero-grid">
      <div>
        <div class="eyebrow">Kufar buyer intelligence</div>
        <h1>${_escXml(title)}</h1>
        ${price ? `<div class="hero-price mono">${_escXml(price)}</div>` : ""}
        ${link ? `<a class="hero-link" href="${_safeXmlUrl(link)}">${_escXml(link)}</a>` : ""}
      </div>
      ${listingImages.length ? `<div class="photo-strip">${listingImages.map(img => `<img src="${_safeXmlUrl(img)}" alt="" />`).join("")}</div>` : ""}
    </div>
  </header>

  ${verdictSection ? `<section class="verdict-card ${vInfo.cls}">
    <div class="verdict-icon">${vInfo.icon}</div>
    <div><div class="verdict-text">${_escXml(verdictLine)}</div>${verdictSummary ? `<div class="verdict-summary">${_escXml(verdictSummary)}</div>` : ""}</div>
  </section>` : ""}

  <section class="facts-grid">
    <div class="fact"><span class="fact-label">Цена</span><span class="fact-value mono">${price ? _escXml(price) : "—"}</span></div>
    <div class="fact"><span class="fact-label">Дата отчёта</span><span class="fact-value">${dateStr}</span></div>
    <div class="fact"><span class="fact-label">Объявление</span><span class="fact-value mono">${adId ? _escXml(String(adId)) : "—"}</span></div>
  </section>

  ${listingParams.length ? `<section class="params-strip">${listingParams.slice(0, 10).map(p => `<span class="param-chip"><b>${_escXml(p.label)}</b> ${_escXml(p.value)}</span>`).join("")}</section>` : ""}

  <main class="content">
${otherSections.map(s => {
    const isPrice = s.title === "Справедливая цена" || s.title === "Потенциал перепродажи";
    const isFlags = s.title === "Красные флаги";
    const sectionCls = isPrice ? " section--price" : isFlags ? " section--flags" : "";
    return `    <section class="section${sectionCls}">
      <div class="section-header"><div class="section-dot"></div><div class="section-title">${_escXml(s.title)}</div></div>
      <div class="section-body">${_escXml(s.body)}</div>
    </section>`;
}).join("\n")}

${data.best_alternative ? `    <section class="section section--best">
      <div class="section-header"><div class="section-dot"></div><div class="section-title">Лучший вариант</div></div>
      ${bestAltHtml}
    </section>` : ""}

${similarHtml}
  </main>

  <footer class="footer"><span class="footer-brand">Rafuk</span> — ${_escXml(data.disclaimer || "Анализ носит информационный характер. Результаты не являются гарантией.")}</footer>
</div>
</body>
</html>`;

        const isTelegram = !!window.Telegram?.WebApp?.initData;

        if (isTelegram) {
            try {
                const exportResp = await postJson("/api/v1/ai/export-report", { html: pdfHtml });
                const exportUrl = exportResp?.url;
                if (!exportUrl) {
                    throw new Error("Сервер не вернул ссылку на экспорт");
                }
                if (window.Telegram?.WebApp?.openLink) {
                    window.Telegram.WebApp.openLink(exportUrl);
                } else {
                    window.location.href = exportUrl;
                }
                return;
            } catch (err) {
                console.error("[AI] Telegram PDF export failed:", err);
            }
        }

        const win = window.open("", "_blank");
        if (win) {
            win.document.write(pdfHtml);
            win.document.close();
            win.onload = function () { win.print(); };
        }
    }

    function _escXml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    // Sanitize URLs for href/src attributes — blocks javascript:, data:,
    // vbscript:, file:, and any other non-http(s) scheme. Returns "#"
    // for unsafe values so the link is rendered but does nothing on click.
    function _safeXmlUrl(url) {
        if (!url || typeof url !== "string") return "#";
        const trimmed = url.trim();
        const lowered = trimmed.toLowerCase();
        if (lowered.startsWith("https://") || lowered.startsWith("http://")) {
            return _escXml(trimmed);
        }
        // Allow same-origin relative URLs (no scheme).
        if (trimmed.startsWith("/") && !trimmed.startsWith("//")) {
            return _escXml(trimmed);
        }
        return "#";
    }

    // Bind PDF export button
    const pdfBtn = document.getElementById("ai-export-pdf");
    if (pdfBtn) {
        pdfBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            exportToPdf();
        });
    }

    return {
        loadAIAnalysis,
        closeAIModal,
        exportToPdf,
    };
}
