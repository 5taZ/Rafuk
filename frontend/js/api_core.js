/**
 * api_core.js — Shared HTTP primitives, currency helpers, and rate loading.
 *
 * Provides the foundational request wrappers that every other API module builds on.
 */

function createApiCore(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        renderLoading,
        renderError,
        renderStrictSearch,
        renderDealInputs,
        renderTrackerInputs,
        setActiveView,
        clearSearchData,
        search,
    } = context;

    // OPUS-3: 429 is intentionally NOT retryable. The backend's AI
    // rate-limiter increments BEFORE checking the cap, so retrying
    // a 429 quietly drains another quota point on every attempt
    // even though the response was already a refusal. 502/503/504
    // are still retryable — those mean the request didn't run.
    const RETRYABLE_STATUSES = new Set([502, 503, 504]);
    const RETRY_BASE_DELAY_MS = 300;
    const RETRY_MAX_ATTEMPTS = 3;

    function _retryDelayMs(attempt, response) {
        const retryAfter = response?.headers?.get?.("retry-after");
        if (retryAfter) {
            const seconds = Number(retryAfter);
            if (Number.isFinite(seconds) && seconds >= 0) {
                return Math.min(5000, seconds * 1000);
            }
        }
        return Math.min(4000, RETRY_BASE_DELAY_MS * (2 ** (attempt - 1)))
            + Math.floor(Math.random() * 180);
    }

    function _sleep(ms, signal) {
        return new Promise((resolve, reject) => {
            if (signal?.aborted) {
                reject(new DOMException("Aborted", "AbortError"));
                return;
            }
            const timer = setTimeout(resolve, ms);
            signal?.addEventListener("abort", () => {
                clearTimeout(timer);
                reject(new DOMException("Aborted", "AbortError"));
            }, { once: true });
        });
    }

    function _friendlyErrorMessage(message) {
        if (/Lead version is required for updates|Lead was updated elsewhere/i.test(message)) {
            return "Данные устарели. Обновите список и попробуйте ещё раз.";
        }
        return message;
    }

    function _validationErrorMessage(detail) {
        if (!Array.isArray(detail)) return "";
        return detail.slice(0, 4).map((item) => {
            const loc = Array.isArray(item?.loc)
                ? item.loc.filter((part) => !["body", "query", "path"].includes(String(part))).join(".")
                : "";
            const msg = String(item?.msg || item?.message || "").trim();
            return loc && msg ? `${loc}: ${msg}` : (msg || loc);
        }).filter(Boolean).join("; ");
    }

    // ── Telegram headers ─────────────────────────────────────────────────
    function telegramHeaders() {
        const initData = window.Telegram?.WebApp?.initData;
        return initData ? { "X-Telegram-Init-Data": initData } : {};
    }

    // ── Generic request wrapper ──────────────────────────────────────────
    async function requestJson(url, options = {}) {
        const method = (options.method || "GET").toUpperCase();
        // FE-H7: X-Requested-With header on every state-changing call.
        // HTML forms and <img>/<link> tags can't set custom headers, so
        // requiring "XMLHttpRequest" here means a CSRF attacker has to
        // also beat the browser's CORS preflight — on top of the
        // existing Origin check on the server. It costs nothing for
        // legitimate traffic (we already send X-Telegram-Init-Data)
        // and gives us one more layer of defence for the endpoints
        // that mutate state.
        const isStateChanging = method !== "GET" && method !== "HEAD";
        const headers = {
            ...telegramHeaders(),
            ...(isStateChanging ? { "X-Requested-With": "XMLHttpRequest" } : {}),
            ...(options.headers || {}),
        };

        const canRetry = method === "GET" || method === "HEAD";
        const maxAttempts = canRetry && options.retry !== false
            ? (options.retryAttempts || RETRY_MAX_ATTEMPTS)
            : 1;
        const timeoutMs = options.timeout || 90000;

        let timedOut = false;
        let response;
        for (let attempt = 1; attempt <= maxAttempts; attempt++) {
            const controller = new AbortController();
            const timer = setTimeout(() => {
                timedOut = true;
                controller.abort();
            }, timeoutMs);
            const onExternalAbort = () => controller.abort();
            if (options.signal) {
                options.signal.addEventListener("abort", onExternalAbort, { once: true });
            }
            try {
                response = await fetch(url, {
                    ...options,
                    headers,
                    signal: controller.signal,
                });
                if (
                    attempt < maxAttempts
                    && RETRYABLE_STATUSES.has(response.status)
                ) {
                    await _sleep(_retryDelayMs(attempt, response), options.signal);
                    continue;
                }
                break;
            } catch (fetchErr) {
                if (fetchErr.name === "AbortError") {
                    if (options.signal?.aborted) throw fetchErr;
                    throw new Error("Превышено время ожидания. Попробуйте ещё раз.");
                }
                if (attempt >= maxAttempts) throw fetchErr;
                await _sleep(_retryDelayMs(attempt), options.signal);
            } finally {
                clearTimeout(timer);
                if (options.signal) {
                    options.signal.removeEventListener("abort", onExternalAbort);
                }
            }
        }

        if (!response || !response.ok) {
            let message = "Не удалось выполнить запрос.";
            let detailPayload = null;
            try {
                const payload = await response.json();
                if (Array.isArray(payload.detail)) {
                    const validationMessage = _validationErrorMessage(payload.detail);
                    if (validationMessage) {
                        message = validationMessage;
                        detailPayload = { errors: payload.detail };
                    }
                } else if (typeof payload.detail === "string" && payload.detail.trim()) {
                    message = _friendlyErrorMessage(payload.detail.trim());
                } else if (payload.detail && typeof payload.detail === "object") {
                    detailPayload = payload.detail;
                    if (typeof payload.detail.message === "string" && payload.detail.message.trim()) {
                        message = payload.detail.message.trim();
                    }
                }
            } catch (_) {
                if (response.status >= 500) {
                    message = "Сервер временно недоступен. Попробуйте позже.";
                }
            }
            const err = new Error(message);
            err.status = response?.status || 0;
            if (detailPayload) {
                err.detail = detailPayload;
                err.errorCode = detailPayload.error || "";
                err.bucket = detailPayload.bucket || "";
            }
            throw err;
        }

        if (response.status === 204) {
            return null;
        }

        return response.json();
    }

    // ── Convenience wrappers ─────────────────────────────────────────────
    function getJson(url, options) {
        return requestJson(url, options);
    }

    function postJson(url, payload, extraOptions) {
        return requestJson(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            ...(extraOptions || {}),
        });
    }

    function deleteJson(url, payload) {
        // BE-M3: DELETE may carry a JSON body (e.g. account-deletion
        // confirmation). When ``payload`` is omitted we keep the
        // historical no-body behaviour so existing callers don't need
        // to change.
        const opts = { method: "DELETE" };
        if (payload !== undefined) {
            opts.headers = { "Content-Type": "application/json" };
            opts.body = JSON.stringify(payload);
        }
        return requestJson(url, opts);
    }

    // ── Query builder ────────────────────────────────────────────────────
    function buildCommonQuery(params = {}) {
        const query = new URLSearchParams({
            query: state.search.query,
            currency: state.misc.currency,
            strict_search: String(state.search.strictSearch),
        });
        if (state.filters.category != null) {
            query.set("category", String(state.filters.category));
        }

        for (const [key, value] of Object.entries(params)) {
            if (value == null || value === "") {
                continue;
            }
            query.set(key, String(value));
        }

        return query.toString();
    }

    return {
        telegramHeaders,
        requestJson,
        getJson,
        postJson,
        deleteJson,
        buildCommonQuery,
    };
}
