/**
 * api_ai.js — AI listing analysis API calls.
 */
function createApiAi(context) {
    const { state, elements, postJson, escapeHtml, formatPrice, telegramHeaders } = context;

    let _aiLoading = false;

    async function loadAIAnalysis(adId) {
        const block = elements.detailAiBlock;
        const content = elements.detailAiContent;
        const query = (state.detail?.query || state.query || "").trim();
        if (_aiLoading || !adId || !query || !block || !content) return;

        _aiLoading = true;
        state.detailAi = {
            adId,
            loading: true,
            result: null,
            error: "",
            source: "ai",
        };

        block.hidden = false;
        content.innerHTML = '<div class="ai-loading">Анализирую объявление…</div>';

        try {
            const result = await postJson("/api/v1/ai/analyze", {
                ad_id: adId,
                query,
            });
            state.detailAi = {
                adId,
                loading: false,
                result,
                error: "",
                source: "ai",
            };
            renderAIResult(content, result);
        } catch (err) {
            const message = `Не удалось выполнить анализ${err.message ? `: ${err.message}` : ""}`;
            state.detailAi = {
                adId,
                loading: false,
                result: null,
                error: message,
                source: "ai",
            };
            content.innerHTML = `<div class="ai-error">${escapeHtml(message)}</div>`;
        } finally {
            _aiLoading = false;
        }
    }

    function renderAIResult(container, data) {
        let html = "";

        // ── Condition assessment from photos ──
        if (data.condition) {
            const cond = data.condition;
            const badgeClass = cond.label === "Отличное" ? "ai-badge--good" :
                               cond.label === "Хорошее" ? "ai-badge--ok" :
                               cond.label === "Удовлетворительное" ? "ai-badge--warn" : "ai-badge--bad";
            html += `<div class="ai-section">
                <span class="ai-label">Состояние по фото</span>
                <span class="ai-badge ${badgeClass}">${escapeHtml(cond.label)}</span>
                ${cond.notes?.length ? `<ul class="ai-notes">${cond.notes.map(n => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}
            </div>`;
        }

        // ── Fair price estimate ──
        if (data.fair_price) {
            const fp = data.fair_price;
            const fromPrice = fp.from || fp.from_price;
            const toPrice = fp.to || fp.to_price;
            html += `<div class="ai-section">
                <span class="ai-label">За сколько можно купить</span>
                ${fromPrice != null && toPrice != null
                    ? `<div class="ai-fair-price">${Math.round(fromPrice)} — ${Math.round(toPrice)} BYN</div>`
                    : fromPrice != null
                        ? `<div class="ai-fair-price">~${Math.round(fromPrice)} BYN</div>`
                        : ""}
                ${fp.reasoning ? `<p class="ai-reasoning">${escapeHtml(fp.reasoning)}</p>` : ""}
            </div>`;
        }

        // ── What to watch out for ──
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

        // ── Best alternative (link to cheapest similar) ──
        if (data.best_alternative) {
            const ba = data.best_alternative;
            html += `<div class="ai-section ai-section--best">
                <span class="ai-label">Лучше вариант</span>
                <a class="ai-best-link" href="${escapeHtml(ba.link)}" target="_blank" rel="noreferrer noopener">
                    ${ba.image_url ? `<img class="ai-best-thumb" src="${escapeHtml(ba.image_url)}" alt="" loading="lazy">` : ""}
                    <div class="ai-best-info">
                        <span class="ai-best-title">${escapeHtml(ba.title)}</span>
                        <span class="ai-best-price mono">${Math.round(ba.price_byn)} BYN</span>
                    </div>
                    <span class="ai-best-arrow">→</span>
                </a>
            </div>`;
        }

        // ── Other alternatives ──
        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter(s => s.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                html += `<div class="ai-section">
                    <span class="ai-label">Другие варианты</span>
                    <div class="ai-similar">${others.map(s =>
                        `<a class="ai-similar-item" href="${escapeHtml(s.link)}" target="_blank" rel="noreferrer noopener">
                            ${s.image_url ? `<img class="ai-similar-thumb" src="${escapeHtml(s.image_url)}" alt="" loading="lazy">` : ""}
                            <div class="ai-similar-info">
                                <span class="ai-similar-title">${escapeHtml(s.title)}</span>
                                <span class="ai-similar-price mono">${Math.round(s.price_byn)} BYN</span>
                            </div>
                        </a>`
                    ).join("")}</div>
                </div>`;
            }
        }

        // ── Recommendation ──
        if (data.recommendation?.text) {
            const rec = data.recommendation;
            const verdictClass = rec.verdict === "worth_it" ? "ai-badge--good"
                : rec.verdict === "think_twice" ? "ai-badge--warn" : "ai-badge--bad";
            const verdictText = rec.verdict === "worth_it" ? "Стоит брать"
                : rec.verdict === "think_twice" ? "Подумай" : "Дорого";
            html += `<div class="ai-section ai-section--recommendation">
                <span class="ai-label">Рекомендация</span>
                <span class="ai-badge ${verdictClass}">${verdictText}</span>
                <p class="ai-recommendation-text">${escapeHtml(rec.text)}</p>
            </div>`;
        }

        // ── Summary ──
        if (data.summary) {
            html += `<div class="ai-summary">${escapeHtml(data.summary)}</div>`;
        }

        // ── Disclaimer ──
        html += `<p class="ai-disclaimer">${escapeHtml(data.disclaimer || "Анализ носит информационный характер. Результаты не являются гарантией.")}</p>`;

        container.innerHTML = html;
    }

    let _photoLoading = false;

    async function searchByPhoto(file) {
        if (_photoLoading) return;
        _photoLoading = true;

        const section = elements.photoResultsSection;
        const list = elements.photoResultsList;
        const descEl = elements.photoResultsDesc;
        const queryEl = elements.photoResultsQuery;

        if (!section || !list) return;

        // Show photo results section, hide regular listings
        section.hidden = false;
        const listingsSection = document.getElementById("listings-section");
        if (listingsSection) listingsSection.hidden = true;

        descEl.textContent = "Анализирую фото…";
        queryEl.textContent = "";
        list.innerHTML = '<div class="ai-loading">Определяю товар и ищу на Kufar…</div>';

        // Switch to ads view
        const adsTab = document.getElementById("tab-ads");
        if (adsTab) adsTab.click();

        try {
            const formData = new FormData();
            formData.append("photo", file);

            const resp = await fetch("/api/v1/ai/search-by-photo", {
                method: "POST",
                headers: { ...telegramHeaders() },
                body: formData,
            });

            if (!resp.ok) {
                let detail = "";
                try { const j = await resp.json(); detail = j.detail || ""; } catch {}
                throw new Error(detail || `Ошибка ${resp.status}`);
            }

            const data = await resp.json();

            const sourceLabel = data.source === "ocr" ? "OCR" : "AI";
            descEl.textContent = data.description || "Товар определён";
            if (data.source && descEl.textContent) {
                descEl.textContent = `${descEl.textContent} (${sourceLabel})`;
            }
            queryEl.textContent = data.query || "";

            if (!data.listings?.length) {
                list.innerHTML = '<p class="photo-results-empty">Ничего не найдено на Kufar по этому запросу</p>';
                return;
            }

            list.innerHTML = "";
            data.listings.forEach(item => {
                const card = document.createElement("article");
                card.className = "listing";

                const thumb = item.image_url
                    ? `<img class="listing-thumb" src="${escapeHtml(item.image_url)}" alt="" loading="lazy">`
                    : `<div class="listing-thumb placeholder">Нет фото</div>`;

                const price = item.price_byn ? formatPrice(item.price_byn) : "—";
                const link = item.link || `https://www.kufar.by/item/${item.ad_id}`;

                card.innerHTML = `
                    <div class="listing-top">
                        ${thumb}
                        <div class="listing-body">
                            <span class="listing-name">${escapeHtml(item.title)}</span>
                            <span class="listing-price mono">${price}</span>
                        </div>
                    </div>
                    <div class="listing-actions">
                        <a class="listing-btn listing-btn--accent" href="${escapeHtml(link)}" target="_blank" rel="noreferrer noopener">Перейти на Kufar</a>
                    </div>
                `;
                list.appendChild(card);
            });

        } catch (err) {
            list.innerHTML = `<div class="ai-error">Не удалось выполнить поиск${err.message ? ": " + escapeHtml(err.message) : ""}</div>`;
        } finally {
            _photoLoading = false;
        }
    }

    return {
        loadAIAnalysis,
        searchByPhoto,
    };
}
