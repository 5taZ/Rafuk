/**
 * api_listing_assistant.js — AI помощник продавцу.
 *
 * Полностью самодостаточный модуль: открывает модалку, принимает
 * фото / название / цену / описание, сжимает фото на клиенте,
 * вызывает /api/v1/ai/listing-assistant и сохраняет историю запросов
 * в localStorage. Все диалоги пользователя остаются доступными
 * через вкладку «История» внутри модалки.
 */

function createApiListingAssistant(context) {
    const {
        state,
        showToast,
        postJson,
    } = context;

    // ── DOM refs ────────────────────────────────────────────────────────
    const modal = document.getElementById("la-modal");
    const overlay = document.getElementById("la-overlay");
    const closeBtn = document.getElementById("la-modal-close");
    const openBtn = document.getElementById("listing-assistant-open-btn");
    const historyShortcutBtn = document.getElementById("listing-assistant-history-btn");
    const cardCountBadge = document.getElementById("la-history-count");

    const tabsRow = modal?.querySelector(".la-tabs");
    const formPane = document.getElementById("la-pane-form");
    const historyPane = document.getElementById("la-pane-history");
    const tabButtons = modal ? Array.from(modal.querySelectorAll(".la-tab")) : [];
    const tabHistoryCount = document.getElementById("la-tab-history-count");

    const form = document.getElementById("listing-assistant-form");
    const submitBtn = document.getElementById("la-submit-btn");
    const submitLabel = document.getElementById("la-submit-label");
    const titleInput = document.getElementById("la-title-input");
    const priceInput = document.getElementById("la-price-input");
    const conditionSelect = document.getElementById("la-condition-select");
    const negotiableCheckbox = document.getElementById("la-negotiable-checkbox");
    const notesInput = document.getElementById("la-notes-input");
    const notesCounter = document.getElementById("la-notes-counter");
    const photoInput = document.getElementById("la-photo-input");
    const photoGrid = document.getElementById("la-photo-grid");
    const photoAdd = document.getElementById("la-photo-add");
    const resultBox = document.getElementById("listing-assistant-result");
    const historyList = document.getElementById("la-history-list");
    const historyEmpty = document.getElementById("la-history-empty");

    if (!modal || !form || !resultBox) {
        // Modal markup missing — bail quietly so the rest of the app still loads.
        return { destroy() {} };
    }

    // ── Persistence ─────────────────────────────────────────────────────
    // We intentionally don't persist the draft itself — the modal opens
    // fresh every time, with optional re-population via the History tab.
    const HISTORY_KEY = "rafuk:listing-assistant:history";
    const HISTORY_MAX = 20;

    /** @type {{data: string, w: number, h: number}[]} */
    const photos = [];

    function loadHistory() {
        try {
            const raw = localStorage.getItem(HISTORY_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    function saveHistory(list) {
        try {
            const trimmed = list.slice(0, HISTORY_MAX);
            localStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed));
        } catch (_) {
            // Quota exceeded — drop oldest entries until it fits.
            try {
                while (list.length > 1) {
                    list.pop();
                    try {
                        localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
                        break;
                    } catch (_) {}
                }
            } catch (_) {}
        }
    }

    function pushHistoryEntry(entry) {
        const list = loadHistory();
        list.unshift(entry);
        saveHistory(list);
        renderHistoryCounts();
        renderHistoryList();
    }

    function renderHistoryCounts() {
        const count = loadHistory().length;
        if (cardCountBadge) {
            cardCountBadge.textContent = String(count);
            cardCountBadge.hidden = count === 0;
        }
        if (tabHistoryCount) {
            tabHistoryCount.textContent = String(count);
            tabHistoryCount.hidden = count === 0;
        }
    }

    function clearForm() {
        titleInput.value = "";
        priceInput.value = "";
        conditionSelect.value = "";
        negotiableCheckbox.checked = false;
        notesInput.value = "";
        photos.length = 0;
        renderPhotoGrid();
        resultBox.replaceChildren();
        resultBox.hidden = true;
        state.listingAssistantResult = null;
        updateNotesCounter();
    }

    function updateNotesCounter() {
        if (!notesCounter) return;
        const len = (notesInput.value || "").length;
        notesCounter.textContent = `${len} / 1200`;
    }

    // ── Modal open/close ────────────────────────────────────────────────
    function openModal(initialTab = "form") {
        switchTab(initialTab);
        renderHistoryList();
        openModalAnimated(modal);
        if (initialTab === "form") {
            // Defer focus so the keyboard doesn't jump before the modal animates in.
            setTimeout(() => titleInput?.focus({ preventScroll: true }), 320);
        }
    }

    function closeModal() {
        closeModalAnimated(modal);
    }

    function switchTab(name) {
        for (const btn of tabButtons) {
            const isActive = btn.dataset.laTab === name;
            btn.classList.toggle("is-active", isActive);
            btn.setAttribute("aria-selected", String(isActive));
        }
        if (formPane) formPane.hidden = name !== "form";
        if (historyPane) historyPane.hidden = name !== "history";
    }

    // ── Photo handling ──────────────────────────────────────────────────
    const MAX_PHOTOS = 4;
    const MAX_DIMENSION = 1280;
    const JPEG_QUALITY = 0.78;

    /**
     * Resize an image File to JPEG ≤ MAX_DIMENSION on its longest side.
     * Returns a data URL plus the resulting dimensions for layout.
     */
    function compressImage(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
            reader.onload = (e) => {
                const src = e.target?.result;
                if (typeof src !== "string") {
                    reject(new Error("Не удалось прочитать файл"));
                    return;
                }
                const img = new Image();
                img.onload = () => {
                    let { width, height } = img;
                    if (width > MAX_DIMENSION || height > MAX_DIMENSION) {
                        const ratio = Math.min(MAX_DIMENSION / width, MAX_DIMENSION / height);
                        width = Math.round(width * ratio);
                        height = Math.round(height * ratio);
                    }
                    const canvas = document.createElement("canvas");
                    canvas.width = width;
                    canvas.height = height;
                    const ctx = canvas.getContext("2d");
                    if (!ctx) {
                        reject(new Error("Не удалось обработать фото"));
                        return;
                    }
                    ctx.drawImage(img, 0, 0, width, height);
                    try {
                        const data = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
                        resolve({ data, w: width, h: height });
                    } catch (err) {
                        reject(err);
                    }
                };
                img.onerror = () => reject(new Error("Не удалось обработать фото"));
                img.src = src;
            };
            reader.readAsDataURL(file);
        });
    }

    function renderPhotoGrid() {
        if (!photoGrid) return;
        photoGrid.replaceChildren();
        photos.forEach((photo, index) => {
            const cell = document.createElement("div");
            cell.className = "la-photo-cell";

            const img = document.createElement("img");
            img.className = "la-photo-thumb";
            img.alt = `Фото ${index + 1}`;
            img.src = photo.data;
            cell.appendChild(img);

            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "la-photo-remove";
            remove.setAttribute("aria-label", "Удалить фото");
            remove.textContent = "×";
            remove.addEventListener("click", () => {
                photos.splice(index, 1);
                renderPhotoGrid();
            });
            cell.appendChild(remove);

            photoGrid.appendChild(cell);
        });
        if (photoAdd) {
            photoAdd.style.display = photos.length >= MAX_PHOTOS ? "none" : "";
        }
    }

    async function handlePhotoFiles(fileList) {
        if (!fileList || !fileList.length) return;
        const remaining = MAX_PHOTOS - photos.length;
        if (remaining <= 0) {
            showToast(`Максимум ${MAX_PHOTOS} фото`, "info");
            return;
        }
        const files = Array.from(fileList).slice(0, remaining);
        for (const file of files) {
            if (!/^image\//.test(file.type)) {
                showToast("Можно загружать только изображения", "error");
                continue;
            }
            try {
                const compressed = await compressImage(file);
                photos.push(compressed);
            } catch (err) {
                showToast(err?.message || "Не удалось обработать фото", "error");
            }
        }
        renderPhotoGrid();
    }

    // ── Result rendering ────────────────────────────────────────────────

    function el(tag, props = {}, ...children) {
        const node = document.createElement(tag);
        if (props.className) node.className = props.className;
        if (props.text != null) node.textContent = props.text;
        if (props.attrs) {
            for (const [k, v] of Object.entries(props.attrs)) {
                if (v == null) continue;
                node.setAttribute(k, String(v));
            }
        }
        for (const child of children) {
            if (child == null || child === false) continue;
            node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
        }
        return node;
    }

    function copyToClipboard(text, sourceBtn) {
        const fallback = () => {
            const ta = document.createElement("textarea");
            ta.value = text;
            ta.setAttribute("readonly", "");
            ta.style.position = "absolute";
            ta.style.left = "-9999px";
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand("copy"); } catch (_) {}
            document.body.removeChild(ta);
        };
        const finalize = () => {
            showToast("✓ Скопировано", "success", 1400);
            if (sourceBtn) {
                const original = sourceBtn.textContent;
                sourceBtn.textContent = "Скопировано";
                sourceBtn.disabled = true;
                setTimeout(() => {
                    sourceBtn.textContent = original;
                    sourceBtn.disabled = false;
                }, 1400);
            }
        };
        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(text).then(finalize, () => {
                fallback();
                finalize();
            });
        } else {
            fallback();
            finalize();
        }
    }

    function buildCopyBlock(label, value) {
        if (!value || !String(value).trim()) return null;
        const text = String(value).trim();
        const wrap = el("div", { className: "la-copy-block" });
        const header = el(
            "div",
            { className: "la-copy-head" },
            el("span", { className: "la-copy-label", text: label }),
            el("button", {
                className: "la-copy-btn",
                attrs: { type: "button" },
                text: "Копировать",
            }),
        );
        const body = el("div", { className: "la-copy-body" }, document.createTextNode(text));
        wrap.appendChild(header);
        wrap.appendChild(body);
        const btn = header.querySelector(".la-copy-btn");
        btn.addEventListener("click", () => copyToClipboard(text, btn));
        return wrap;
    }

    function buildPriceTier(tier, kind) {
        if (!tier || !tier.price_byn) return null;
        return el(
            "div",
            { className: `la-tier la-tier--${kind}` },
            el("span", { className: "la-tier-kicker", text: tier.label || kind }),
            el(
                "div",
                { className: "la-tier-price" },
                el("strong", { className: "la-tier-price-num mono" },
                    document.createTextNode(`${Math.round(tier.price_byn)}`)),
                el("span", { className: "la-tier-price-unit", text: " BYN" }),
            ),
            tier.weeks_to_sell
                ? el("span", { className: "la-tier-weeks", text: tier.weeks_to_sell })
                : null,
            tier.reasoning
                ? el("p", { className: "la-tier-reason", text: tier.reasoning })
                : null,
        );
    }

    function buildPricingSection(pricing) {
        if (!pricing) return null;
        const tiers = el(
            "div",
            { className: "la-tier-grid" },
            buildPriceTier(pricing.fast, "fast"),
            buildPriceTier(pricing.market, "market"),
            buildPriceTier(pricing.patient, "patient"),
        );

        const meta = [];
        if (pricing.market_median_byn) {
            meta.push(`медиана ${Math.round(pricing.market_median_byn)} BYN`);
        }
        if (pricing.market_q1_byn && pricing.market_q3_byn) {
            meta.push(
                `Q1-Q3: ${Math.round(pricing.market_q1_byn)}-${Math.round(pricing.market_q3_byn)} BYN`
            );
        }
        if (pricing.competing_count) {
            meta.push(`${pricing.competing_count} объявлений`);
        }

        return el(
            "section",
            { className: "la-section" },
            el("h4", { className: "la-section-title", text: "Цена" }),
            tiers,
            pricing.floor_byn
                ? el("p", { className: "la-floor" },
                    el("span", { text: "Минимум, ниже не падать: " }),
                    el("strong", { className: "mono" },
                        document.createTextNode(`${Math.round(pricing.floor_byn)} BYN`)),
                )
                : null,
            meta.length
                ? el("p", { className: "la-section-foot", text: meta.join(" · ") })
                : null,
        );
    }

    function buildSellingPoints(points) {
        if (!Array.isArray(points) || !points.length) return null;
        const ul = el("ul", { className: "la-bullets" });
        for (const point of points) {
            ul.appendChild(el("li", { text: String(point) }));
        }
        return el(
            "section",
            { className: "la-section" },
            el("h4", { className: "la-section-title", text: "Что подсветить покупателю" }),
            ul,
        );
    }

    function buildPlaybook(items) {
        if (!Array.isArray(items) || !items.length) return null;
        const list = el("div", { className: "la-playbook" });
        for (const item of items) {
            list.appendChild(
                el("div", { className: "la-playbook-item" },
                    el("span", { className: "la-playbook-kicker", text: "Если" }),
                    el("p", { className: "la-playbook-scenario", text: item.scenario || "" }),
                    el("span", { className: "la-playbook-kicker", text: "Отвечай" }),
                    el("p", { className: "la-playbook-response", text: item.response || "" }),
                ),
            );
        }
        return el(
            "section",
            { className: "la-section" },
            el("h4", { className: "la-section-title", text: "Как отвечать на торг" }),
            list,
        );
    }

    function buildPhotoTips(tips) {
        if (!Array.isArray(tips) || !tips.length) return null;
        const ul = el("ul", { className: "la-bullets" });
        for (const tip of tips) {
            ul.appendChild(el("li", { text: String(tip) }));
        }
        return el(
            "section",
            { className: "la-section" },
            el("h4", { className: "la-section-title", text: "Фото" }),
            ul,
        );
    }

    function renderError(message) {
        resultBox.replaceChildren();
        resultBox.appendChild(el("div", { className: "la-error", text: message }));
        resultBox.hidden = false;
    }

    function renderLoading() {
        resultBox.replaceChildren();
        const wrap = el(
            "div",
            { className: "la-loading" },
            el("div", { className: "la-loading-spinner", attrs: { "aria-hidden": "true" } }),
            el("p", { className: "la-loading-text", text: "Считаю рынок и собираю текст…" }),
            el("p", { className: "la-loading-sub", text: "10–25 секунд" }),
        );
        resultBox.appendChild(wrap);
        resultBox.hidden = false;
    }

    function renderResult(data) {
        resultBox.replaceChildren();

        const summary = (data.market_summary || "").trim();
        if (summary) {
            resultBox.appendChild(
                el(
                    "section",
                    { className: "la-section la-section--summary" },
                    el("h4", { className: "la-section-title", text: "Контекст рынка" }),
                    el("p", { className: "la-summary", text: summary }),
                ),
            );
        }

        const titleBlock = buildCopyBlock("Заголовок", data.title_suggestion);
        if (titleBlock) {
            resultBox.appendChild(
                el("section", { className: "la-section" },
                    el("h4", { className: "la-section-title", text: "Заголовок" }),
                    titleBlock,
                ),
            );
        }

        const desc = buildCopyBlock("Описание", data.description);
        if (desc) {
            const descSection = el(
                "section",
                { className: "la-section" },
                el("h4", { className: "la-section-title", text: "Описание" }),
                desc,
            );
            const short = (data.description_short || "").trim();
            if (short && short !== (data.description || "").trim()) {
                descSection.appendChild(
                    el("details", { className: "la-short-wrap" },
                        el("summary", { text: "Короткая версия (для сообщений)" }),
                        buildCopyBlock("Короткое", short),
                    ),
                );
            }
            resultBox.appendChild(descSection);
        }

        const sp = buildSellingPoints(data.selling_points);
        if (sp) resultBox.appendChild(sp);

        const pricing = buildPricingSection(data.pricing);
        if (pricing) resultBox.appendChild(pricing);

        const playbook = buildPlaybook(data.negotiation_playbook);
        if (playbook) resultBox.appendChild(playbook);

        const photoTips = buildPhotoTips(data.photo_tips);
        if (photoTips) resultBox.appendChild(photoTips);

        if (data.disclaimer) {
            resultBox.appendChild(
                el("p", { className: "la-disclaimer", text: data.disclaimer }),
            );
        }

        resultBox.hidden = false;
    }

    // ── History list ────────────────────────────────────────────────────

    function fmtDate(iso) {
        if (!iso) return "";
        try {
            const d = new Date(iso);
            return d.toLocaleString("ru-RU", {
                day: "2-digit",
                month: "short",
                hour: "2-digit",
                minute: "2-digit",
            });
        } catch (_) {
            return "";
        }
    }

    function renderHistoryList() {
        if (!historyList) return;
        const entries = loadHistory();
        historyList.replaceChildren();
        if (!entries.length) {
            if (historyEmpty) historyEmpty.hidden = false;
            return;
        }
        if (historyEmpty) historyEmpty.hidden = true;

        for (const entry of entries) {
            const item = el("article", {
                className: "la-history-item",
                attrs: { "data-history-id": String(entry.id) },
            });

            const head = el("header", { className: "la-history-head" },
                el("strong", { className: "la-history-title", text: entry.input?.title || "—" }),
                el("span", { className: "la-history-date", text: fmtDate(entry.ts) }),
            );

            const meta = el("p", { className: "la-history-meta" });
            const bits = [];
            const market = entry.output?.pricing?.market?.price_byn;
            if (market) bits.push(`рынок ${Math.round(market)} BYN`);
            if (entry.input?.draft_price_byn) {
                bits.push(`мой черновик ${Math.round(entry.input.draft_price_byn)} BYN`);
            }
            if (entry.photos_count) bits.push(`${entry.photos_count} фото`);
            meta.textContent = bits.join(" · ") || "без рынка";

            const openBtnEl = el("button", {
                className: "la-history-open",
                attrs: { type: "button" },
                text: "Открыть",
            });
            openBtnEl.addEventListener("click", () => {
                state.listingAssistantResult = entry.output;
                renderResult(entry.output);
                if (entry.input) {
                    titleInput.value = entry.input.title || "";
                    priceInput.value = entry.input.draft_price_byn != null
                        ? String(entry.input.draft_price_byn) : "";
                    conditionSelect.value = entry.input.condition || "";
                    negotiableCheckbox.checked = !!entry.input.is_negotiable;
                    notesInput.value = entry.input.extra_notes || "";
                    updateNotesCounter();
                }
                switchTab("form");
            });

            const deleteBtnEl = el("button", {
                className: "la-history-delete",
                attrs: { type: "button", "aria-label": "Удалить из истории" },
                text: "✕",
            });
            deleteBtnEl.addEventListener("click", () => {
                const list = loadHistory().filter((e) => e.id !== entry.id);
                saveHistory(list);
                renderHistoryCounts();
                renderHistoryList();
            });

            const actions = el("div", { className: "la-history-actions" }, openBtnEl, deleteBtnEl);

            item.appendChild(head);
            item.appendChild(meta);
            item.appendChild(actions);
            historyList.appendChild(item);
        }
    }

    // ── Submit flow ─────────────────────────────────────────────────────

    function setBusy(busy) {
        if (!submitBtn || !submitLabel) return;
        submitBtn.disabled = busy;
        submitBtn.classList.toggle("is-busy", busy);
        submitLabel.textContent = busy ? "Готовлю…" : "Подготовить объявление";
    }

    async function handleSubmit(event) {
        event.preventDefault();
        const title = (titleInput.value || "").trim();
        if (title.length < 3) {
            showToast("Опиши товар хотя бы в 3 символа", "error");
            titleInput.focus();
            return;
        }

        const priceRaw = (priceInput.value || "").trim();
        let draftPriceByn = null;
        if (priceRaw) {
            const parsed = Number(priceRaw);
            if (!Number.isFinite(parsed) || parsed < 0) {
                showToast("Цена должна быть числом", "error");
                priceInput.focus();
                return;
            }
            draftPriceByn = parsed;
        }

        const payload = {
            title,
            condition: conditionSelect.value || null,
            draft_price_byn: draftPriceByn,
            is_negotiable: !!negotiableCheckbox.checked,
            extra_notes: (notesInput.value || "").trim() || null,
            photos: photos.map((p) => p.data),
        };

        setBusy(true);
        renderLoading();

        try {
            const data = await postJson("/api/v1/ai/listing-assistant", payload);
            renderResult(data || {});
            state.listingAssistantResult = data;

            // Save to history (we keep input + count of photos, NOT the photo
            // bytes themselves to keep localStorage under quota).
            pushHistoryEntry({
                id: Date.now(),
                ts: new Date().toISOString(),
                input: {
                    title: payload.title,
                    draft_price_byn: payload.draft_price_byn,
                    condition: payload.condition,
                    is_negotiable: payload.is_negotiable,
                    extra_notes: payload.extra_notes,
                },
                photos_count: photos.length,
                output: data,
            });
        } catch (error) {
            const text = (error && error.message) || "Не удалось получить ответ AI";
            renderError(text);
            showToast(text, "error");
        } finally {
            setBusy(false);
        }
    }

    // ── Wire DOM ───────────────────────────────────────────────────────

    form.addEventListener("submit", handleSubmit);
    notesInput?.addEventListener("input", updateNotesCounter);

    photoInput?.addEventListener("change", (e) => {
        void handlePhotoFiles(e.target.files);
        // Reset the value so picking the same file again still triggers change.
        e.target.value = "";
    });

    if (tabsRow) {
        tabsRow.addEventListener("click", (e) => {
            const target = e.target.closest("[data-la-tab]");
            if (target) switchTab(target.dataset.laTab);
        });
    }

    openBtn?.addEventListener("click", () => {
        // Each "Открыть помощника" click starts with a clean slate so
        // pre-filled values from a previous session don't confuse the user.
        clearForm();
        openModal("form");
    });
    historyShortcutBtn?.addEventListener("click", () => openModal("history"));
    closeBtn?.addEventListener("click", closeModal);
    overlay?.addEventListener("click", closeModal);
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !modal.hidden) {
            closeModal();
        }
    });

    // Initial state: just refresh history badges. Form is always blank
    // until the user types or restores from history.
    renderHistoryCounts();
    updateNotesCounter();

    return {
        destroy() {
            form.removeEventListener("submit", handleSubmit);
        },
    };
}
