function createRenderAdmin(context) {
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

    function _isAdmin() {
        return state.profile.data?.permissions?.is_admin === true;
    }

    function _date(value) {
        return formatDate(value) || "—";
    }

    function _datetimeLocalValue(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 16);
    }

    function _statusBadge(status) {
        return domEl(
            "span",
            { className: `account-status-badge account-status-badge--${_accent(status?.accent)}` },
            domEl("span", { className: "account-status-dot", attrs: { "aria-hidden": "true" } }),
            domEl("span", { text: status?.display_name || status?.code || "—" }),
        );
    }

    function _statusOptions(currentCode) {
        const statuses = state.admin.statuses || [];
        if (!statuses.length && currentCode) {
            return [domEl("option", { value: currentCode, text: currentCode, attrs: { selected: true } })];
        }
        return statuses.map((status) => domEl("option", {
            value: status.code,
            text: status.display_name,
            attrs: { selected: status.code === currentCode },
        }));
    }

    function _quotaText(bucket) {
        return `${Number(bucket?.used || 0)} / ${Number(bucket?.limit || 0)}`;
    }

    function _userIdentity(user) {
        const username = user.username ? `@${user.username}` : "";
        const firstName = user.first_name || "";
        if (username && firstName) return `${username} / ${firstName}`;
        return username || firstName || "Без имени";
    }

    function _userEditor(user) {
        const saving = state.admin.savingUserIds.has(String(user.telegram_user_id));
        return domEl(
            "div",
            { className: "admin-user-editor" },
            domEl(
                "label",
                { className: "admin-field" },
                domEl("span", { className: "admin-field-label", text: "Новый статус" }),
                domEl(
                    "select",
                    { className: "admin-input", dataset: { adminUserStatus: "" } },
                    _statusOptions(user.status?.code),
                ),
            ),
            domEl(
                "label",
                { className: "admin-field" },
                domEl("span", { className: "admin-field-label", text: "Истекает" }),
                domEl("input", {
                    className: "admin-input",
                    type: "datetime-local",
                    value: _datetimeLocalValue(user.status?.expires_at),
                    dataset: { adminUserExpires: "" },
                }),
            ),
            domEl(
                "label",
                { className: "admin-field admin-field--wide" },
                domEl("span", { className: "admin-field-label", text: "Заметка" }),
                domEl("textarea", {
                    className: "admin-input admin-textarea",
                    value: user.status_note || "",
                    attrs: { maxlength: "512", rows: "3", placeholder: "Почему выдан статус" },
                    dataset: { adminUserNote: "" },
                }),
            ),
            domEl(
                "div",
                { className: "admin-editor-actions" },
                domEl("button", {
                    className: "primary-btn small",
                    type: "button",
                    text: saving ? "Сохраняю…" : "Сохранить",
                    attrs: { disabled: saving },
                    dataset: { adminSaveUser: "" },
                }),
                domEl("button", {
                    className: "ghost-btn small",
                    type: "button",
                    text: "Отмена",
                    attrs: { disabled: saving },
                    dataset: { adminCancelUser: "" },
                }),
            ),
        );
    }

    function _userCard(user) {
        const editing = String(state.admin.editingUserId || "") === String(user.telegram_user_id);
        const saving = state.admin.savingUserIds.has(String(user.telegram_user_id));
        const card = domEl(
            "article",
            {
                className: "admin-user-card",
                attrs: { role: "listitem" },
                dataset: { adminUserCard: "", telegramUserId: user.telegram_user_id },
            },
            domEl(
                "div",
                { className: "admin-user-top" },
                domEl(
                    "div",
                    { className: "admin-user-main" },
                    domEl("strong", { className: "admin-user-name", text: _userIdentity(user) }),
                    domEl("span", { className: "admin-user-id mono", text: `TG ${user.telegram_user_id}` }),
                ),
                _statusBadge(user.status),
            ),
            domEl(
                "div",
                { className: "admin-user-grid" },
                domEl("span", { text: "AI today" }),
                domEl("strong", { className: "mono", text: _quotaText(user.limits?.ai) }),
                domEl("span", { text: "Assistant today" }),
                domEl("strong", { className: "mono", text: _quotaText(user.limits?.assistant) }),
                domEl("span", { text: "Создан" }),
                domEl("strong", { text: _date(user.created_at) }),
                domEl("span", { text: "Был" }),
                domEl("strong", { text: _date(user.last_seen_at) }),
            ),
        );
        if (editing) {
            card.appendChild(_userEditor(user));
        } else {
            card.appendChild(domEl(
                "div",
                { className: "admin-card-actions" },
                domEl("button", {
                    className: "ghost-btn small",
                    type: "button",
                    text: saving ? "Сохраняю…" : "Изменить статус",
                    attrs: { disabled: saving },
                    dataset: { adminEditUser: "" },
                }),
            ));
        }
        return card;
    }

    function _emptyAdmin(title, copy) {
        return domEl(
            "div",
            { className: "admin-empty" },
            domEl("strong", { text: title }),
            domEl("span", { text: copy }),
        );
    }

    function renderAdminUsers() {
        return safeRender("renderAdminUsers", () => {
            if (elements.adminUserQuery && document.activeElement !== elements.adminUserQuery) {
                elements.adminUserQuery.value = state.admin.query || "";
            }
            if (!elements.adminUsersList || !elements.adminUsersState) return;
            domClear(elements.adminUsersList);
            if (!_isAdmin()) {
                elements.adminUsersState.textContent = "";
                elements.adminUsersList.appendChild(_emptyAdmin("Админка скрыта", "Открой профиль администратора, чтобы управлять пользователями."));
                return;
            }
            if (state.admin.loading) {
                elements.adminUsersState.textContent = "Загружаю пользователей…";
                for (let i = 0; i < 3; i++) {
                    elements.adminUsersList.appendChild(domEl("div", { className: "admin-skeleton-card" }));
                }
                return;
            }
            if (state.admin.error) {
                elements.adminUsersState.textContent = state.admin.error;
                return;
            }
            const users = state.admin.users || [];
            elements.adminUsersState.textContent = users.length ? `${users.length} пользователей` : "";
            if (!users.length) {
                elements.adminUsersList.appendChild(_emptyAdmin("Никого не нашли", "Проверь TG ID, username, имя или статусный фильтр."));
                return;
            }
            for (const user of users) elements.adminUsersList.appendChild(_userCard(user));
        });
    }

    function _renderStatusFilterOptions() {
        if (!elements.adminStatusFilter) return;
        const current = state.admin.selectedStatus || "";
        const fragment = document.createDocumentFragment();
        fragment.appendChild(domEl("option", { value: "", text: "Все статусы" }));
        for (const status of state.admin.statuses || []) {
            fragment.appendChild(domEl("option", {
                value: status.code,
                text: status.display_name,
                attrs: { selected: status.code === current },
            }));
        }
        elements.adminStatusFilter.replaceChildren(fragment);
        elements.adminStatusFilter.value = current;
    }

    function _statusCard(status) {
        const saving = state.admin.savingStatusCodes.has(status.code);
        return domEl(
            "article",
            {
                className: "admin-status-card",
                attrs: { role: "listitem" },
                dataset: { adminStatusCard: "", statusCode: status.code },
            },
            domEl(
                "div",
                { className: "admin-status-card-head" },
                domEl(
                    "div",
                    { className: "admin-status-title-wrap" },
                    _statusBadge(status),
                    domEl("span", { className: "admin-status-code mono", text: status.code }),
                ),
                domEl("span", { className: "admin-status-order mono", text: `#${Number(status.sort_order || 0)}` }),
            ),
            domEl("p", { className: "admin-status-tagline", text: status.tagline || "" }),
            domEl(
                "div",
                { className: "admin-limits-form" },
                domEl(
                    "label",
                    { className: "admin-field" },
                    domEl("span", { className: "admin-field-label", text: "AI / день" }),
                    domEl("input", {
                        className: "admin-input mono",
                        type: "number",
                        value: Number(status.ai_daily_limit || 0),
                        attrs: { min: "0", max: "10000", step: "1", inputmode: "numeric" },
                        dataset: { adminStatusAi: "" },
                    }),
                ),
                domEl(
                    "label",
                    { className: "admin-field" },
                    domEl("span", { className: "admin-field-label", text: "Помощник / день" }),
                    domEl("input", {
                        className: "admin-input mono",
                        type: "number",
                        value: Number(status.assistant_daily_limit || 0),
                        attrs: { min: "0", max: "10000", step: "1", inputmode: "numeric" },
                        dataset: { adminStatusAssistant: "" },
                    }),
                ),
                domEl("button", {
                    className: "primary-btn small admin-status-save",
                    type: "button",
                    text: saving ? "Сохраняю…" : "Сохранить",
                    attrs: { disabled: saving },
                    dataset: { adminSaveStatus: "" },
                }),
            ),
        );
    }

    function renderAdminStatuses() {
        return safeRender("renderAdminStatuses", () => {
            _renderStatusFilterOptions();
            if (!elements.adminStatusesList || !elements.adminStatusesState) return;
            domClear(elements.adminStatusesList);
            if (!_isAdmin()) {
                elements.adminStatusesState.textContent = "";
                return;
            }
            if (state.admin.statusesLoading) {
                elements.adminStatusesState.textContent = "Загружаю статусы…";
                for (let i = 0; i < 2; i++) {
                    elements.adminStatusesList.appendChild(domEl("div", { className: "admin-skeleton-card" }));
                }
                return;
            }
            if (state.admin.statusesError) {
                elements.adminStatusesState.textContent = state.admin.statusesError;
                return;
            }
            const statuses = state.admin.statuses || [];
            elements.adminStatusesState.textContent = statuses.length ? `${statuses.length} статусов` : "";
            if (!statuses.length) {
                elements.adminStatusesList.appendChild(_emptyAdmin("Статусы не загружены", "Обнови список статусов."));
                return;
            }
            for (const status of statuses) elements.adminStatusesList.appendChild(_statusCard(status));
        });
    }

    return {
        renderAdminUsers,
        renderAdminStatuses,
    };
}
