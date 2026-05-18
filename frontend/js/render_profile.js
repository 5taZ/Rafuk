function createRenderProfile(context) {
    const {
        state,
        elements,
        formatDate,
        safeRender,
        domEl,
        domClear,
    } = context;

    const ACCENTS = new Set(["gray", "blue", "violet", "amber", "red"]);

    function _accent(value) {
        const raw = String(value || "gray");
        return ACCENTS.has(raw) ? raw : "gray";
    }

    function _formatReset(value) {
        if (!value) return "—";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "—";
        return date.toLocaleTimeString("ru-BY", { hour: "2-digit", minute: "2-digit" });
    }

    function _formatDate(value) {
        return formatDate(value) || "—";
    }

    function _limitCard(title, bucket) {
        const limit = Number(bucket?.limit || 0);
        const used = Number(bucket?.used || 0);
        const remaining = Number(bucket?.remaining || 0);
        const track = domEl(
            "progress",
            {
                className: "quota-progress",
                value: Math.min(used, limit),
                attrs: { max: Math.max(1, limit), "aria-label": `${title}: ${used} из ${limit}` },
            },
        );
        return domEl(
            "article",
            { className: "profile-limit-card" },
            domEl(
                "div",
                { className: "profile-limit-head" },
                domEl("span", { className: "profile-limit-title", text: title }),
                domEl("strong", { className: "profile-limit-count mono", text: `${used} / ${limit}` }),
            ),
            track,
            domEl(
                "div",
                { className: "profile-limit-meta" },
                domEl("span", { text: `${remaining} осталось` }),
                domEl("span", { text: `сброс в ${_formatReset(bucket?.resets_at)}` }),
            ),
        );
    }

    function _permission(label, enabled) {
        return domEl(
            "li",
            { className: `permission-row ${enabled ? "is-on" : "is-off"}` },
            domEl("span", { className: "permission-dot", attrs: { "aria-hidden": "true" } }),
            domEl("span", { text: label }),
        );
    }

    function _renderChip() {
        const chip = elements.profileChip;
        const label = elements.profileChipLabel;
        if (!chip || !label) return;
        const profile = state.profile.data;
        chip.className = "profile-chip";
        chip.removeAttribute("aria-busy");
        chip.removeAttribute("data-accent");
        if (!profile && (state.profile.loading || !state.profile.error)) {
            chip.classList.add("profile-chip--loading");
            chip.setAttribute("aria-busy", "true");
            label.textContent = "Профиль";
            return;
        }
        if (!profile) {
            chip.classList.add("profile-chip--muted");
            label.textContent = "Профиль";
            return;
        }
        const status = profile.status || {};
        const permissions = profile.permissions || {};
        const aiRemaining = Number(profile.limits?.ai?.remaining || 0);
        const parts = [status.display_name || "Профиль"];
        if (permissions.is_admin) {
            parts.push("Admin");
        } else if (status.code !== "bare_search" && Number(profile.limits?.ai?.limit || 0) > 0) {
            parts.push(`${aiRemaining} AI`);
        }
        chip.dataset.accent = _accent(status.accent);
        label.textContent = parts.join(" · ");
    }

    function _renderAiLocks() {
        const profile = state.profile.data;
        if (!profile) return;
        const canUseAi = profile.permissions?.can_use_ai === true;
        const canUseAssistant = profile.permissions?.can_use_assistant === true;
        if (elements.detailAiBtn) {
            elements.detailAiBtn.classList.toggle("is-ai-locked", !canUseAi);
            elements.detailAiBtn.dataset.aiLocked = canUseAi ? "false" : "true";
            elements.detailAiBtn.textContent = canUseAi ? "Анализ" : "AI закрыт";
            elements.detailAiBtn.setAttribute(
                "aria-label",
                canUseAi ? "AI анализ объявления" : "AI закрыт — откройте профиль",
            );
        }
        const assistantBtn = elements.listingAssistantOpenButton;
        const assistantSection = elements.listingAssistantSection;
        if (assistantBtn) {
            assistantBtn.classList.toggle("is-ai-locked", !canUseAssistant);
            assistantBtn.dataset.aiLocked = canUseAssistant ? "false" : "true";
            assistantBtn.textContent = canUseAssistant ? "Открыть помощника" : "AI закрыт — открыть профиль";
            assistantBtn.setAttribute(
                "aria-label",
                canUseAssistant ? "Открыть AI помощника продавца" : "AI помощник закрыт — откройте профиль",
            );
        }
        if (assistantSection) assistantSection.classList.toggle("ai-feature-locked", !canUseAssistant);
    }

    function _renderSkeleton() {
        return domEl(
            "div",
            { className: "profile-skeleton", attrs: { role: "status", "aria-live": "polite" } },
            domEl("span", { className: "sr-only", text: "Загружаю профиль" }),
            domEl("div", { className: "profile-skeleton-hero" }),
            domEl("div", { className: "profile-skeleton-grid" },
                domEl("span"),
                domEl("span"),
            ),
            domEl("div", { className: "profile-skeleton-line" }),
        );
    }

    function _renderError() {
        return domEl(
            "div",
            { className: "profile-empty" },
            domEl("h2", { className: "profile-view-title", text: "Профиль не загрузился" }),
            domEl("p", { className: "profile-view-copy", text: state.profile.error || "Попробуйте обновить профиль." }),
            domEl("button", { className: "primary-btn", type: "button", text: "Повторить", dataset: { profileAction: "retry" } }),
            domEl("button", { className: "ghost-btn", type: "button", text: "К поиску", dataset: { profileAction: "overview" } }),
        );
    }

    function _renderProfileView() {
        if (!elements.profileContent) return;
        domClear(elements.profileContent);
        if (state.profile.loading && !state.profile.data) {
            elements.profileContent.appendChild(_renderSkeleton());
            return;
        }
        const profile = state.profile.data;
        if (!profile) {
            elements.profileContent.appendChild(_renderError());
            return;
        }
        const status = profile.status || {};
        const permissions = profile.permissions || {};
        const accent = _accent(status.accent);
        const isBare = status.code === "bare_search" || permissions.can_use_ai !== true;
        const heroMeta = [
            `Выдано: ${_formatDate(status.granted_at)}`,
            status.expires_at ? `до ${_formatDate(status.expires_at)}` : "без срока",
        ];
        const user = profile.user || {};
        const displayName = user.username ? `@${user.username}` : (user.first_name || `TG ${user.telegram_user_id || ""}`.trim());

        const shell = domEl(
            "div",
            { className: "profile-shell" },
            domEl(
                "section",
                { className: `profile-hero profile-hero--${accent}` },
                domEl("span", { className: "profile-kicker", text: displayName }),
                domEl("h2", { className: "profile-view-title", attrs: { id: "profile-view-title" }, text: status.display_name || "Профиль" }),
                domEl("p", { className: "profile-view-copy", text: status.tagline || "Твой режим доступа в Rafuk." }),
                domEl(
                    "div",
                    { className: "profile-hero-meta" },
                    heroMeta.map((item) => domEl("span", { text: item })),
                ),
            ),
            domEl(
                "section",
                { className: "profile-section" },
                domEl("h3", { className: "profile-section-title", text: "Лимиты сегодня" }),
                domEl(
                    "div",
                    { className: "profile-limits-grid" },
                    _limitCard("AI-анализ", profile.limits?.ai),
                    _limitCard("AI-помощник продавца", profile.limits?.assistant),
                ),
            ),
            domEl(
                "section",
                { className: "profile-section" },
                domEl("h3", { className: "profile-section-title", text: "Что доступно" }),
                domEl(
                    "ul",
                    { className: "permissions-list" },
                    _permission("Обычный поиск", true),
                    _permission("Объявления", true),
                    _permission("Автопоиск", true),
                    _permission("Мои объявления", true),
                    _permission("AI-функции", permissions.can_use_ai === true || permissions.can_use_assistant === true),
                ),
            ),
        );

        if (isBare) {
            shell.appendChild(domEl(
                "section",
                { className: "profile-locked" },
                domEl("span", { className: "profile-locked-kicker", text: "AI закрыт" }),
                domEl("h3", { className: "profile-locked-title", text: "AI доступен со статуса Скаут Барахолки" }),
                domEl("p", { className: "profile-locked-copy", text: "Попроси доступ у владельца — обычный поиск, объявления и автопоиск остаются доступными." }),
            ));
        }

        const actions = domEl(
            "div",
            { className: "profile-actions" },
            domEl("button", { className: "ghost-btn", type: "button", text: "К поиску", dataset: { profileAction: "overview" } }),
        );
        if (permissions.is_admin === true) {
            actions.appendChild(domEl("button", {
                className: "primary-btn profile-admin-btn",
                type: "button",
                text: "Админка",
                dataset: { profileAction: "admin" },
            }));
        }
        shell.appendChild(actions);
        elements.profileContent.appendChild(shell);
    }

    function renderProfile() {
        return safeRender("renderProfile", () => {
            _renderChip();
            _renderAiLocks();
            _renderProfileView();
        });
    }

    return {
        renderProfile,
    };
}
