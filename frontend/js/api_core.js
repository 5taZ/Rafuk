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
        const response = await fetch(url, {
            ...options,
            headers,
            signal: options.signal || undefined,
        });

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

    function postJson(url, payload) {
        return requestJson(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    function deleteJson(url) {
        return requestJson(url, { method: "DELETE" });
    }

    // ── Query builder ────────────────────────────────────────────────────
    function buildCommonQuery(params = {}) {
        const query = new URLSearchParams({
            query: state.query,
            currency: state.currency,
            strict_search: String(state.strictSearch),
        });
        if (state.category != null) {
            query.set("category", String(state.category));
        }

        for (const [key, value] of Object.entries(params)) {
            if (value == null || value === "") {
                continue;
            }
            query.set(key, String(value));
        }

        return query.toString();
    }

    // ── Comparison query parser ─────────────────────────────────────────
    function parseComparisonQueries(value) {
        const items = String(value || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
        return Array.from(new Set(items.map((item) => item.toLocaleLowerCase("ru-RU"))))
            .map((key) => items.find((item) => item.toLocaleLowerCase("ru-RU") === key))
            .filter(Boolean)
            .slice(0, 2);
    }

    return {
        telegramHeaders,
        requestJson,
        getJson,
        postJson,
        deleteJson,
        buildCommonQuery,
        parseComparisonQueries,
    };
}
