/**
 * api_ai_modal.js — modal lifecycle + progress UI for the AI analysis flow.
 *
 * UX-M8 (Wave 25): extracted from api_ai.js so the orchestrator stops being
 * a 1318-line god-file. The split is by responsibility:
 *   • modal (this file) — open/close, loader ring + progress bar, stage labels
 *   • render            — DOM-build helpers + final result/error rendering
 * The orchestrator (api_ai.js) wires them together with a tiny shared
 * state object (`aiCtx`) carrying only the cross-module mutable bits
 * (`loading`, `pollSession`). Everything else is module-local.
 */
(function (app) {
"use strict";

const { domEl, openModalAnimated, closeModalAnimated } = app;
const prefersReducedMotion = app._prefersReducedMotion || (() => (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
));

function createAiModal(context, aiCtx) {
    const { elements, logClientError = () => {} } = context;

    // Modal-local mutable state. Only the modal touches these, so they
    // stay encapsulated rather than leaking into aiCtx.
    let _aiProgress = 0;
    let _progressFrame = null;
    let _progressStartedAt = 0;

    const LOADING_STEPS = [
        "Проверяю данные объявления...",
        "Смотрю фото и состояние...",
        "Сверяю похожие лоты...",
        "Оцениваю риски сделки...",
        "Готовлю рекомендацию...",
        "Финализирую вывод...",
    ];
    const LOADING_OVERTIME_STEPS = [
        "Уточняю финальные детали...",
        "Проверяю рекомендацию перед показом...",
        "Ответ почти готов...",
    ];
    const AI_PROGRESS_EXPECTED_MS = 26000;
    const AI_PROGRESS_SOFT_CAP = 94;
    const AI_PROGRESS_GLIDE_CAP = 98.6;
    const AI_PROGRESS_GLIDE_MS = 42000;
    const STAGE_LABELS = {
        queued: "Ставлю анализ в очередь...",
        loading_market_data: "Проверяю данные объявления...",
        search_ready: "Сверяю цену с похожими лотами...",
        photo_precheck: "Смотрю фото и состояние...",
        calling_ai: "Оцениваю цену, риски и торг...",
        building_response: "Готовлю рекомендацию...",
        done: "Анализ завершён!",
        timeout: "AI анализ занял слишком долго...",
        error: "Не удалось завершить AI анализ...",
        target_missing: "Объявление не найдено...",
    };

    function _updateProgressDisplay(pct) {
        _aiProgress = Math.max(0, Math.min(100, pct));
        const barEl = elements.aiProgressBar;
        const pctEl = elements.aiProgressPct;
        if (barEl) barEl.style.transform = `scaleX(${_aiProgress / 100})`;
        if (pctEl) {
            pctEl.textContent = _aiProgress >= 99.5
                ? "100%"
                : (_aiProgress >= AI_PROGRESS_SOFT_CAP ? "почти готово" : Math.round(_aiProgress) + "%");
        }
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
        requestAnimationFrame(() => {
            barEl.style.transition = previousTransition;
        });
    }

    function setStageLabel(stage) {
        const textEl = elements.aiLoaderText;
        if (!textEl || !stage || !STAGE_LABELS[stage]) return;
        textEl.textContent = STAGE_LABELS[stage];
    }

    function _cancelProgressFrame() {
        if (_progressFrame) {
            cancelAnimationFrame(_progressFrame);
            _progressFrame = null;
        }
    }

    function startLoadingAnimation() {
        let step = 0;
        let overtimeStep = -1;
        _aiProgress = 0;
        _progressStartedAt = performance.now();
        const textEl = elements.aiLoaderText;

        if (prefersReducedMotion()) {
            if (textEl) textEl.textContent = LOADING_STEPS[0];
            _updateProgressDisplay(12);
            return;
        }

        function tick(now) {
            const elapsed = Math.max(0, now - _progressStartedAt);
            const normalized = Math.min(elapsed / AI_PROGRESS_EXPECTED_MS, 1);
            const overtime = Math.max(0, elapsed - AI_PROGRESS_EXPECTED_MS);
            const overtimeNormalized = Math.min(overtime / AI_PROGRESS_GLIDE_MS, 1);
            const target = 3
                + normalized * (AI_PROGRESS_SOFT_CAP - 3)
                + overtimeNormalized * (AI_PROGRESS_GLIDE_CAP - AI_PROGRESS_SOFT_CAP);
            _updateProgressDisplay(Math.max(_aiProgress, target));

            if (overtime > 1200) {
                const nextOvertimeStep = Math.floor(overtime / 6500) % LOADING_OVERTIME_STEPS.length;
                if (nextOvertimeStep !== overtimeStep) {
                    overtimeStep = nextOvertimeStep;
                    if (textEl) textEl.textContent = LOADING_OVERTIME_STEPS[overtimeStep];
                }
            } else {
                const nextStep = Math.min(
                    LOADING_STEPS.length - 1,
                    Math.floor(normalized * LOADING_STEPS.length),
                );
                if (nextStep !== step) {
                    step = nextStep;
                    if (textEl) textEl.textContent = LOADING_STEPS[step];
                }
            }

            _progressFrame = requestAnimationFrame(tick);
        }

        if (textEl) textEl.textContent = LOADING_STEPS[0];
        _updateProgressDisplay(3);
        _cancelProgressFrame();
        _progressFrame = requestAnimationFrame(tick);
    }

    function stopLoadingAnimation(success = true) {
        _cancelProgressFrame();
        if (success) {
            // Smooth transition from current progress to 100%
            const startPct = _aiProgress;
            const targetPct = 100;
            if (prefersReducedMotion() || startPct >= targetPct - 1) {
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

    function showCompletionThen(callback) {
        stopLoadingAnimation();

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
                logClientError("AI render error:", e);
                if (elements.aiModalResult) {
                    elements.aiModalResult.hidden = false;
                    elements.aiModalResult.replaceChildren(
                        domEl("div", { className: "ai-error", text: "Ошибка отображения результата" })
                    );
                }
            }
        }, prefersReducedMotion() ? 150 : 950);
    }

    /** Apply error styling to the loader ring/icon — used by the orchestrator
     * when a request fails. The actual error message+retry button live in
     * the render module so this just handles the loader-area visuals. */
    function showLoaderError() {
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
    }

    function openAIModal(subtitle, options = {}) {
        const { startLoading = true } = options;
        if (elements.aiModalSubtitle && subtitle) {
            elements.aiModalSubtitle.textContent = subtitle;
        }

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
        if (elements.aiModal) openModalAnimated(elements.aiModal);

        // Scroll to top
        const scrollBody = elements.aiModal?.querySelector(".ai-modal-body");
        if (scrollBody) scrollBody.scrollTop = 0;

        if (startLoading) {
            startLoadingAnimation();
        } else if (loadingEl) {
            loadingEl.hidden = true;
        }
    }

    function closeAIModal() {
        // Cancel any in-flight polling loop and free the loading slot
        // so the user can re-open AI Analysis (for the same ad or a
        // different one) without waiting for the old poll to time out.
        // The shared aiCtx is the cross-module signalling channel —
        // both this module and the orchestrator's loadAIAnalysis
        // observe its `pollSession` and `loading` fields.
        aiCtx.pollSession += 1;
        aiCtx.loading = false;
        stopLoadingAnimation(false);
        if (elements.aiModal) closeModalAnimated(elements.aiModal);
    }

    return {
        openAIModal,
        closeAIModal,
        startLoadingAnimation,
        stopLoadingAnimation,
        showCompletionThen,
        showLoaderError,
        setStageLabel,
    };
}

app.createAiModal = createAiModal;
})(window.App = window.App || {});
