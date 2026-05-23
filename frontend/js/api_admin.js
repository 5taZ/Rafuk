function createApiAdmin(context) {
    const {
        state,
        getJson,
        requestJson,
        markDirty,
        renderAll,
        showToast,
    } = context;

    let _usersRequestId = 0;

    function _isAdmin() {
        return state.profile.data?.permissions?.is_admin === true;
    }

    function _adminUsersUrl() {
        const query = new URLSearchParams();
        if (state.admin.query) query.set("query", state.admin.query);
        if (state.admin.selectedStatus) query.set("status", state.admin.selectedStatus);
        query.set("limit", "50");
        query.set("offset", "0");
        return `/api/v1/admin/users?${query.toString()}`;
    }

    async function loadAdminUsers(options = {}) {
        if (!_isAdmin()) return [];
        const requestId = ++_usersRequestId;
        state.admin.loading = true;
        state.admin.error = "";
        markDirty("adminUsers");
        renderAll();
        try {
            const users = await getJson(_adminUsersUrl(), { retry: options.retry !== false });
            if (requestId !== _usersRequestId) return state.admin.users;
            state.admin.users = Array.isArray(users) ? users : [];
            state.admin.error = "";
            return state.admin.users;
        } catch (error) {
            if (requestId !== _usersRequestId) return state.admin.users;
            state.admin.error = error.message || "Не удалось загрузить пользователей";
            if (!options.silent) showToast(state.admin.error, "error");
            return [];
        } finally {
            if (requestId === _usersRequestId) {
                state.admin.loading = false;
                markDirty("adminUsers");
                renderAll();
            }
        }
    }

    async function loadAdminStatuses(options = {}) {
        if (!_isAdmin()) return [];
        state.admin.statusesLoading = true;
        state.admin.statusesError = "";
        markDirty("adminStatuses");
        renderAll();
        try {
            const statuses = await getJson("/api/v1/admin/statuses", { retry: options.retry !== false });
            state.admin.statuses = Array.isArray(statuses)
                ? statuses.slice().sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
                : [];
            state.admin.statusesError = "";
            return state.admin.statuses;
        } catch (error) {
            state.admin.statusesError = error.message || "Не удалось загрузить статусы";
            if (!options.silent) showToast(state.admin.statusesError, "error");
            return [];
        } finally {
            state.admin.statusesLoading = false;
            markDirty("adminStatuses", "adminUsers");
            renderAll();
        }
    }

    async function updateUserStatus(telegramUserId, payload) {
        if (!_isAdmin()) return null;
        const key = String(telegramUserId);
        state.admin.savingUserIds.add(key);
        markDirty("adminUsers");
        renderAll();
        try {
            const updated = await requestJson(`/api/v1/admin/users/${encodeURIComponent(key)}/status`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            state.admin.editingUserId = null;
            showToast("Статус пользователя обновлён", "success", 1800);
            await loadAdminUsers({ silent: true });
            if (String(state.profile.data?.user?.telegram_user_id || "") === key && typeof context.loadProfile === "function") {
                await context.loadProfile({ silent: true });
            }
            return updated;
        } catch (error) {
            showToast(error.message || "Не удалось изменить статус", "error");
            return null;
        } finally {
            state.admin.savingUserIds.delete(key);
            markDirty("adminUsers");
            renderAll();
        }
    }

    async function updateStatusLimits(code, payload) {
        if (!_isAdmin()) return null;
        state.admin.savingStatusCodes.add(code);
        markDirty("adminStatuses");
        renderAll();
        try {
            const updated = await requestJson(`/api/v1/admin/statuses/${encodeURIComponent(code)}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            showToast("Лимиты статуса сохранены", "success", 1800);
            await Promise.all([
                loadAdminStatuses({ silent: true }),
                loadAdminUsers({ silent: true }),
                typeof context.loadProfile === "function" ? context.loadProfile({ silent: true }) : null,
            ]);
            return updated;
        } catch (error) {
            showToast(error.message || "Не удалось сохранить лимиты", "error");
            return null;
        } finally {
            state.admin.savingStatusCodes.delete(code);
            markDirty("adminStatuses");
            renderAll();
        }
    }

    async function loadAdminAudit(options = {}) {
        if (!_isAdmin()) return [];
        state.admin.auditLoading = true;
        state.admin.auditError = "";
        markDirty("adminAudit");
        renderAll();
        try {
            const params = new URLSearchParams();
            params.set("limit", "50");
            params.set("offset", String(state.admin.auditOffset || 0));
            const entries = await getJson(`/api/v1/admin/audit?${params.toString()}`, { retry: options.retry !== false });
            const list = Array.isArray(entries) ? entries : [];
            if (state.admin.auditOffset > 0) {
                state.admin.auditEntries = (state.admin.auditEntries || []).concat(list);
            } else {
                state.admin.auditEntries = list;
            }
            state.admin.auditHasMore = list.length >= 50;
            state.admin.auditError = "";
            return state.admin.auditEntries;
        } catch (error) {
            state.admin.auditError = error.message || "Не удалось загрузить аудит";
            if (!options.silent) showToast(state.admin.auditError, "error");
            return [];
        } finally {
            state.admin.auditLoading = false;
            markDirty("adminAudit");
            renderAll();
        }
    }

    return {
        loadAdminUsers,
        updateUserStatus,
        loadAdminStatuses,
        updateStatusLimits,
        loadAdminAudit,
    };
}
