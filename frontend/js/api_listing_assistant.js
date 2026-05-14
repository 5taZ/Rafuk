/**
 * api_listing_assistant.js — AI помощник продавцу.
 *
 * Полностью самодостаточный модуль: открывает модалку, принимает
 * фото / название / цену / описание, сжимает фото на клиенте,
 * вызывает /api/v1/ai/listing-assistant и сохраняет историю запросов
 * в localStorage. Все диалоги пользователя остаются доступными
 * через вкладку «История» внутри модалки.
 */
(function (app) {
"use strict";

const { openModalAnimated, closeModalAnimated } = app;
const bindRovingTablist = app.bindRovingTablist || (() => () => {});
const prefersReducedMotion = app._prefersReducedMotion || (() => (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
));


function createApiListingAssistant(context) {
    const {
        state,
        showToast,
        postJson,
        safeKufarUrl,
        safeImageUrl,
        checkAiConsent,
        logClientError = () => {},
    } = context;

    // ── DOM refs ────────────────────────────────────────────────────────
    const modal = document.getElementById("la-modal");
    const overlay = document.getElementById("la-overlay");
    const closeBtn = document.getElementById("la-modal-close");
    const openBtn = document.getElementById("listing-assistant-open-btn");

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
    const resultOverlay = document.getElementById("la-result-overlay");
    const resultBackBtn = document.getElementById("la-result-back");
    const historyList = document.getElementById("la-history-list");
    const historyEmpty = document.getElementById("la-history-empty");
    const historyClearBtn = document.getElementById("la-history-clear");
    const saveHistoryCheckbox = document.getElementById("la-save-history-checkbox");

    if (!modal || !form || !resultBox) {
        return { destroy() {} };
    }

    let _resultSource = "form";
    let _analysisId = 0;

    // ── Persistence ─────────────────────────────────────────────────────
    // We intentionally don't persist the draft itself — the modal opens
    // fresh every time, with optional re-population via the History tab.
    const HISTORY_KEY = "rafuk:listing-assistant:history";
    const HISTORY_SAVE_KEY = "rafuk:listing-assistant:save-history";
    const HISTORY_MAX = 20;
    const HISTORY_TTL_MS = 30 * 24 * 60 * 60 * 1000;

    /** @type {{data: string, w: number, h: number}[]} */
    const photos = [];

    function loadHistory() {
        try {
            const raw = localStorage.getItem(HISTORY_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) return [];
            const entries = _prepareHistoryForStorage(parsed);
            if (entries.length !== parsed.length) {
                localStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
            }
            return entries;
        } catch (_) {
            return [];
        }
    }

    function isHistorySavingEnabled() {
        return !!saveHistoryCheckbox && saveHistoryCheckbox.checked;
    }

    function initHistoryPreference() {
        if (!saveHistoryCheckbox) return;
        try {
            saveHistoryCheckbox.checked = localStorage.getItem(HISTORY_SAVE_KEY) === "1";
        } catch (_) {
            saveHistoryCheckbox.checked = false;
        }
    }

    function saveHistoryPreference() {
        if (!saveHistoryCheckbox) return;
        try {
            localStorage.setItem(HISTORY_SAVE_KEY, saveHistoryCheckbox.checked ? "1" : "0");
        } catch (_) {}
    }

    function isHistoryEntryFresh(entry, now = Date.now()) {
        const ts = Date.parse(entry?.ts || "");
        return Number.isFinite(ts) && now - ts <= HISTORY_TTL_MS;
    }

    function _trimEntryForStorage(entry) {
        const out = { ...entry };
        if (out.output) {
            const o = { ...out.output };
            if (o.description && o.description.length > 500) {
                o.description = o.description.slice(0, 500);
            }
            if (o.market_summary && o.market_summary.length > 300) {
                o.market_summary = o.market_summary.slice(0, 300);
            }
            out.output = o;
        }
        return out;
    }

    function _prepareHistoryForStorage(list) {
        const now = Date.now();
        return list
            .filter((entry) => isHistoryEntryFresh(entry, now))
            .slice(0, HISTORY_MAX)
            .map(_trimEntryForStorage);
    }

    function saveHistory(list) {
        try {
            const trimmed = _prepareHistoryForStorage(list);
            localStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed));
        } catch (_) {
            try {
                const fallback = _prepareHistoryForStorage(list);
                while (fallback.length > 1) {
                    fallback.pop();
                    try {
                        localStorage.setItem(HISTORY_KEY, JSON.stringify(fallback));
                        break;
                    } catch (_) {}
                }
            } catch (_) {}
        }
    }

    function pushHistoryEntry(entry) {
        if (!isHistorySavingEnabled()) return;
        const list = loadHistory();
        list.unshift(entry);
        saveHistory(list);
        renderHistoryCounts();
        renderHistoryList();
    }

    function renderHistoryCounts() {
        const count = loadHistory().length;
        if (tabHistoryCount) {
            tabHistoryCount.textContent = String(count);
            tabHistoryCount.hidden = count === 0;
        }
        if (historyClearBtn) historyClearBtn.disabled = count === 0;
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
        hideResultOverlay();
        state.misc.listingAssistantResult = null;
        updateNotesCounter();
    }

    function updateNotesCounter() {
        if (!notesCounter) return;
        const len = (notesInput.value || "").length;
        notesCounter.textContent = `${len} / 1200`;
    }

    // ── Modal open/close ────────────────────────────────────────────────
    function openModal(initialTab = "form") {
        _analysisId++;
        switchTab(initialTab);
        renderHistoryList();
        openModalAnimated(modal);
        if (initialTab === "form") {
            // Defer focus so the keyboard doesn't jump before the modal animates in.
            setTimeout(() => titleInput?.focus({ preventScroll: true }), 320);
        }
    }

    function closeModal() {
        _analysisId++;
        _cancelLaProgress();
        closeModalAnimated(modal);
    }

    function switchTab(name) {
        for (const btn of tabButtons) {
            const isActive = btn.dataset.laTab === name;
            btn.classList.toggle("is-active", isActive);
            btn.setAttribute("aria-selected", String(isActive));
            btn.tabIndex = isActive ? 0 : -1;
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
            showToast("Скопировано", "success", 1400);
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
        const row = el("div", { className: `la-tier la-tier--${kind}` });
        row.appendChild(el("span", { className: "la-tier-kicker", text: tier.label || kind }));
        const priceWrap = el("div", { className: "la-tier-price" });
        priceWrap.appendChild(el("span", { className: "la-tier-price-num mono" },
            document.createTextNode(`${Math.round(tier.price_byn)}`)));
        priceWrap.appendChild(el("span", { className: "la-tier-price-unit", text: " BYN" }));
        row.appendChild(priceWrap);
        if (tier.weeks_to_sell) {
            row.appendChild(el("span", { className: "la-tier-weeks", text: tier.weeks_to_sell }));
        }
        // Reasoning goes on a separate line below the row
        if (tier.reasoning) {
            const reasonLine = el("div", { className: "la-tier-reason-line" });
            reasonLine.appendChild(el("span", { className: "la-tier-reason", text: tier.reasoning }));
            // Wrap tier + reason together
            const wrap = el("div");
            wrap.appendChild(row);
            wrap.appendChild(reasonLine);
            return wrap;
        }
        return row;
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
            el("span", { className: "la-section-title", text: "Цена" }),
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
            el("span", { className: "la-section-title", text: "Что подсветить покупателю" }),
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
            el("span", { className: "la-section-title", text: "Как отвечать на торг" }),
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
            el("span", { className: "la-section-title", text: "Советы по фото" }),
            ul,
        );
    }

    function buildCompetitors(compList) {
        if (!Array.isArray(compList) || !compList.length) return null;
        const list = el("div", { className: "la-competitors" });
        for (const comp of compList) {
            const href = safeKufarUrl ? safeKufarUrl(comp.link) : "";
            if (!safeKufarUrl && comp.link) {
                logClientError("[listing-assistant] safeKufarUrl not in context, link dropped", null, "warn");
            }
            const wrapper = href
                ? el("a", {
                    className: "la-competitor-row la-competitor-link",
                    attrs: { href, target: "_blank", rel: "noreferrer noopener" },
                })
                : el("div", { className: "la-competitor-row" });

            if (comp.image_url) {
                const imgSrc = safeImageUrl ? safeImageUrl(comp.image_url) : "";
                if (imgSrc) {
                    const imgEl = el("img", {
                        className: "la-competitor-thumb",
                        attrs: {
                            src: imgSrc,
                            alt: comp.title || "\u041A\u043E\u043D\u043A\u0443\u0440\u0435\u043D\u0442",
                            loading: "lazy",
                        },
                    });
                    imgEl.addEventListener("error", () => {
                        imgEl.style.display = "none";
                    });
                    wrapper.appendChild(imgEl);
                }
            }

            const info = el("div", { className: "la-competitor-info" });
            info.appendChild(
                el("span", { className: "la-competitor-title", text: comp.title || "" }),
            );
            info.appendChild(
                el("span", { className: "la-competitor-price mono" },
                    document.createTextNode(`${Math.round(comp.price_byn || 0)} BYN`)),
            );
            if (comp.advantage) {
                info.appendChild(
                    el("span", { className: "la-competitor-advantage", text: comp.advantage }),
                );
            }
            wrapper.appendChild(info);

            if (href) {
                wrapper.appendChild(
                    el("span", { className: "la-competitor-cta", text: "Открыть" }),
                );
            }

            list.appendChild(wrapper);
        }
        return el(
            "section",
            { className: "la-section" },
            el("span", { className: "la-section-title", text: "Конкуренты на рынке" }),
            list,
        );
    }

    function renderError(message) {
        resultBox.replaceChildren();
        resultBox.appendChild(el("div", { className: "la-error", text: message }));
        showResultOverlay();
    }

    function showResultOverlay() {
        if (resultOverlay) resultOverlay.hidden = false;
        if (resultBox) resultBox.scrollTop = 0;
        const sheet = modal?.querySelector(".detail-sheet");
        if (sheet) sheet.classList.add("la-sheet-expanded");
    }

    function hideResultOverlay() {
        if (resultOverlay) resultOverlay.hidden = true;
        const sheet = modal?.querySelector(".detail-sheet");
        if (sheet) sheet.classList.remove("la-sheet-expanded");
        _cancelLaProgress();
    }

    // ── Progress animation (AI-style) ──────────────────────────────────
    const LA_LOADING_STEPS = [
        "Сверяю похожие лоты…",
        "Считаю ценовой коридор…",
        "Готовлю текст объявления…",
        "Формирую план торга…",
        "Финализирую карточку…",
    ];
    const LA_LOADING_OVERTIME_STEPS = [
        "Дорабатываю карточку…",
        "Проверяю цену и аргументы торга…",
        "Ответ почти готов…",
    ];
    const LA_PROGRESS_EXPECTED_MS = 18000;
    const LA_PROGRESS_SOFT_CAP = 94;
    const LA_PROGRESS_GLIDE_CAP = 98.6;
    const LA_PROGRESS_GLIDE_MS = 32000;
    let _laProgressFrame = null;
    let _laProgressStartedAt = 0;
    let _laProgress = 0;

    function _cancelLaProgress() {
        if (_laProgressFrame) {
            cancelAnimationFrame(_laProgressFrame);
            _laProgressFrame = null;
        }
    }

    function _updateLaProgress(pct) {
        _laProgress = Math.max(0, Math.min(100, pct));
        const barEl = resultBox?.querySelector(".la-progress-bar");
        const pctEl = resultBox?.querySelector(".la-progress-pct");
        if (barEl) barEl.style.transform = `scaleX(${_laProgress / 100})`;
        if (pctEl) {
            pctEl.textContent = _laProgress >= 99.5
                ? "100%"
                : (_laProgress >= LA_PROGRESS_SOFT_CAP ? "почти готово" : Math.round(_laProgress) + "%");
        }
    }

    function _startLaProgress() {
        _cancelLaProgress();
        _laProgress = 0;
        _laProgressStartedAt = performance.now();
        let step = 0;
        let overtimeStep = -1;
        const textEl = () => resultBox?.querySelector(".la-loading-text");

        if (prefersReducedMotion()) {
            const t = textEl();
            if (t) t.textContent = LA_LOADING_STEPS[0];
            _updateLaProgress(12);
            return;
        }

        function tick(now) {
            const elapsed = Math.max(0, now - _laProgressStartedAt);
            const normalized = Math.min(elapsed / LA_PROGRESS_EXPECTED_MS, 1);
            const overtime = Math.max(0, elapsed - LA_PROGRESS_EXPECTED_MS);
            const overtimeNormalized = Math.min(overtime / LA_PROGRESS_GLIDE_MS, 1);
            const target = 3
                + normalized * (LA_PROGRESS_SOFT_CAP - 3)
                + overtimeNormalized * (LA_PROGRESS_GLIDE_CAP - LA_PROGRESS_SOFT_CAP);
            _updateLaProgress(Math.max(_laProgress, target));

            if (overtime > 1200) {
                const nextOvertimeStep = Math.floor(overtime / 5600) % LA_LOADING_OVERTIME_STEPS.length;
                if (nextOvertimeStep !== overtimeStep) {
                    overtimeStep = nextOvertimeStep;
                    const t = textEl();
                    if (t) t.textContent = LA_LOADING_OVERTIME_STEPS[overtimeStep];
                }
            } else {
                const nextStep = Math.min(
                    LA_LOADING_STEPS.length - 1,
                    Math.floor(normalized * LA_LOADING_STEPS.length),
                );
                if (nextStep !== step) {
                    step = nextStep;
                    const t = textEl();
                    if (t) t.textContent = LA_LOADING_STEPS[step];
                }
            }

            _laProgressFrame = requestAnimationFrame(tick);
        }

        const t = textEl();
        if (t) t.textContent = LA_LOADING_STEPS[0];
        _updateLaProgress(3);
        _laProgressFrame = requestAnimationFrame(tick);
    }

    function _stopLaProgress(success = true) {
        _cancelLaProgress();
        const ring = resultBox?.querySelector(".la-loader-ring");
        const icon = resultBox?.querySelector(".la-loader-icon");
        const barEl = resultBox?.querySelector(".la-progress-bar");
        const pctEl = resultBox?.querySelector(".la-progress-pct");
        const textEl = resultBox?.querySelector(".la-loading-text");

        if (success) {
            const markDone = () => {
                if (ring) ring.classList.add("la-loader-ring--done");
                if (icon) { icon.textContent = "✓"; icon.classList.add("la-loader-icon--done"); }
                if (barEl) barEl.classList.add("la-progress-bar--done");
                if (pctEl) pctEl.classList.add("la-progress-pct--done");
                if (textEl) textEl.textContent = "Готово!";
            };
            if (prefersReducedMotion() || _laProgress >= 99) {
                _updateLaProgress(100);
                markDone();
            } else {
                const startPct = _laProgress;
                const startTime = performance.now();
                const duration = Math.min(850, Math.max(420, (100 - startPct) * 9));
                function animateStep(now) {
                    const t = Math.min((now - startTime) / duration, 1);
                    const eased = 1 - Math.pow(1 - t, 3);
                    _updateLaProgress(startPct + (100 - startPct) * eased);
                    if (t < 1) {
                        _laProgressFrame = requestAnimationFrame(animateStep);
                    } else {
                        _laProgressFrame = null;
                        markDone();
                    }
                }
                _laProgressFrame = requestAnimationFrame(animateStep);
            }
        }
    }

    function renderLoading() {
        resultBox.replaceChildren();
        const wrap = el(
            "div",
            { className: "la-loading" },
            el("div", { className: "la-loader-wrap" },
                el("div", { className: "la-loader-ring" }),
                el("span", { className: "la-loader-icon", text: "AI" }),
            ),
            el("p", { className: "la-loading-text", text: "Сверяю похожие лоты…" }),
            el("div", { className: "la-progress" },
                el("div", { className: "la-progress-bar" }),
            ),
            el("span", { className: "la-progress-pct", text: "3%" }),
            el("p", { className: "la-loading-sub", text: "Обычно 10–25 секунд" }),
        );
        resultBox.appendChild(wrap);
        showResultOverlay();
        _startLaProgress();
    }

    function renderResult(data) {
        resultBox.replaceChildren();

        const summary = (data.market_summary || "").trim();
        if (summary) {
            resultBox.appendChild(
                el(
                    "section",
                    { className: "la-section la-section--summary" },
                    el("span", { className: "la-section-title", text: "Контекст рынка" }),
                    el("p", { className: "la-summary", text: summary }),
                ),
            );
        }

        const titleBlock = buildCopyBlock("Заголовок", data.title_suggestion);
        if (titleBlock) {
            resultBox.appendChild(
                el("section", { className: "la-section" },
                    el("span", { className: "la-section-title", text: "Заголовок" }),
                    titleBlock,
                ),
            );
        }

        const desc = buildCopyBlock("Описание", data.description);
        if (desc) {
            const descSection = el(
                "section",
                { className: "la-section" },
                el("span", { className: "la-section-title", text: "Описание" }),
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

        const competitors = buildCompetitors(data.competitors);
        if (competitors) resultBox.appendChild(competitors);

        const photoTips = buildPhotoTips(data.photo_tips);
        if (photoTips) resultBox.appendChild(photoTips);

        if (data.disclaimer) {
            resultBox.appendChild(
                el("p", { className: "la-disclaimer", text: data.disclaimer }),
            );
        }

        showResultOverlay();
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
                _resultSource = "history";
                state.misc.listingAssistantResult = entry.output;
                renderResult(entry.output);
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

    function clearHistory() {
        try {
            localStorage.removeItem(HISTORY_KEY);
        } catch (_) {
            saveHistory([]);
        }
        renderHistoryCounts();
        renderHistoryList();
        showToast("История помощника очищена", "success", 1400);
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

        if (typeof checkAiConsent === "function") {
            try {
                await checkAiConsent();
            } catch (_) {
                return;
            }
        }

        setBusy(true);
        _resultSource = "form";
        const myId = ++_analysisId;
        renderLoading();

        try {
            const data = await postJson("/api/v1/ai/listing-assistant", payload);

            if (_analysisId !== myId) return;

            _stopLaProgress(true);
            const delay = prefersReducedMotion() ? 150 : 950;
            setTimeout(() => {
                if (_analysisId !== myId) return;
                renderResult(data || {});
            }, delay);

            state.misc.listingAssistantResult = data;

            pushHistoryEntry({
                id: typeof crypto !== "undefined" && crypto.randomUUID
                    ? crypto.randomUUID()
                    : Date.now().toString(36) + Math.random().toString(36).slice(2),
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
            if (_analysisId !== myId) return;
            _stopLaProgress(false);
            _cancelLaProgress();
            const text = (error && error.message) || "Не удалось получить ответ AI";
            renderError(text);
            showToast(text, "error");
        } finally {
            setBusy(false);
        }
    }

    // ── Wire DOM ───────────────────────────────────────────────────────

    const _notesHandler = () => updateNotesCounter();
    const _photoHandler = (e) => {
        void handlePhotoFiles(e.target.files);
        e.target.value = "";
    };
    const _photoOpenHandler = () => photoInput?.click();
    const _tabsHandler = (e) => {
        const target = e.target.closest("[data-la-tab]");
        if (target) switchTab(target.dataset.laTab);
    };
    const _openHandler = () => {
        clearForm();
        openModal("form");
    };
    const _closeHandler = () => closeModal();
    const _overlayHandler = () => _modalBackdropHandler();
    const _resultBackHandler = () => {
        hideResultOverlay();
        if (_resultSource === "history") {
            switchTab("history");
        }
    };
    const _saveHistoryPreferenceHandler = () => {
        saveHistoryPreference();
        showToast(
            isHistorySavingEnabled()
                ? "История будет храниться 30 дней"
                : "Новые запросы не будут сохраняться",
            "info",
            1800,
        );
    };
    const _clearHistoryHandler = () => clearHistory();
    const _modalBackdropHandler = () => {
        if (resultOverlay && !resultOverlay.hidden) {
            _resultBackHandler();
        } else {
            closeModal();
        }
    };

    form.addEventListener("submit", handleSubmit);
    notesInput?.addEventListener("input", _notesHandler);
    photoInput?.addEventListener("change", _photoHandler);
    photoAdd?.addEventListener("click", _photoOpenHandler);
    if (tabsRow) tabsRow.addEventListener("click", _tabsHandler);
    const _tabsKeyboardCleanup = bindRovingTablist(tabsRow);
    openBtn?.addEventListener("click", _openHandler);
    closeBtn?.addEventListener("click", _closeHandler);
    overlay?.addEventListener("click", _overlayHandler);
    resultBackBtn?.addEventListener("click", _resultBackHandler);
    saveHistoryCheckbox?.addEventListener("change", _saveHistoryPreferenceHandler);
    historyClearBtn?.addEventListener("click", _clearHistoryHandler);
    const _keydownHandler = (e) => {
        if (e.key === "Escape" && !modal.hidden) {
            if (resultOverlay && !resultOverlay.hidden) {
                _resultBackHandler();
            } else {
                closeModal();
            }
        }
    };
    document.addEventListener("keydown", _keydownHandler);

    initHistoryPreference();
    renderHistoryCounts();
    updateNotesCounter();

    return {
        destroy() {
            form.removeEventListener("submit", handleSubmit);
            document.removeEventListener("keydown", _keydownHandler);
            notesInput?.removeEventListener("input", _notesHandler);
            photoInput?.removeEventListener("change", _photoHandler);
            photoAdd?.removeEventListener("click", _photoOpenHandler);
            if (tabsRow) tabsRow.removeEventListener("click", _tabsHandler);
            _tabsKeyboardCleanup();
            openBtn?.removeEventListener("click", _openHandler);
            closeBtn?.removeEventListener("click", _closeHandler);
            overlay?.removeEventListener("click", _overlayHandler);
            resultBackBtn?.removeEventListener("click", _resultBackHandler);
            saveHistoryCheckbox?.removeEventListener("change", _saveHistoryPreferenceHandler);
            historyClearBtn?.removeEventListener("click", _clearHistoryHandler);
        },
    };
}

app.createApiListingAssistant = createApiListingAssistant;
})(window.App = window.App || {});
