/**
 * api_ai.js — AI listing analysis with dedicated modal + PDF export.
 */
function createApiAi(context) {
    const { state, elements, postJson, getJson, safeUrl, formatPrice } = context;

    let _aiLoading = false;
    let _aiProgress = 0;
    let _lastAiData = null;

    const LOADING_STEPS = [
        "Загружаю данные объявления...",
        "Анализирую фотографии...",
        "Сравниваю с рынком...",
        "Подбираю альтернативы...",
        "Формирую рекомендации...",
        "Осталось немного...",
    ];
    const PROGRESS_CAPS = [
        { server: 0, cap: 12 },
        { server: 10, cap: 28 },
        { server: 30, cap: 56 },
        { server: 50, cap: 82 },
        { server: 85, cap: 96 },
    ];
    const STAGE_LABELS = {
        queued: "Ставлю задачу в очередь...",
        loading_market_data: "Загружаю данные объявления...",
        search_ready: "Подбираю рынок и аналоги...",
        photo_precheck: "Быстро оцениваю фото...",
        calling_ai: "Отправляю данные в AI...",
        building_response: "Собираю итоговый анализ...",
        done: "Анализ завершён!",
        timeout: "AI анализ занял слишком долго...",
        error: "Не удалось завершить AI анализ...",
        target_missing: "Объявление не найдено...",
    };

    function _updateProgressDisplay(pct) {
        _aiProgress = pct;
        const barEl = elements.aiProgressBar;
        const pctEl = elements.aiProgressPct;
        if (barEl) barEl.style.width = pct + "%";
        if (pctEl) pctEl.textContent = Math.round(pct) + "%";
    }

    function _setStageLabel(stage) {
        const textEl = elements.aiLoaderText;
        if (!textEl || !stage || !STAGE_LABELS[stage]) return;
        textEl.textContent = STAGE_LABELS[stage];
    }

    /**
     * Progress model: server sends milestones (10, 30, 50, 85).
     * Between milestones we creep slowly so the bar never stalls.
     * When a new server milestone arrives, we jump toward it quickly.
     * The bar never reaches 100% until analysis is truly done.
     */
    let _serverCeiling = 0; // highest server milestone seen

    function _setServerProgress(serverPct) {
        if (serverPct > _serverCeiling) {
            _serverCeiling = serverPct;
        }
    }

    function _getProgressCap(serverPct) {
        let cap = 12;
        for (let i = 0; i < PROGRESS_CAPS.length; i++) {
            if (serverPct >= PROGRESS_CAPS[i].server) {
                cap = PROGRESS_CAPS[i].cap;
            }
        }
        return cap;
    }

    function _startLoadingAnimation() {
        let step = 0;
        _aiProgress = 0;
        _serverCeiling = 0;
        const textEl = elements.aiLoaderText;

        function tick() {
            step = (step + 1) % LOADING_STEPS.length;
            if (textEl) textEl.textContent = LOADING_STEPS[step];

            const maxLocal = _getProgressCap(_serverCeiling);
            if (_aiProgress < _serverCeiling) {
                const gap = _serverCeiling - _aiProgress;
                const jump = Math.max(1.6, gap * 0.45);
                _aiProgress = Math.min(_aiProgress + jump, _serverCeiling);
            } else if (_aiProgress < maxLocal) {
                const remaining = maxLocal - _aiProgress;
                const minCreep = _serverCeiling >= 85 ? 0.15 : 0.35;
                const maxCreep = _serverCeiling >= 85 ? 0.35 : 0.75;
                const creep = Math.min(
                    remaining,
                    minCreep + Math.random() * (maxCreep - minCreep),
                );
                _aiProgress = Math.min(_aiProgress + creep, maxLocal);
            }
            _updateProgressDisplay(_aiProgress);
        }

        if (textEl) textEl.textContent = LOADING_STEPS[0];
        _updateProgressDisplay(2);
        state.aiLoadingTimer = setInterval(tick, 800);
    }

    function _stopLoadingAnimation(success = true) {
        if (state.aiLoadingTimer) {
            clearInterval(state.aiLoadingTimer);
            state.aiLoadingTimer = null;
        }
        if (success) {
            _updateProgressDisplay(100);
            const barEl = elements.aiProgressBar;
            if (barEl) barEl.classList.add("ai-progress-bar--done");
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
        }, 500);
    }

    function openAIModal(subtitle) {
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
                notice.textContent = "Анализ занимает 2–3 минуты";
                const loadingEl = elements.aiModalLoading;
                if (loadingEl) {
                    loadingEl.parentNode.insertBefore(notice, loadingEl.nextSibling);
                }
            }
            notice.hidden = false;
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
        if (elements.aiModal) elements.aiModal.hidden = false;
        document.body.classList.add("modal-open");

        // Scroll to top
        const scrollBody = elements.aiModal?.querySelector(".ai-modal-body");
        if (scrollBody) scrollBody.scrollTop = 0;

        _startLoadingAnimation();
    }

    function closeAIModal() {
        _stopLoadingAnimation();
        if (elements.aiModal) elements.aiModal.hidden = true;
        document.body.classList.remove("modal-open");
    }

    async function loadAIAnalysis(adId) {
        const query = (state.detail?.query || state.query || "").trim();
        if (!adId || !query) return;

        const cached = state.detailAi;
        if (cached && cached.adId === adId && cached.result && !cached.error) {
            openAIModal(state.detail?.title || "");
            _stopLoadingAnimation();
            setTimeout(() => _renderAIModalResult(cached.result), 200);
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
                _showCompletionThen(() => _renderAIModalResult(result));
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
                        // Update server milestone — local timer creeps toward it
                        if (status.progress > 0) {
                            _setServerProgress(status.progress);
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
            const condLabelMap = {
                "Отличное": { text: "Отличное", cls: "ai-badge--good" },
                "Хорошее": { text: "Хорошее", cls: "ai-badge--ok" },
                "Удовлетворительное": { text: "Удовлетв.", cls: "ai-badge--warn" },
                "Плохое": { text: "Плохое", cls: "ai-badge--bad" },
                "Excellent": { text: "Отличное", cls: "ai-badge--good" },
                "Good": { text: "Хорошее", cls: "ai-badge--ok" },
                "Fair": { text: "Удовлетв.", cls: "ai-badge--warn" },
                "Poor": { text: "Плохое", cls: "ai-badge--bad" },
            };
            const mapped = condLabelMap[cond.label] || { text: cond.label, cls: "ai-badge--ok" };
            nodes.push(
                _buildAiSection(
                    "Состояние по фото",
                    domFragment(
                        domEl("span", { className: `ai-badge ${mapped.cls}`, text: mapped.text }),
                        cond.confidence
                            ? domEl("span", { className: "ai-confidence", text: `уверенность ${Math.round(cond.confidence * 100)}%` })
                            : null,
                        cond.notes?.length
                            ? domEl("ul", { className: "ai-notes" }, cond.notes.map((note) => domEl("li", { text: note })))
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
                        data.watch_out.map((item) => domEl(
                            "div",
                            { className: "ai-watch-item" },
                            domEl("strong", { text: item.point }),
                            domEl("span", { text: item.why }),
                        ))
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
            const c = data.condition;
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
            const body = data.watch_out.map(w => `${w.point}: ${w.why}`).join("\n\n");
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

        const title = state.detail?.title || "Объявление";
        const price = state.detail?.price ? formatPrice(state.detail?.price) : "";
        const adId = data.ad_id || "";
        const link = state.detail?.link || (adId ? `https://www.kufar.by/item/${adId}` : "");
        const dateStr = new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
        const sections = _buildPdfSections(data);

        // Verdict section extracted for hero treatment
        const verdictSection = sections.find(s => s.title === "Вердикт");
        const otherSections = sections.filter(s => s.title !== "Вердикт");
        const vMap = { "Стоит брать": { cls: "good", icon: "&#10003;" }, "Подумай": { cls: "warn", icon: "&#9888;" }, "Дорого": { cls: "bad", icon: "&#10007;" } };
        const verdictLine = verdictSection ? verdictSection.body.split("\n")[0] : "";
        const verdictSummary = verdictSection ? verdictSection.body.split("\n").slice(1).join("\n").trim() : "";
        const vInfo = vMap[verdictLine] || { cls: "warn", icon: "&#9888;" };

        const pdfHtml = `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>${_escXml(title)}</title>
<style>
  @page { margin: 0; size: A4; }
  @page :first { margin-top: 0; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1e293b; font-size: 10pt; line-height: 1.6; background: #fff; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .print-banner { display: none; padding: 14px 18px; background: #eff6ff; border-bottom: 1px solid #bfdbfe; }
  .print-banner-inner { max-width: 860px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .print-banner-text { color: #1e3a8a; font-size: 9pt; }
  .print-banner-btn { appearance: none; border: none; border-radius: 10px; background: #2563eb; color: #fff; padding: 10px 14px; font: inherit; font-size: 9pt; font-weight: 700; cursor: pointer; }

  /* ── Hero ── */
  .hero { background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%); color: #fff; padding: 36px 32px 28px; position: relative; overflow: hidden; }
  .hero::after { content: ""; position: absolute; top: -40px; right: -40px; width: 200px; height: 200px; background: rgba(59,130,246,0.15); border-radius: 50%; }
  .hero-badge { display: inline-block; background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.2); border-radius: 6px; padding: 3px 10px; font-size: 8pt; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: rgba(255,255,255,0.8); margin-bottom: 12px; }
  .hero h1 { font-size: 18pt; font-weight: 800; line-height: 1.25; margin-bottom: 6px; max-width: 85%; }
  .hero-price { font-size: 16pt; font-weight: 700; color: #60a5fa; margin-bottom: 10px; }
  .hero-meta { font-size: 8.5pt; color: rgba(255,255,255,0.5); display: flex; gap: 16px; flex-wrap: wrap; }
  .hero-meta span { display: inline-flex; align-items: center; gap: 4px; }
  .hero-link { color: rgba(255,255,255,0.6); text-decoration: none; font-size: 8pt; word-break: break-all; }

  /* ── Verdict strip ── */
  .verdict-strip { padding: 16px 32px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid #e2e8f0; }
  .verdict-strip.good { background: linear-gradient(90deg, #f0fdf4 0%, #fff 100%); border-left: 4px solid #22c55e; }
  .verdict-strip.warn { background: linear-gradient(90deg, #fffbeb 0%, #fff 100%); border-left: 4px solid #f59e0b; }
  .verdict-strip.bad { background: linear-gradient(90deg, #fef2f2 0%, #fff 100%); border-left: 4px solid #ef4444; }
  .verdict-icon { width: 36px; height: 36px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 16pt; font-weight: 700; flex-shrink: 0; }
  .good .verdict-icon { background: #dcfce7; color: #15803d; }
  .warn .verdict-icon { background: #fef3c7; color: #b45309; }
  .bad .verdict-icon { background: #fee2e2; color: #dc2626; }
  .verdict-text { font-size: 13pt; font-weight: 700; }
  .good .verdict-text { color: #15803d; }
  .warn .verdict-text { color: #b45309; }
  .bad .verdict-text { color: #dc2626; }
  .verdict-summary { font-size: 9.5pt; color: #475569; margin-top: 3px; line-height: 1.5; }

  /* ── Content ── */
  .content { padding: 20px 32px 32px; }
  .section { margin-bottom: 18px; page-break-inside: avoid; }
  .section-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .section-dot { width: 6px; height: 6px; border-radius: 50%; background: #3b82f6; flex-shrink: 0; }
  .section-title { font-size: 9pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #3b82f6; }
  .section-body { font-size: 10pt; color: #334155; white-space: pre-wrap; padding-left: 14px; border-left: 2px solid #e2e8f0; line-height: 1.6; }

  /* ── Price range highlight ── */
  .section-body.price-highlight { background: #f8fafc; border-left-color: #3b82f6; padding: 8px 12px; border-radius: 0 6px 6px 0; font-weight: 600; font-size: 11pt; }

  /* ── Footer ── */
  .footer { margin-top: 28px; padding: 14px 32px; background: #f8fafc; border-top: 1px solid #e2e8f0; }
  .footer p { font-size: 7.5pt; color: #94a3b8; line-height: 1.5; }
  .footer-brand { font-weight: 600; color: #64748b; }
  @media screen {
    body { background: #e2e8f0; }
    .page-shell { max-width: 860px; margin: 0 auto; background: #fff; min-height: 100vh; box-shadow: 0 10px 40px rgba(15, 23, 42, 0.16); }
    .print-banner { display: block; }
  }
  @media print {
    .print-banner { display: none !important; }
    .page-shell { box-shadow: none; }
  }
</style>
</head>
<body>
<div class="print-banner">
  <div class="print-banner-inner">
    <div class="print-banner-text">Если диалог печати не открылся автоматически, нажмите кнопку и выберите «Сохранить как PDF».</div>
    <button class="print-banner-btn" type="button" onclick="window.print()">Печать / PDF</button>
  </div>
</div>
<div class="page-shell">

<div class="hero">
  <div class="hero-badge">Rafuks &middot; AI Report</div>
  <h1>${_escXml(title)}</h1>
  ${price ? `<div class="hero-price">${_escXml(price)}</div>` : ""}
  <div class="hero-meta">
    <span>${dateStr}</span>
    ${adId ? `<span>ID ${_escXml(String(adId))}</span>` : ""}
  </div>
  ${link ? `<a class="hero-link" href="${_escXml(link)}">${_escXml(link)}</a>` : ""}
</div>

${verdictSection ? `
<div class="verdict-strip ${vInfo.cls}">
  <div class="verdict-icon">${vInfo.icon}</div>
  <div>
    <div class="verdict-text">${_escXml(verdictLine)}</div>
    ${verdictSummary ? `<div class="verdict-summary">${_escXml(verdictSummary)}</div>` : ""}
  </div>
</div>
` : ""}

<div class="content">
${otherSections.map(s => {
    const isPrice = s.title === "Справедливая цена" || s.title === "Потенциал перепродажи";
    const isFlags = s.title === "Красные флаги";
    const bodyCls = isPrice ? " price-highlight" : "";
    return `<div class="section">
  <div class="section-header">
    <div class="section-dot"${isFlags ? ' style="background:#ef4444;"' : ""}></div>
    <div class="section-title"${isFlags ? ' style="color:#ef4444;"' : ""}>${_escXml(s.title)}</div>
  </div>
  <div class="section-body${bodyCls}">${_escXml(s.body)}</div>
</div>`;
}).join("\n")}
</div>

<div class="footer">
  <p><span class="footer-brand">Rafuks</span> &mdash; ${_escXml(data.disclaimer || "Анализ носит информационный характер. Результаты не являются гарантией.")}</p>
</div>

</div>
<script>
window.addEventListener("load", function () {
  setTimeout(function () {
    try { window.print(); } catch (_) {}
  }, 350);
});
</script>
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
            .replace(/"/g, "&quot;");
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
