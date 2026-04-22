/**
 * api_ai.js — AI listing analysis with dedicated modal.
 */
function createApiAi(context) {
    const { state, elements, postJson, escapeHtml, safeUrl, formatPrice, telegramHeaders } = context;

    let _aiLoading = false;
    let _aiProgress = 0;

    const LOADING_STEPS = [
        "Загружаю данные объявления...",
        "Анализирую фотографии...",
        "Сравниваю с рынком...",
        "Подбираю альтернативы...",
        "Формирую рекомендации...",
        "Осталось немного...",
    ];

    function _updateProgressDisplay(pct) {
        _aiProgress = pct;
        const barEl = elements.aiProgressBar;
        const pctEl = elements.aiProgressPct;
        if (barEl) barEl.style.width = pct + "%";
        if (pctEl) pctEl.textContent = Math.round(pct) + "%";
    }

    function _startLoadingAnimation() {
        let step = 0;
        _aiProgress = 0;
        const textEl = elements.aiLoaderText;

        function tick() {
            step = (step + 1) % LOADING_STEPS.length;
            if (textEl) textEl.textContent = LOADING_STEPS[step];
            _aiProgress = Math.min(_aiProgress + 8 + Math.random() * 5, 92);
            _updateProgressDisplay(_aiProgress);
        }

        if (textEl) textEl.textContent = LOADING_STEPS[0];
        _updateProgressDisplay(5);
        state.aiLoadingTimer = setInterval(tick, 2800);
    }

    function _stopLoadingAnimation(success = true) {
        if (state.aiLoadingTimer) {
            clearInterval(state.aiLoadingTimer);
            state.aiLoadingTimer = null;
        }
        if (success) {
            _updateProgressDisplay(100);
            const pctEl = elements.aiProgressPct;
            if (pctEl) pctEl.classList.add("ai-progress-pct--done");
            const barEl = elements.aiProgressBar;
            if (barEl) barEl.classList.add("ai-progress-bar--done");
        }
    }

    function _showCompletionThen(callback) {
        _stopLoadingAnimation();

        const loadingEl = elements.aiModalLoading;
        if (!loadingEl) { callback(); return; }

        const ring = loadingEl.querySelector(".ai-loader-ring");
        const icon = loadingEl.querySelector(".ai-loader-icon");
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
                    elements.aiModalResult.innerHTML = `<div class="ai-error">Ошибка отображения результата</div>`;
                }
            }
        }, 500);
    }

    function openAIModal(subtitle) {
        if (elements.aiModalSubtitle && subtitle) {
            elements.aiModalSubtitle.textContent = subtitle;
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
        const pctEl = elements.aiProgressPct;
        if (pctEl) {
            pctEl.classList.remove("ai-progress-pct--error");
            pctEl.classList.remove("ai-progress-pct--done");
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

        // If already analyzed for this ad, show cached result immediately
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
            const result = await postJson("/api/v1/ai/analyze", {
                ad_id: adId,
                query,
            }, { timeout: 270000 });
            console.log("[AI] Response received:", result ? "ok" : "null", result ? Object.keys(result).join(",") : "");
            state.detailAi = {
                adId,
                loading: false,
                result,
                error: "",
                source: "ai",
            };
            _showCompletionThen(() => _renderAIModalResult(result));
        } catch (err) {
            console.error("[AI] Request failed:", err);
            const message = `Не удалось выполнить анализ${err.message ? `: ${err.message}` : ""}`;
            state.detailAi = {
                adId,
                loading: false,
                result: null,
                error: message,
                source: "ai",
            };
            _showAIError(message);
        } finally {
            _aiLoading = false;
        }
    }

    function _showAIError(message) {
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
        // Show percentage and bar in red on error
        const pctEl = elements.aiProgressPct;
        if (pctEl) pctEl.classList.add("ai-progress-pct--error");
        const barEl = elements.aiProgressBar;
        if (barEl) barEl.classList.add("ai-progress-bar--error");

        setTimeout(() => {
            if (elements.aiModalLoading) elements.aiModalLoading.hidden = true;
            if (elements.aiModalResult) elements.aiModalResult.hidden = true;
            if (elements.aiModalError) {
                elements.aiModalError.hidden = false;
                elements.aiModalError.innerHTML = `<div class="ai-error">${escapeHtml(message)}</div>`;
            }
        }, 700);
    }

    function _renderAIModalResult(data) {
        console.log("[AI] Rendering result, data keys:", data ? Object.keys(data).join(",") : "null");
        if (elements.aiModalLoading) elements.aiModalLoading.hidden = true;
        if (elements.aiModalError) elements.aiModalError.hidden = true;

        const container = elements.aiModalResult;
        if (!container) return;
        container.hidden = false;

        let html = "";

        // ── Summary + verdict ──
        if (data.recommendation) {
            const rec = data.recommendation;
            const verdictClass = rec.verdict === "worth_it" ? "ai-badge--good"
                : rec.verdict === "think_twice" ? "ai-badge--warn" : "ai-badge--bad";
            const verdictText = rec.verdict === "worth_it" ? "Стоит брать"
                : rec.verdict === "think_twice" ? "Подумай" : "Дорого";
            html += `<div class="ai-modal-verdict">
                <span class="ai-badge ${verdictClass} ai-badge--lg">${verdictText}</span>
                ${data.summary ? `<p class="ai-modal-summary">${escapeHtml(data.summary)}</p>` : ""}
            </div>`;
        }

        // ── Condition assessment ──
        if (data.condition) {
            const cond = data.condition;
            const badgeClass = cond.label === "Отличное" ? "ai-badge--good" :
                               cond.label === "Хорошее" ? "ai-badge--ok" :
                               cond.label === "Удовлетворительное" ? "ai-badge--warn" : "ai-badge--bad";
            html += `<div class="ai-section">
                <span class="ai-label">Состояние по фото</span>
                <span class="ai-badge ${badgeClass}">${escapeHtml(cond.label)}</span>
                ${cond.confidence ? `<span class="ai-confidence">уверенность ${Math.round(cond.confidence * 100)}%</span>` : ""}
                ${cond.notes?.length ? `<ul class="ai-notes">${cond.notes.map(n => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}
            </div>`;
        }

        // ── Fair price ──
        if (data.fair_price) {
            const fp = data.fair_price;
            const fromPrice = fp.from || fp.from_price;
            const toPrice = fp.to || fp.to_price;
            html += `<div class="ai-section">
                <span class="ai-label">Справедливая цена</span>
                ${fromPrice != null && toPrice != null
                    ? `<div class="ai-fair-price">${Math.round(fromPrice)} — ${Math.round(toPrice)} BYN</div>`
                    : fromPrice != null
                        ? `<div class="ai-fair-price">~${Math.round(fromPrice)} BYN</div>`
                        : ""}
                ${fp.reasoning ? `<p class="ai-reasoning">${escapeHtml(fp.reasoning)}</p>` : ""}
            </div>`;
        }

        // ── Market context ──
        if (data.market_context) {
            html += `<div class="ai-section">
                <span class="ai-label">Контекст рынка</span>
                <p class="ai-reasoning">${escapeHtml(data.market_context)}</p>
            </div>`;
        }

        // ── Best alternative ──
        if (data.best_alternative) {
            const ba = data.best_alternative;
            html += `<div class="ai-section ai-section--best">
                <span class="ai-label">Лучший вариант</span>
                <a class="ai-best-link" href="${safeUrl(ba.link)}" target="_blank" rel="noreferrer noopener">
                    ${ba.image_url ? `<img class="ai-best-thumb" src="${safeUrl(ba.image_url)}" alt="" loading="lazy">` : ""}
                    <div class="ai-best-info">
                        <span class="ai-best-title">${escapeHtml(ba.title)}</span>
                        <span class="ai-best-price mono">${Math.round(ba.price_byn)} BYN</span>
                        ${ba.condition ? `<span class="ai-best-condition">${escapeHtml(ba.condition)}</span>` : ""}
                    </div>
                    <span class="ai-best-arrow">→</span>
                </a>
                ${data.best_pick_reason ? `<p class="ai-best-reason">${escapeHtml(data.best_pick_reason)}</p>` : ""}
            </div>`;
        }

        // ── Other alternatives ──
        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter(s => s.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                html += `<div class="ai-section">
                    <span class="ai-label">Другие варианты (${others.length})</span>
                    <div class="ai-similar">${others.map(s =>
                        `<a class="ai-similar-item" href="${safeUrl(s.link)}" target="_blank" rel="noreferrer noopener">
                            ${s.image_url ? `<img class="ai-similar-thumb" src="${safeUrl(s.image_url)}" alt="" loading="lazy">` : ""}
                            <div class="ai-similar-info">
                                <span class="ai-similar-title">${escapeHtml(s.title)}</span>
                                <span class="ai-similar-price mono">${Math.round(s.price_byn)} BYN${s.condition ? ` · ${escapeHtml(s.condition)}` : ""}</span>
                            </div>
                        </a>`
                    ).join("")}</div>
                </div>`;
            }
        }

        // ── Watch out ──
        if (data.watch_out?.length) {
            html += `<div class="ai-section">
                <span class="ai-label">На что обратить внимание</span>
                <div class="ai-watch-list">${data.watch_out.map(w =>
                    `<div class="ai-watch-item">
                        <strong>${escapeHtml(w.point)}</strong>
                        <span>${escapeHtml(w.why)}</span>
                    </div>`
                ).join("")}</div>
            </div>`;
        }

        // ── Meeting checklist ──
        if (data.meeting_checklist?.length) {
            html += `<div class="ai-section">
                <span class="ai-label">Чек-лист для встречи</span>
                <ol class="ai-checklist">${data.meeting_checklist.map(item =>
                    `<li>${escapeHtml(item)}</li>`
                ).join("")}</ol>
            </div>`;
        }

        // ── Negotiation tips ──
        if (data.negotiation_tips?.length) {
            html += `<div class="ai-section">
                <span class="ai-label">Как торговаться</span>
                <ul class="ai-tips">${data.negotiation_tips.map(tip =>
                    `<li>${escapeHtml(tip)}</li>`
                ).join("")}</ul>
            </div>`;
        }

        // ── Red flags ──
        if (data.red_flags?.length) {
            html += `<div class="ai-section">
                <span class="ai-label">Красные флаги</span>
                <div class="ai-flags">${data.red_flags.map(flag =>
                    `<div class="ai-flag-item">${escapeHtml(flag)}</div>`
                ).join("")}</div>
            </div>`;
        }

        // ── Recommendation text ──
        if (data.recommendation?.text) {
            html += `<div class="ai-section ai-section--recommendation">
                <span class="ai-label">Рекомендация</span>
                <p class="ai-recommendation-text">${escapeHtml(data.recommendation.text)}</p>
            </div>`;
        }

        // ── Disclaimer ──
        html += `<p class="ai-disclaimer">${escapeHtml(data.disclaimer || "Анализ носит информационный характер. Результаты не являются гарантией.")}</p>`;

        container.innerHTML = html;
    }

    return {
        loadAIAnalysis,
        closeAIModal,
    };
}
