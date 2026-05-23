function createApiProfile(context) {
    const {
        state,
        hasTelegramInitData,
        getJson,
        markDirty,
        renderAll,
        showToast,
    } = context;

    async function loadProfile(options = {}) {
        if (!hasTelegramInitData()) {
            state.profile.loading = false;
            state.profile.error = "Профиль доступен внутри Telegram Mini App.";
            markDirty("profile");
            renderAll();
            return null;
        }
        state.profile.loading = true;
        state.profile.error = "";
        markDirty("profile");
        renderAll();
        try {
            const data = await getJson("/api/v1/profile/me", { retry: options.retry !== false });
            state.profile.data = data;
            state.profile.error = "";
            return data;
        } catch (error) {
            state.profile.error = error.message || "Не удалось загрузить профиль";
            if (!options.silent) showToast(state.profile.error, "error");
            return null;
        } finally {
            state.profile.loading = false;
            markDirty("profile");
            renderAll();
        }
    }

    return {
        loadProfile,
    };
}
