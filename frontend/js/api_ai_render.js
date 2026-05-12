/**
 * api_ai_render.js — DOM-build helpers and final-result rendering for the AI
 * analysis modal.
 *
 * UX-M8 (Wave 25): extracted from api_ai.js. This module is pure
 * presentation: it takes a result `data` object and produces the DOM
 * tree for it, plus error-state DOM. The orchestrator owns the
 * decision of WHEN to call render functions and what `data` looks
 * like; the modal owns the loader visuals.
 */
(function (app) {
"use strict";

const { domEl, domFragment } = app;

function createAiRender(context, aiCtx) {
    const { elements, safeUrl, formatPrice } = context;

    // Section IDs for scroll navigation
    const SECTION_IDS = {
        red_flags: "ai-sec-flags",
        scam: "ai-sec-scam",
        photo_auth: "ai-sec-photo",
        condition: "ai-sec-condition",
        fair_price: "ai-sec-price",
        resale: "ai-sec-resale",
        market: "ai-sec-market",
        best_alt: "ai-sec-best",
        similar: "ai-sec-similar",
        watch_out: "ai-sec-watch",
        checklist: "ai-sec-checklist",
        tips: "ai-sec-tips",
        recommendation: "ai-sec-rec",
    };

    function renderAiErrorState(container, message, onRetry) {
        const retryBtn = domEl("button", {
            className: "ai-retry-btn",
            type: "button",
            text: "Повторить",
        });
        retryBtn.addEventListener("click", onRetry);
        container.replaceChildren(
            domEl("div", { className: "ai-error", text: message }),
            retryBtn,
        );
    }

    function _buildAiSection(label, children, extraClass = "", sectionId = "") {
        const attrs = {};
        if (sectionId) {
            attrs.id = sectionId;
            attrs["data-section-key"] = sectionId;
        }
        return domEl(
            "div",
            { className: `ai-section${extraClass ? ` ${extraClass}` : ""}`, ...attrs },
            domEl("span", { className: "ai-label", text: label }),
            children,
        );
    }

    function _buildAiList(tagName, className, items) {
        return domEl(
            tagName,
            { className },
            items.map((item) => domEl("li", { text: item })),
        );
    }

    function _splitAiSentences(text) {
        const normalized = String(text || "").replace(/\s+/g, " ").trim();
        if (!normalized) {
            return { lead: "", details: [] };
        }

        const parts = normalized
            .match(/[^.!?]+[.!?]?/gu)
            ?.map((part) => part.trim())
            .filter(Boolean) || [normalized];

        return {
            lead: parts[0] || normalized,
            details: parts.slice(1, 4),
        };
    }

    function _buildAiMetaPill(text, extraClass = "") {
        return domEl("span", {
            className: `ai-meta-pill${extraClass ? ` ${extraClass}` : ""}`,
            text,
        });
    }

    function _formatAiWarning(raw) {
        const text = String(raw || "").trim();
        if (!text) return "";
        if (/AI сейчас на лимите|429|RATE_LIMIT|RESOURCE_EXHAUSTED/i.test(text)) {
            return "AI сейчас на лимите. Показан рыночный черновик по данным рынка — полный анализ можно повторить чуть позже.";
        }
        if (/AI-сервис временно недоступен\. Показан упрощ[её]нный анализ\./.test(text)) {
            return "AI не ответил. Показан рыночный черновик по данным рынка.";
        }
        if (text.includes("AI-сервис сейчас недоступен")) {
            return "AI не ответил. Показан рыночный черновик по данным рынка.";
        }
        return text;
    }

    function _buildAiResultNodes(data) {
        const nodes = [];
        const aiWarning = _formatAiWarning(data._ai_warning);

        if (aiWarning) {
            nodes.push(
                domEl("div", { className: "ai-warning-banner" },
                    domEl("span", { className: "ai-warning-icon", attrs: { "aria-hidden": "true" }, text: "i" }),
                    domEl("span", { text: aiWarning }),
                )
            );
        }

        if (data.recommendation) {
            const verdictMap = {
                worth_it: { text: "Стоит брать", cls: "ai-badge--good" },
                think_twice: { text: "Подумай", cls: "ai-badge--warn" },
                overpriced: { text: "Дорого", cls: "ai-badge--bad" },
            };
            const verdict = verdictMap[data.recommendation.verdict] || verdictMap.think_twice;
            nodes.push(
                domEl(
                    "div",
                    { className: "ai-modal-verdict" },
                    domEl("span", { className: `ai-badge ${verdict.cls} ai-badge--lg`, text: verdict.text }),
                    data.summary ? domEl("p", { className: "ai-modal-summary", text: data.summary }) : null,
                )
            );
        }

        if (data.red_flags?.length) {
            nodes.push(
                _buildAiSection(
                    "Красные флаги",
                    domEl(
                        "div",
                        { className: "ai-flags" },
                        data.red_flags.map((flag, index) => domEl(
                            "div",
                            { className: "ai-flag-item" },
                            domEl("span", { className: "ai-flag-index mono", text: String(index + 1).padStart(2, "0") }),
                            domEl("span", { className: "ai-flag-text", text: flag }),
                        )),
                    ),
                    "ai-section--flags",
                    SECTION_IDS.red_flags,
                )
            );
        }

        // ── Scam analysis ──────────────────────────────────────────────
        if (data.scam_analysis) {
            const scam = data.scam_analysis;
            const scamChildren = [];
            if (scam.indicators?.length) {
                scamChildren.push(
                    domEl("ul", { className: "ai-scam-indicators" },
                        scam.indicators.map((ind) => domEl("li", { text: ind })))
                );
            }
            if (scam.seller_warnings?.length) {
                scamChildren.push(
                    domEl("div", { className: "ai-scam-seller-warnings" },
                        domEl("span", { className: "ai-label-sub", text: "Продавец:" }),
                        domEl("ul", { className: "ai-notes" },
                            scam.seller_warnings.map((w) => domEl("li", { text: w }))))
                );
            }
            if (scam.photo_issues?.length) {
                scamChildren.push(
                    domEl("div", { className: "ai-scam-photo-warnings" },
                        domEl("span", { className: "ai-label-sub", text: "Фото:" }),
                        domEl("ul", { className: "ai-notes" },
                            scam.photo_issues.map((iss) => domEl("li", { text: iss }))))
                );
            }
            if (scam.advice) {
                scamChildren.push(
                    domEl("p", { className: "ai-scam-advice", text: scam.advice })
                );
            }
            nodes.push(
                _buildAiSection("Безопасность сделки", domFragment(...scamChildren), "ai-section--scam", SECTION_IDS.scam)
            );
        }

        // ── Photo authenticity ─────────────────────────────────────────
        if (data.photo_authenticity) {
            const photo = data.photo_authenticity;
            const photoIssues = [];
            if (photo.stock_photo_detected) photoIssues.push("Стоковое фото");
            if (photo.duplicate_image_detected) photoIssues.push("Дубликат изображения");
            if (photo.watermark_detected) photoIssues.push("Водяной знак");
            if (photo.screenshot_detected) photoIssues.push("Скриншот вместо фото");
            if (photo.issues?.length) {
                photo.issues.forEach((iss) => { if (!photoIssues.includes(iss)) photoIssues.push(iss); });
            }

            const photoChildren = [];
            if (photoIssues.length) {
                photoChildren.push(
                    domEl("ul", { className: "ai-photo-issues" },
                        photoIssues.map((iss) => domEl("li", { className: "ai-photo-issue-item", text: iss })))
                );
            } else {
                photoChildren.push(
                    domEl("span", { className: "ai-badge ai-badge--good", text: "Фото выглядит подлинным" })
                );
            }
            if (photo.confidence != null) {
                photoChildren.push(
                    domEl("span", { className: "ai-confidence", text: `уверенность ${Math.round(photo.confidence * 100)}%` })
                );
            }
            nodes.push(
                _buildAiSection("Аутентичность фото", domFragment(...photoChildren), "ai-section--photo-auth", SECTION_IDS.photo_auth)
            );
        }

        if (data.condition) {
            const cond = data.condition;
            // Defensive: condition might be a string from cached/poll result
            const condObj = typeof cond === "string" ? { label: cond } : cond;
            const condLabelMap = {
                "Отличное": { text: "Отличное", cls: "ai-badge--good" },
                "Хорошее": { text: "Хорошее", cls: "ai-badge--ok" },
                "Удовлетворительное": { text: "Удовлетв.", cls: "ai-badge--warn" },
                "Требует внимания": { text: "Внимание", cls: "ai-badge--bad" },
                "Плохое": { text: "Плохое", cls: "ai-badge--bad" },
                "Excellent": { text: "Отличное", cls: "ai-badge--good" },
                "Good": { text: "Хорошее", cls: "ai-badge--ok" },
                "Fair": { text: "Удовлетв.", cls: "ai-badge--warn" },
                "Poor": { text: "Плохое", cls: "ai-badge--bad" },
            };
            const mapped = condLabelMap[condObj.label] || { text: condObj.label || "—", cls: "ai-badge--ok" };
            nodes.push(
                _buildAiSection(
                    "Состояние по фото",
                    domFragment(
                        domEl("span", { className: `ai-badge ${mapped.cls}`, text: mapped.text }),
                        condObj.confidence
                            ? domEl("span", { className: "ai-confidence", text: `уверенность ${Math.round(condObj.confidence * 100)}%` })
                            : null,
                        condObj.notes?.length
                            ? domEl("ul", { className: "ai-notes" }, condObj.notes.map((note) => domEl("li", { text: note })))
                            : null,
                    ),
                    "",
                    SECTION_IDS.condition,
                )
            );
        }

        if (data.fair_price) {
            const fairPrice = data.fair_price;
            const fromPrice = fairPrice.from || fairPrice.from_price;
            const toPrice = fairPrice.to || fairPrice.to_price;
            let priceText = "";
            if (fromPrice != null && toPrice != null) {
                priceText = `${Math.round(fromPrice)} — ${Math.round(toPrice)} BYN`;
            } else if (fromPrice != null) {
                priceText = `~${Math.round(fromPrice)} BYN`;
            }
            nodes.push(
                _buildAiSection(
                    "Справедливая цена",
                    domFragment(
                        priceText ? domEl("div", { className: "ai-fair-price", text: priceText }) : null,
                        fairPrice.reasoning ? domEl("p", { className: "ai-reasoning", text: fairPrice.reasoning }) : null,
                    ),
                    "",
                    SECTION_IDS.fair_price,
                )
            );
        }

        if (data.resale_potential) {
            const resale = data.resale_potential;
            const prices = [resale.fast_price, resale.market_price, resale.optimal_price].filter(Boolean);
            if (prices.length) {
                nodes.push(
                    _buildAiSection(
                        "Потенциал перепродажи",
                        domFragment(
                            domEl(
                                "div",
                                { className: "ai-resale-prices" },
                                prices.map((price) => domEl(
                                    "div",
                                    { className: "ai-resale-row" },
                                    domEl("span", { className: "ai-resale-label", text: price.label }),
                                    domEl("span", { className: "ai-resale-price mono", text: `${Math.round(price.price_byn)} BYN` }),
                                    price.reasoning ? domEl("span", { className: "ai-resale-note", text: price.reasoning }) : null,
                                ))
                            ),
                            resale.reasoning ? domEl("p", { className: "ai-reasoning", text: resale.reasoning }) : null,
                        ),
                        "",
                        SECTION_IDS.resale,
                    )
                );
            }
        }

        if (data.market_context) {
            const marketContext = _splitAiSentences(data.market_context);
            const contextPills = [];
            if (data.similar_listings?.length) {
                contextPills.push(_buildAiMetaPill(`Аналогов: ${data.similar_listings.length}`, "ai-meta-pill--accent"));
            }
            if (data.best_alternative?.price_byn) {
                contextPills.push(_buildAiMetaPill(`Ориентир: ${formatPrice(data.best_alternative.price_byn)}`));
            }
            if (data.price_reference_scope === "category" && data.price_reference_label) {
                contextPills.push(_buildAiMetaPill(data.price_reference_label, "ai-meta-pill--scope"));
            }
            nodes.push(
                _buildAiSection(
                    "Контекст рынка",
                    domEl(
                        "div",
                        { className: "ai-market-context" },
                        domEl("p", { className: "ai-market-lead", text: marketContext.lead || data.market_context }),
                        marketContext.details.length
                            ? domEl(
                                "div",
                                { className: "ai-market-points" },
                                marketContext.details.map((item) => domEl("div", { className: "ai-market-point", text: item })),
                            )
                            : null,
                        contextPills.length
                            ? domEl("div", { className: "ai-meta-pills" }, contextPills)
                            : null,
                    ),
                    "ai-section--market",
                    SECTION_IDS.market,
                )
            );
        }

        if (data.best_alternative) {
            const bestAlternative = data.best_alternative;
            const bestPills = [];
            if (bestAlternative.price_byn) {
                bestPills.push(_buildAiMetaPill(formatPrice(bestAlternative.price_byn), "ai-meta-pill--good"));
            }
            if (bestAlternative.condition) {
                bestPills.push(_buildAiMetaPill(bestAlternative.condition, "ai-meta-pill--muted"));
            }
            nodes.push(
                _buildAiSection(
                    "Лучший вариант",
                    domFragment(
                        domEl(
                            "div",
                            { className: "ai-best-caption" },
                            domEl("span", { className: "ai-best-kicker", text: "Самый близкий аналог из найденных" }),
                            data.best_pick_reason ? domEl("p", { className: "ai-best-reason", text: data.best_pick_reason }) : null,
                        ),
                        domEl(
                            "a",
                            {
                                className: "ai-best-link",
                                attrs: { href: safeUrl(bestAlternative.link), target: "_blank", rel: "noreferrer noopener" },
                            },
                            bestAlternative.image_url
                                ? domEl("img", {
                                    className: "ai-best-thumb",
                                    attrs: { src: safeUrl(bestAlternative.image_url), alt: bestAlternative.title || "Лучшая альтернатива", loading: "lazy" },
                                })
                                : null,
                            domEl(
                                "div",
                                { className: "ai-best-info" },
                                domEl("span", { className: "ai-best-title", text: bestAlternative.title }),
                                bestPills.length ? domEl("div", { className: "ai-meta-pills" }, bestPills) : null,
                            ),
                            domEl("span", { className: "ai-best-cta", text: "Открыть" }),
                        ),
                    ),
                    "ai-section--best",
                    SECTION_IDS.best_alt,
                )
            );
        }

        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter((item) => item.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                nodes.push(
                    _buildAiSection(
                        `Другие варианты (${others.length})`,
                        domEl(
                            "div",
                            { className: "ai-similar" },
                            others.map((item) => domEl(
                                "a",
                                {
                                    className: "ai-similar-item",
                                    attrs: { href: safeUrl(item.link), target: "_blank", rel: "noreferrer noopener" },
                                },
                                item.image_url
                                    ? domEl("img", {
                                        className: "ai-similar-thumb",
                                        attrs: { src: safeUrl(item.image_url), alt: item.title || "Похожий товар", loading: "lazy" },
                                    })
                                    : null,
                                domEl(
                                    "div",
                                    { className: "ai-similar-info" },
                                    domEl("span", { className: "ai-similar-title", text: item.title }),
                                    domEl(
                                        "span",
                                        {
                                            className: "ai-similar-price mono",
                                            text: `${Math.round(item.price_byn)} BYN${item.condition ? ` · ${item.condition}` : ""}`,
                                        },
                                    ),
                                ),
                            ))
                        ),
                        "",
                        SECTION_IDS.similar,
                    )
                );
            }
        }

        if (data.watch_out?.length) {
            nodes.push(
                _buildAiSection(
                    "На что обратить внимание",
                    domEl(
                        "div",
                        { className: "ai-watch-list" },
                        data.watch_out.map((item) => {
                            // Defensive: item might be a string instead of {point, why}
                            const point = typeof item === "string" ? item : (item.point || "");
                            const why = typeof item === "string" ? "" : (item.why || "");
                            return domEl(
                                "div",
                                { className: "ai-watch-item" },
                                domEl("strong", { text: point }),
                                why ? domEl("span", { text: why }) : null,
                            );
                        })
                    ),
                    "",
                    SECTION_IDS.watch_out,
                )
            );
        }

        if (data.meeting_checklist?.length) {
            nodes.push(
                _buildAiSection(
                    "Чек-лист для встречи",
                    _buildAiList("ol", "ai-checklist", data.meeting_checklist),
                    "",
                    SECTION_IDS.checklist,
                )
            );
        }

        if (data.negotiation_tips?.length) {
            nodes.push(
                _buildAiSection(
                    "Как торговаться",
                    _buildAiList("ul", "ai-tips", data.negotiation_tips),
                    "",
                    SECTION_IDS.tips,
                )
            );
        }

        if (data.recommendation?.text) {
            nodes.push(
                _buildAiSection(
                    "Рекомендация",
                    domEl("p", { className: "ai-recommendation-text", text: data.recommendation.text }),
                    "ai-section--recommendation",
                    SECTION_IDS.recommendation,
                )
            );
        }

        nodes.push(
            domEl("p", {
                className: "ai-disclaimer",
                text: data.disclaimer || "AI-анализ носит информационно-справочный характер и не является финансовой или инвестиционной консультацией.",
            })
        );
        return nodes;
    }

    function renderAIModalResult(data) {
        if (elements.aiModalLoading) elements.aiModalLoading.hidden = true;
        if (elements.aiModalError) elements.aiModalError.hidden = true;

        const container = elements.aiModalResult;
        if (!container) return;
        container.hidden = false;

        // Hide time notice when results appear
        const notice = elements.aiModal?.querySelector(".ai-time-notice");
        if (notice) notice.hidden = true;

        // Build content area with all sections
        const contentArea = domEl("div", { className: "ai-content-area" });
        const resultNodes = _buildAiResultNodes(data);
        for (const node of resultNodes) {
            contentArea.appendChild(node);
        }

        // Simple single-column layout
        const layout = domEl("div", { className: "ai-result-layout" }, contentArea);

        container.replaceChildren(layout);
    }

    return {
        renderAIModalResult,
        renderAiErrorState,
    };
}

app.createAiRender = createAiRender;
})(window.App = window.App || {});
