/**
 * api_ai.js — orchestrator for the AI listing analysis flow.
 *
 * UX-M8 (Wave 25): this file used to be a 1318-line god-file. It now
 * coordinates three sibling modules and only owns the cross-cutting
 * concerns: the async `loadAIAnalysis` polling loop, the
 * `_showAIError` glue (which touches both modal visuals and render
 * DOM), and the shared `aiCtx` state that the sub-modules read/write.
 *
 *   • api_ai_modal.js   — modal lifecycle + progress UI
 *   • api_ai_render.js  — DOM-build helpers + final result rendering
 *
 * Both lazy files are loaded together by app_actions.js#ensureAiLoaded,
 * so the sub-factories are registered on window.App by the time
 * `createApiAi` runs.
 *
 * Shared state (`aiCtx`):
 *   loading      — guards re-entrancy across `loadAIAnalysis` calls.
 *   pollSession  — bumped by `closeAIModal` so an in-flight poll loop
 *                  sees its session is stale and exits silently.
 */
(function (app) {
"use strict";

function createApiAi(context) {
    const { state, elements, postJson, getJson, logClientError = () => {} } = context;

    // Cross-module shared state. Sub-modules read/write these directly;
    // see each module's header for which fields it touches.
    const aiCtx = {
        loading: false,
        pollSession: 0,
    };

    if (
        typeof app.createAiModal !== "function" ||
        typeof app.createAiRender !== "function"
    ) {
        throw new Error("AI submodules failed to register");
    }
    const modal = app.createAiModal(context, aiCtx);
    const render = app.createAiRender(context, aiCtx);

    async function loadAIAnalysis(adId) {
        const query = (state.detail.data?.query || state.search.activeQuery || "").trim();
        if (!adId || !query) return;
        if (typeof context.canUseAiFeature === "function" && !context.canUseAiFeature("ai")) return;

        const cached = state.detail.ai;
        if (
            cached &&
            cached.adId === adId &&
            cached.result &&
            !cached.error
        ) {
            modal.openAIModal(state.detail.data?.title || "", { startLoading: false });
            render.renderAIModalResult(cached.result);
            return;
        }

        // Check AI consent before proceeding
        if (typeof context.checkAiConsent === "function") {
            try {
                await context.checkAiConsent();
            } catch (_) {
                // User denied consent — don't proceed
                return;
            }
        }

        if (aiCtx.loading) return;
        aiCtx.loading = true;
        // Snapshot the current session so the polling loop below can
        // detect mid-flight cancellation by closeAIModal (which bumps
        // aiCtx.pollSession) and exit silently instead of writing into
        // state.detail.ai or rendering against a hidden modal.
        const pollSession = ++aiCtx.pollSession;
        const isCancelled = () => pollSession !== aiCtx.pollSession;
        state.detail.ai = {
            adId,
            loading: true,
            result: null,
            error: "",
            source: "ai",
        };

        modal.openAIModal(state.detail.data?.title || "");

        const category = state.filters.category || undefined;
        try {
            let result = null;
            // RV-AI-07: closing the modal or running out of patience does
            // not stop the server, which still finishes (and has charged
            // for) the analysis. Re-opening the same listing resumes that
            // task instead of starting — and paying for — a second one.
            let task = _resumableTask(adId, query, category);
            for (;;) {
                if (!task) {
                    // POST starts async analysis, returns task_id immediately
                    const startResp = await postJson("/api/v1/ai/analyze", {
                        ad_id: adId,
                        query,
                        category,
                    });
                    if (isCancelled()) return;

                    // Cached result returned immediately
                    if (startResp.cached && startResp.result) {
                        const cachedResult = startResp.result;
                        state.detail.ai = { adId, loading: false, result: cachedResult, error: "", source: "ai" };
                        modal.stopLoadingAnimation(false);
                        render.renderAIModalResult(cachedResult);
                        return;
                    }
                    if (!startResp.task_id) {
                        throw new Error("Сервер не вернул идентификатор задачи");
                    }
                    task = {
                        adId,
                        query,
                        category,
                        taskId: startResp.task_id,
                        budgetMs: _pollBudgetMs(startResp),
                        settled: false,
                        resumed: false,
                    };
                    aiCtx.lastTask = task;
                }
                try {
                    result = await _pollTask(task, isCancelled);
                    break;
                } catch (err) {
                    // A resumed task can expire server-side (task TTL,
                    // restart): start a fresh one instead of failing.
                    if (err?.taskGone && task.resumed) {
                        task = null;
                        continue;
                    }
                    throw err;
                }
            }

            if (isCancelled() || result == null) return;
            state.detail.ai = { adId, loading: false, result, error: "", source: "ai" };
            modal.showCompletionThen(() => {
                if (isCancelled()) return;
                render.renderAIModalResult(result);
            });
        } catch (err) {
            if (isCancelled()) return;
            logClientError("[AI] Request failed:", err);
            if (typeof context.handleAiAccessError === "function") {
                const handledAccessError = await context.handleAiAccessError(err, "ai");
                if (handledAccessError) {
                    state.detail.ai = { adId, loading: false, result: null, error: "", source: "ai" };
                    modal.closeAIModal();
                    return;
                }
            }
            const message = err.message || "Не удалось выполнить анализ. Проверьте интернет-соединение.";
            state.detail.ai = {
                adId,
                loading: false,
                result: null,
                error: message,
                source: "ai",
            };
            _showAIError(message, adId);
        } finally {
            // Only release the slot if our session is still the active
            // one. Otherwise closeAIModal already cleared aiCtx.loading
            // and a fresh request may have started another session.
            if (!isCancelled()) {
                aiCtx.loading = false;
            }
        }
    }

    // RV-AI-07: how long the client waits for one analysis. The pipeline
    // runs a Kufar search (up to ~2×15 s), an optional photo pre-check
    // (30 s) and the model call bounded by ai_analysis_timeout (150 s,
    // api/config.py) before it answers with a fallback. The old cap of
    // 30 polls (~100 s) gave up first, and the "Повторить" that followed
    // started a second task and spent a second quota point. A backend
    // that reports ``poll_timeout_seconds`` with the task id wins.
    const DEFAULT_POLL_BUDGET_MS = 240_000;
    const POLL_BUDGET_GRACE_MS = 15_000;
    const MAX_POLL_BUDGET_MS = 600_000;
    // Poll for the result every ~3 seconds.
    const POLL_INTERVAL = 3000;
    const POLL_TIMEOUT = 12000;
    const MAX_CONSECUTIVE_POLL_ERRORS = 4;
    const nextPollDelay = () => POLL_INTERVAL + Math.floor(Math.random() * 700);

    function _pollBudgetMs(startResp) {
        const serverSeconds = Number(startResp?.poll_timeout_seconds);
        if (Number.isFinite(serverSeconds) && serverSeconds > 0) {
            return Math.min(serverSeconds * 1000 + POLL_BUDGET_GRACE_MS, MAX_POLL_BUDGET_MS);
        }
        return DEFAULT_POLL_BUDGET_MS;
    }

    function _resumableTask(adId, query, category) {
        const last = aiCtx.lastTask;
        if (!last || last.settled) return null;
        if (last.adId !== adId || last.query !== query || last.category !== category) return null;
        last.resumed = true;
        return last;
    }

    function _pollTask(task, isCancelled) {
        const deadline = Date.now() + task.budgetMs;
        let pollCount = 0;
        let consecutivePollErrors = 0;
        const settle = () => { task.settled = true; };
        return new Promise((resolve, reject) => {
            const poll = async () => {
                if (isCancelled()) {
                    resolve(null);
                    return;
                }
                if (Date.now() > deadline) {
                    // The task stays resumable: "Повторить" keeps waiting
                    // for this very analysis instead of starting another.
                    reject(new Error("Анализ ещё не готов: сервер работает дольше обычного. «Повторить» продолжит ждать этот же анализ."));
                    return;
                }
                pollCount++;
                try {
                    const status = await getJson(`/api/v1/ai/task/${task.taskId}`, { timeout: POLL_TIMEOUT });
                    if (isCancelled()) {
                        resolve(null);
                        return;
                    }
                    consecutivePollErrors = 0;
                    if (status.stage) {
                        modal.setStageLabel(status.stage);
                    }
                    if (status.status === "done") {
                        settle();
                        resolve(status.result);
                    } else if (status.status === "error") {
                        settle();
                        reject(new Error(status.error || "Ошибка AI анализа"));
                    } else {
                        // Still pending/processing — poll again
                        setTimeout(poll, nextPollDelay());
                    }
                } catch (err) {
                    if (isCancelled()) {
                        resolve(null);
                        return;
                    }
                    const message = String(err?.message || "");
                    const isTaskMissing = err?.status === 404 || message.includes("не найдена");
                    consecutivePollErrors += 1;

                    if (isTaskMissing && task.resumed && pollCount === 1) {
                        settle();
                        const gone = new Error("AI task expired");
                        gone.taskGone = true;
                        reject(gone);
                        return;
                    }
                    if (isTaskMissing && pollCount > 5) {
                        settle();
                        reject(new Error("Связь с задачей AI-анализа потеряна. Попробуйте открыть анализ ещё раз."));
                        return;
                    }
                    if (err?.sessionExpired || err?.accountDeleted) {
                        reject(err);
                        return;
                    }

                    if (consecutivePollErrors >= MAX_CONSECUTIVE_POLL_ERRORS) {
                        reject(new Error("Не удалось стабильно получить статус AI-анализа. Проверьте соединение и попробуйте ещё раз."));
                        return;
                    }

                    setTimeout(poll, nextPollDelay());
                }
            };
            setTimeout(poll, task.resumed ? 0 : nextPollDelay());
        });
    }

    /** Hybrid error UI: modal-side loader visuals (ring/icon/bar turn
     * red) + render-side error message + retry button. Lives in the
     * orchestrator because it spans both modules and needs the retry
     * handler bound to the current adId. */
    function _showAIError(message, adId) {
        modal.stopLoadingAnimation(false);
        modal.showLoaderError();
        setTimeout(() => {
            if (elements.aiModalLoading) elements.aiModalLoading.hidden = true;
            if (elements.aiModalResult) elements.aiModalResult.hidden = true;
            if (elements.aiModalError) {
                elements.aiModalError.hidden = false;
                render.renderAiErrorState(
                    elements.aiModalError,
                    message,
                    () => loadAIAnalysis(adId),
                );
            }
        }, 700);
    }

    return {
        loadAIAnalysis,
        closeAIModal: modal.closeAIModal,
    };
}

app.createApiAi = createApiAi;
})(window.App = window.App || {});
