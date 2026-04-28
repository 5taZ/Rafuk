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
        const headers = {
            ...telegramHeaders(),
            ...(options.headers || {}),
        };

        // Default 90s timeout via AbortController (can be overridden per-request)
        const timeoutMs = options.timeout || 90000;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);

        let response;
        try {
            response = await fetch(url, {
                ...options,
                headers,
                signal: options.signal || controller.signal,
            });
        } catch (fetchErr) {
            clearTimeout(timer);
            if (fetchErr.name === "AbortError") {
                throw new Error("Превышено время ожидания. Попробуйте ещё раз.");
            }
            throw fetchErr;
        } finally {
            clearTimeout(timer);
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
