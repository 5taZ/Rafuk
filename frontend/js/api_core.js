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

        // Default 90s timeout via AbortController (can be overridden per-request)
        const timeoutMs = options.timeout || 90000;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);

        // Link external signal so caller abort also triggers our controller
        if (options.signal) {
            options.signal.addEventListener("abort", () => controller.abort(), { once: true });
        }

        let response;
        let timedOut = false;
        try {
            response = await fetch(url, {
                ...options,
                headers,
                signal: controller.signal,
            });
            // Clear timeout immediately on successful response
            clearTimeout(timer);
        } catch (fetchErr) {
            clearTimeout(timer);
            if (fetchErr.name === "AbortError") {
                if (options.signal?.aborted) {
                    // Caller aborted (e.g. stale request) — suppress silently
                    throw fetchErr;
                }
                throw new Error("Превышено время ожидания. Попробуйте ещё раз.");
            }
            throw fetchErr;
        }

        if (!response.ok) {
            let message = "Не удалось выполнить запрос.";
            try {
                const payload = await response.json();
                if (typeof payload.detail === "string" && payload.detail.trim()) {
                    message = payload.detail.trim();
                }
            } catch (_) {
                if (response.status >= 500) {
                    message = "Сервер временно недоступен. Попробуйте позже.";
                }
            }
            throw new Error(message);
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

    function deleteJson(url) {
        return requestJson(url, { method: "DELETE" });
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
