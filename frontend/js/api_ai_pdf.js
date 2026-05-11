/**
 * api_ai_pdf.js — printable HTML / PDF export for the AI analysis result.
 *
 * UX-M8 (Wave 25): extracted from api_ai.js. This module is read-only
 * against `aiCtx.lastData` (set by the render module on success) and
 * uses the app's existing `postJson` (for Telegram-side server export)
 * and `safeUrl` / `escapeHtml` helpers (passed via context).
 *
 * Telegram WebApp path: POST the HTML to `/api/v1/ai/export-report`,
 * then open the returned URL via `openLink`. Non-Telegram fallback:
 * open the HTML in a new tab and trigger window.print().
 */
(function (app) {
"use strict";

function createAiPdf(context, aiCtx) {
    const { state, postJson, safeUrl, formatPrice } = context;

    // Sanitize URLs for href/src attributes — blocks javascript:, data:,
    // vbscript:, file:, and any other non-http(s) scheme. Returns "#"
    // for unsafe values so the link is rendered but does nothing on click.
    function _safeXmlUrl(url) {
        if (!url || typeof url !== "string") return "#";
        const trimmed = url.trim();
        const lowered = trimmed.toLowerCase();
        const escapeHtml = context.escapeHtml;
        if (lowered.startsWith("https://") || lowered.startsWith("http://")) {
            return escapeHtml(trimmed);
        }
        if (trimmed.startsWith("/") && !trimmed.startsWith("//")) {
            return escapeHtml(trimmed);
        }
        return "#";
    }

    function _buildPdfSections(data) {
        const sections = [];

        // AI warning (fallback analysis)
        if (data._ai_warning) {
            sections.push({ title: "⚠ Внимание", body: data._ai_warning });
        }

        // Verdict
        if (data.recommendation) {
            const verdictMap = {
                worth_it: "Стоит брать",
                think_twice: "Подумай",
                overpriced: "Дорого",
            };
            sections.push({
                title: "Вердикт",
                body: (verdictMap[data.recommendation.verdict] || data.recommendation.verdict)
                    + (data.summary ? "\n\n" + data.summary : ""),
            });
        }

        // Condition
        if (data.condition) {
            const c = typeof data.condition === "string" ? { label: data.condition } : data.condition;
            let body = c.label || "";
            if (c.confidence) body += ` (уверенность ${Math.round(c.confidence * 100)}%)`;
            if (c.notes?.length) body += "\n\n" + c.notes.map(n => "• " + n).join("\n");
            sections.push({ title: "Состояние по фото", body });
        }

        // Fair price
        if (data.fair_price) {
            const fp = data.fair_price;
            const from = fp.from || fp.from_price;
            const to = fp.to || fp.to_price;
            let body = from != null && to != null
                ? `${Math.round(from)} — ${Math.round(to)} BYN`
                : from != null ? `~${Math.round(from)} BYN` : "";
            if (fp.reasoning) body += "\n\n" + fp.reasoning;
            sections.push({ title: "Справедливая цена", body });
        }

        // Resale
        if (data.resale_potential) {
            const rp = data.resale_potential;
            const prices = [rp.fast_price, rp.market_price, rp.optimal_price].filter(Boolean);
            if (prices.length) {
                let body = prices.map(p => `${p.label}: ${Math.round(p.price_byn)} BYN`).join("\n");
                if (rp.reasoning) body += "\n\n" + rp.reasoning;
                sections.push({ title: "Потенциал перепродажи", body });
            }
        }

        // Market context
        if (data.market_context) {
            let body = data.market_context;
            if (data.price_reference_scope === "category" && data.price_reference_label) {
                body += `\nОриентир: ${data.price_reference_label}`;
            }
            sections.push({ title: "Контекст рынка", body });
        }

        // Best alternative
        if (data.best_alternative) {
            const ba = data.best_alternative;
            let body = `${ba.title} — ${Math.round(ba.price_byn)} BYN`;
            if (ba.condition) body += ` (${ba.condition})`;
            if (ba.link) body += "\n" + ba.link;
            if (data.best_pick_reason) body += "\n\n" + data.best_pick_reason;
            sections.push({ title: "Лучший вариант", body });
        }

        // Similar listings
        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter(s => s.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                const body = others.map(s => `${s.title} — ${Math.round(s.price_byn)} BYN${s.condition ? " (" + s.condition + ")" : ""}`).join("\n");
                sections.push({ title: `Другие варианты (${others.length})`, body });
            }
        }

        // Watch out
        if (data.watch_out?.length) {
            const body = data.watch_out.map(w => {
                const point = typeof w === "string" ? w : w.point;
                const why = typeof w === "string" ? "" : w.why;
                return why ? `${point}: ${why}` : point;
            }).join("\n\n");
            sections.push({ title: "На что обратить внимание", body });
        }

        // Meeting checklist
        if (data.meeting_checklist?.length) {
            const body = data.meeting_checklist.map((item, i) => `${i + 1}. ${item}`).join("\n");
            sections.push({ title: "Чек-лист для встречи", body });
        }

        // Negotiation tips
        if (data.negotiation_tips?.length) {
            const body = data.negotiation_tips.map(t => "• " + t).join("\n");
            sections.push({ title: "Как торговаться", body });
        }

        // Red flags
        if (data.red_flags?.length) {
            const body = data.red_flags.map(f => "⚠ " + f).join("\n");
            sections.push({ title: "Красные флаги", body });
        }

        // Recommendation text
        if (data.recommendation?.text) {
            sections.push({ title: "Рекомендация", body: data.recommendation.text });
        }

        return sections;
    }

    async function exportToPdf() {
        const data = aiCtx.lastData;
        if (!data) return;

        const detail = state.detail.data || {};
        const title = detail.title || "Объявление";
        const price = detail.price != null ? formatPrice(detail.price, detail.price_type) : (detail.price_type === "negotiable" ? "Договорная" : "");
        const adId = data.ad_id || "";
        const link = detail.link || (adId ? `https://www.kufar.by/item/${adId}` : "");
        const dateStr = new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
        const sections = _buildPdfSections(data);
        const escapeHtml = context.escapeHtml;

        const listingImages = (detail.images || []).slice(0, 4);

        const listingParams = detail.parameters || [];

        const verdictSection = sections.find(s => s.title === "Вердикт");
        const otherSections = sections.filter(s => s.title !== "Вердикт");
        const vMap = { "Стоит брать": { cls: "good", icon: "&#10003;" }, "Подумай": { cls: "warn", icon: "&#9888;" }, "Дорого": { cls: "bad", icon: "&#10007;" } };
        const verdictLine = verdictSection ? verdictSection.body.split("\n")[0] : "";
        const verdictSummary = verdictSection ? verdictSection.body.split("\n").slice(1).join("\n").trim() : "";
        const vInfo = vMap[verdictLine] || { cls: "warn", icon: "&#9888;" };

        let bestAltHtml = "";
        if (data.best_alternative) {
            const ba = data.best_alternative;
            bestAltHtml = `<div class="alt-card">
  ${ba.image_url ? `<img class="alt-thumb" src="${_safeXmlUrl(ba.image_url)}" alt="" />` : ""}
  <div class="alt-info">
    <div class="alt-title">${escapeHtml(ba.title)}</div>
    <div class="alt-meta">
      <span class="alt-price mono">${Math.round(ba.price_byn)} BYN</span>
      ${ba.condition ? `<span class="alt-cond">${escapeHtml(ba.condition)}</span>` : ""}
    </div>
    ${data.best_pick_reason ? `<div class="alt-reason">${escapeHtml(data.best_pick_reason)}</div>` : ""}
    ${ba.link ? `<a class="alt-link" href="${_safeXmlUrl(ba.link)}">Открыть на Kufar</a>` : ""}
  </div>
</div>`;
        }

        let similarHtml = "";
        if (data.similar_listings?.length) {
            const others = data.best_alternative
                ? data.similar_listings.filter(s => s.ad_id !== data.best_alternative.ad_id)
                : data.similar_listings;
            if (others.length) {
                similarHtml = `<section class="section">
  <div class="section-header">
    <div class="section-dot"></div>
    <div class="section-title">Другие варианты (${others.length})</div>
  </div>
  <div class="similar-list">
${others.slice(0, 8).map(s => `    <div class="similar-row">
      ${s.image_url ? `<img class="similar-thumb" src="${_safeXmlUrl(s.image_url)}" alt="" />` : `<span></span>`}
      <div>
        <div class="similar-title">${escapeHtml(s.title)}</div>
        <div class="similar-meta">${s.condition ? escapeHtml(s.condition) : "Состояние не указано"}${s.link ? ` · <a href="${_safeXmlUrl(s.link)}">Открыть</a>` : ""}</div>
      </div>
      <div class="similar-price mono">${Math.round(s.price_byn)} BYN</div>
    </div>`).join("\n")}
  </div>
</section>`;
                const idx = otherSections.findIndex(s => s.title.startsWith("Другие варианты"));
                if (idx >= 0) otherSections.splice(idx, 1);
            }
        }

        const bestIdx = otherSections.findIndex(s => s.title === "Лучший вариант");
        if (bestIdx >= 0) otherSections.splice(bestIdx, 1);

        const pdfHtml = `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>${escapeHtml(title)}</title>
<style>
  @page { margin: 14mm; size: A4; }
  :root {
    --pdf-bg: #f6f8fb;
    --pdf-canvas: #ffffff;
    --pdf-surface: #ffffff;
    --pdf-surface-muted: #f8fafc;
    --pdf-text: #111827;
    --pdf-text-soft: #334155;
    --pdf-muted: #64748b;
    --pdf-faint: #94a3b8;
    --pdf-border: #e5e7eb;
    --pdf-soft-blue: #eff6ff;
    --pdf-border-blue: #bfdbfe;
    --pdf-accent: #3b82f6;
    --pdf-accent-strong: #1d4ed8;
    --pdf-accent-ink: #1e3a8a;
    --pdf-header: #0f172a;
    --pdf-header-muted: #cbd5e1;
    --pdf-green: #16a34a;
    --pdf-green-ink: #166534;
    --pdf-green-soft: #f0fdf4;
    --pdf-green-border: #bbf7d0;
    --pdf-amber: #d97706;
    --pdf-amber-ink: #92400e;
    --pdf-amber-soft: #fffbeb;
    --pdf-amber-border: #fde68a;
    --pdf-red: #dc2626;
    --pdf-red-ink: #be123c;
    --pdf-red-soft: #fff1f2;
    --pdf-red-border: #fecdd3;
    --pdf-shadow: 0 18px 45px rgba(15, 23, 42, 0.10);
    --pdf-page-shadow: 0 24px 80px rgba(15, 23, 42, 0.16);
  }
  * { box-sizing: border-box; }
  html { background: var(--pdf-bg); }
  body { margin: 0; font-family: "Rubik", "Geist", "Segoe UI", sans-serif; color: var(--pdf-text); font-size: 9.6pt; line-height: 1.52; background: var(--pdf-bg); -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  a { color: var(--pdf-accent-strong); text-decoration: none; word-break: break-word; }
  .mono { font-family: "JetBrains Mono", "SFMono-Regular", monospace; font-variant-numeric: tabular-nums; }
  .print-banner { display: none; padding: 12px 16px; background: var(--pdf-soft-blue); border-bottom: 1px solid var(--pdf-border-blue); color: var(--pdf-accent-ink); font-size: 9pt; }
  .page-shell { max-width: 820px; margin: 0 auto; background: var(--pdf-canvas); min-height: 100vh; }
  .report-head { padding: 24px 28px 18px; color: var(--pdf-canvas); background: var(--pdf-header); border-radius: 0 0 18px 18px; position: relative; overflow: hidden; }
  .brand-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 800; letter-spacing: -0.03em; }
  .brand-mark { width: 32px; height: 32px; border-radius: 10px; display: grid; place-items: center; background: var(--pdf-soft-blue); color: var(--pdf-accent-ink); font-size: 11pt; }
  .report-meta { color: var(--pdf-header-muted); font-size: 8.3pt; text-align: right; }
  .hero-grid { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 20px; align-items: start; }
  .eyebrow { display: inline-flex; margin-bottom: 9px; padding: 3px 9px; border-radius: 999px; background: var(--pdf-soft-blue); color: var(--pdf-accent-ink); font-size: 7.8pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em; }
  h1 { margin: 0; max-width: 560px; font-size: 18pt; line-height: 1.16; letter-spacing: -0.045em; }
  .hero-price { margin-top: 10px; font-size: 19pt; line-height: 1; color: var(--pdf-border-blue); font-weight: 800; }
  .hero-link { display: block; margin-top: 10px; max-width: 560px; color: var(--pdf-header-muted); font-size: 8.2pt; }
  .photo-strip { display: grid; grid-template-columns: repeat(2, 58px); gap: 7px; }
  .photo-strip img { width: 58px; height: 58px; object-fit: cover; border-radius: 12px; border: 1px solid var(--pdf-border-blue); background: var(--pdf-soft-blue); }
  .verdict-card { margin: -10px 28px 18px; padding: 15px 16px; display: grid; grid-template-columns: 42px 1fr; gap: 13px; align-items: center; border-radius: 17px; background: var(--pdf-surface); border: 1px solid var(--verdict-border, var(--pdf-border)); box-shadow: var(--pdf-shadow); position: relative; z-index: 2; break-inside: avoid; }
  .verdict-card.good { --verdict-color: var(--pdf-green); --verdict-bg: var(--pdf-green-soft); --verdict-border: var(--pdf-green-border); }
  .verdict-card.warn { --verdict-color: var(--pdf-amber); --verdict-bg: var(--pdf-amber-soft); --verdict-border: var(--pdf-amber-border); }
  .verdict-card.bad { --verdict-color: var(--pdf-red); --verdict-bg: var(--pdf-red-soft); --verdict-border: var(--pdf-red-border); }
  .verdict-icon { width: 42px; height: 42px; border-radius: 14px; display: grid; place-items: center; font-size: 16pt; font-weight: 900; color: var(--verdict-color); background: var(--verdict-bg); border: 1px solid var(--verdict-border); }
  .verdict-text { color: var(--verdict-color); font-size: 14pt; font-weight: 850; letter-spacing: -0.035em; }
  .verdict-summary { margin-top: 3px; color: var(--pdf-muted); font-size: 9.2pt; }
  .facts-grid { margin: 0 28px 18px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; break-inside: avoid; }
  .fact { padding: 10px 11px; border: 1px solid var(--pdf-border); border-radius: 13px; background: var(--pdf-surface-muted); min-width: 0; }
  .fact-label { display: block; color: var(--pdf-muted); font-size: 7.3pt; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
  .fact-value { display: block; margin-top: 2px; color: var(--pdf-text); font-size: 10pt; font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .params-strip { margin: 0 28px 18px; display: flex; flex-wrap: wrap; gap: 6px; break-inside: avoid; }
  .param-chip { padding: 4px 8px; border-radius: 999px; background: var(--pdf-soft-blue); color: var(--pdf-text-soft); font-size: 7.8pt; border: 1px solid var(--pdf-border-blue); }
  .param-chip b { color: var(--pdf-accent-strong); }
  .content { padding: 0 28px 28px; display: grid; gap: 11px; }
  .section { padding: 13px 14px; background: var(--pdf-surface); border: 1px solid var(--pdf-border); border-radius: 15px; break-inside: avoid; }
  .section--price { background: var(--pdf-soft-blue); border-color: var(--pdf-border-blue); }
  .section--flags { background: var(--pdf-red-soft); border-color: var(--pdf-red-border); }
  .section-header { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
  .section-dot { width: 7px; height: 7px; border-radius: 99px; background: var(--pdf-accent); flex: 0 0 auto; }
  .section--flags .section-dot { background: var(--pdf-red); }
  .section--best .section-dot { background: var(--pdf-green); }
  .section-title { color: var(--pdf-accent-ink); font-size: 8pt; font-weight: 850; text-transform: uppercase; letter-spacing: 0.08em; }
  .section--flags .section-title { color: var(--pdf-red-ink); }
  .section--best .section-title { color: var(--pdf-green-ink); }
  .section-body { color: var(--pdf-text-soft); white-space: pre-wrap; }
  .section--price .section-body { color: var(--pdf-text); font-size: 10.8pt; font-weight: 750; }
  .alt-card { display: grid; grid-template-columns: 68px 1fr; gap: 11px; padding: 10px; border-radius: 13px; background: var(--pdf-green-soft); border: 1px solid var(--pdf-green-border); }
  .alt-thumb { width: 68px; height: 68px; object-fit: cover; border-radius: 10px; }
  .alt-title { color: var(--pdf-text); font-weight: 800; line-height: 1.25; }
  .alt-meta { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 8px; color: var(--pdf-muted); font-size: 8.5pt; }
  .alt-price { color: var(--pdf-green); font-size: 11pt; font-weight: 900; }
  .alt-reason { margin-top: 5px; color: var(--pdf-muted); font-size: 8.6pt; }
  .alt-link { display: inline-block; margin-top: 5px; font-size: 8.2pt; }
  .similar-list { display: grid; gap: 6px; }
  .similar-row { display: grid; grid-template-columns: 42px 1fr auto; gap: 9px; align-items: center; padding: 7px; border-radius: 11px; background: var(--pdf-surface-muted); border: 1px solid var(--pdf-border); }
  .similar-thumb { width: 42px; height: 42px; object-fit: cover; border-radius: 9px; }
  .similar-title { color: var(--pdf-text-soft); font-weight: 750; font-size: 8.8pt; line-height: 1.25; max-height: 2.5em; overflow: hidden; }
  .similar-meta { color: var(--pdf-muted); font-size: 7.8pt; }
  .similar-price { color: var(--pdf-text); font-weight: 900; font-size: 9pt; white-space: nowrap; }
  .footer { padding: 14px 28px 18px; color: var(--pdf-faint); font-size: 7.5pt; border-top: 1px solid var(--pdf-border); }
  .footer-brand { color: var(--pdf-muted); font-weight: 850; }
  @media screen { body { padding: 24px 0; } .page-shell { box-shadow: var(--pdf-page-shadow); border-radius: 24px; overflow: hidden; } .print-banner { display: block; max-width: 820px; margin: 0 auto; border-radius: 16px 16px 0 0; } }
  @media print { html, body { background: var(--pdf-canvas); } .page-shell { max-width: none; } .print-banner { display: none !important; } .report-head { border-radius: 0 0 18px 18px; } .content { gap: 8px; } .section { padding: 10px 11px; } }
</style>
</head>
<body>
<div class="print-banner">Если диалог печати не открылся автоматически, используйте печать из меню браузера и выберите «Сохранить как PDF».</div>
<div class="page-shell">
  <header class="report-head">
    <div class="brand-row">
      <div class="brand"><div class="brand-mark">RF</div><span>Rafuk</span></div>
      <div class="report-meta"><div>AI market memo</div><div>${dateStr}${adId ? ` · ID ${escapeHtml(String(adId))}` : ""}</div></div>
    </div>
    <div class="hero-grid">
      <div>
        <div class="eyebrow">Kufar buyer intelligence</div>
        <h1>${escapeHtml(title)}</h1>
        ${price ? `<div class="hero-price mono">${escapeHtml(price)}</div>` : ""}
        ${link ? `<a class="hero-link" href="${_safeXmlUrl(link)}">${escapeHtml(link)}</a>` : ""}
      </div>
      ${listingImages.length ? `<div class="photo-strip">${listingImages.map(img => `<img src="${_safeXmlUrl(img)}" alt="" />`).join("")}</div>` : ""}
    </div>
  </header>

  ${verdictSection ? `<section class="verdict-card ${vInfo.cls}">
    <div class="verdict-icon">${vInfo.icon}</div>
    <div><div class="verdict-text">${escapeHtml(verdictLine)}</div>${verdictSummary ? `<div class="verdict-summary">${escapeHtml(verdictSummary)}</div>` : ""}</div>
  </section>` : ""}

  <section class="facts-grid">
    <div class="fact"><span class="fact-label">Цена</span><span class="fact-value mono">${price ? escapeHtml(price) : "—"}</span></div>
    <div class="fact"><span class="fact-label">Дата отчёта</span><span class="fact-value">${dateStr}</span></div>
    <div class="fact"><span class="fact-label">Объявление</span><span class="fact-value mono">${adId ? escapeHtml(String(adId)) : "—"}</span></div>
  </section>

  ${listingParams.length ? `<section class="params-strip">${listingParams.slice(0, 10).map(p => `<span class="param-chip"><b>${escapeHtml(p.label)}</b> ${escapeHtml(p.value)}</span>`).join("")}</section>` : ""}

  <main class="content">
${otherSections.map(s => {
    const isPrice = s.title === "Справедливая цена" || s.title === "Потенциал перепродажи";
    const isFlags = s.title === "Красные флаги";
    const sectionCls = isPrice ? " section--price" : isFlags ? " section--flags" : "";
    return `    <section class="section${sectionCls}">
      <div class="section-header"><div class="section-dot"></div><div class="section-title">${escapeHtml(s.title)}</div></div>
      <div class="section-body">${escapeHtml(s.body)}</div>
    </section>`;
}).join("\n")}

${data.best_alternative ? `    <section class="section section--best">
      <div class="section-header"><div class="section-dot"></div><div class="section-title">Лучший вариант</div></div>
      ${bestAltHtml}
    </section>` : ""}

${similarHtml}
  </main>

  <footer class="footer"><span class="footer-brand">Rafuk</span> — ${escapeHtml(data.disclaimer || "AI-анализ носит информационно-справочный характер и не является финансовой или инвестиционной консультацией.")}</footer>
</div>
</body>
</html>`;

        const isTelegram = !!window.Telegram?.WebApp?.initData;

        if (isTelegram) {
            try {
                const exportResp = await postJson("/api/v1/ai/export-report", { html: pdfHtml });
                const exportUrl = exportResp?.url;
                if (!exportUrl) {
                    throw new Error("Сервер не вернул ссылку на экспорт");
                }
                if (window.Telegram?.WebApp?.openLink) {
                    window.Telegram.WebApp.openLink(safeUrl(exportUrl));
                } else {
                    window.location.href = safeUrl(exportUrl);
                }
                return;
            } catch (err) {
                console.error("[AI] Telegram PDF export failed:", err);
            }
        }

        const blob = new Blob([pdfHtml], { type: "text/html" });
        const url = URL.createObjectURL(blob);
        const win = window.open(url, "_blank");
        if (win) {
            win.onload = function () { win.print(); };
        }
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    return {
        exportToPdf,
    };
}

app.createAiPdf = createAiPdf;
})(window.App = window.App || {});
