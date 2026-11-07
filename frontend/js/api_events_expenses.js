function _bindExpenseEvents(ctx) {
    ctx.elements.expensesClose?.addEventListener("click", () => {
        ctx.closeExpensesModal();
    });

    ctx.elements.expensesOverlay?.addEventListener("click", () => {
        ctx.closeExpensesModal();
    });

    ctx.elements.saveExpenseButton?.addEventListener("click", () => {
        const leadId = ctx.state.expenses.currentLeadId;
        if (!leadId) return;
        const type = ctx.elements.expenseTypeSelect?.value || "other";
        const rawAmount = ctx.elements.expenseAmountInput?.value?.trim();
        const displayAmount = rawAmount ? Number(rawAmount) : null;
        const notes = ctx.elements.expenseNotesInput?.value?.trim() || "";
        if (!Number.isFinite(displayAmount) || displayAmount <= 0 || displayAmount > 10000000 || !ctx.elements.expenseAmountInput.validity.valid) {
            ctx.showToast("Введите корректную сумму");
            return;
        }

        const button = ctx.elements.saveExpenseButton;
        button.disabled = true;
        button.classList.add('is-loading');
        const originalText = button.textContent;
        button.textContent = 'Сохраняю...';
        void (async () => {
            try {
                const saved = await ctx.createExpense(leadId, { expense_type: type, amount_byn: displayAmount, notes });
                if (saved && ctx.state.expenses.currentLeadId === leadId) {
                    if (ctx.elements.expenseAmountInput) ctx.elements.expenseAmountInput.value = "";
                    if (ctx.elements.expenseNotesInput) ctx.elements.expenseNotesInput.value = "";
                }
            } finally {
                button.disabled = false;
                button.classList.remove('is-loading');
                button.textContent = originalText;
            }
        })().catch((err) => { ctx.logClientError("save expense failed", err); });
    });

    ctx.elements.cancelExpenseButton?.addEventListener("click", () => {
        ctx.closeExpensesModal();
    });

    const dateInput = document.getElementById("reminder-datetime-input");
    const messageInput = document.getElementById("reminder-message-input");
    const reminderButton = document.getElementById("save-reminder-btn");
    for (const button of document.querySelectorAll("[data-reminder-offset]")) {
        button.addEventListener("click", () => {
            const date = new Date();
            date.setDate(date.getDate() + Number.parseInt(button.dataset.reminderOffset, 10));
            date.setMinutes(0, 0, 0);
            const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
            dateInput.value = local.toISOString().slice(0, 16);
            dateInput.removeAttribute("aria-invalid");
        });
    }
    reminderButton?.addEventListener("click", async () => {
        const leadId = ctx.state.reminders.leadId;
        if (!leadId || reminderButton.disabled) return;
        const date = new Date(dateInput.value);
        if (!dateInput.value || !Number.isFinite(date.getTime()) || date <= new Date() || date.getTime() > Date.now() + 366 * 86400000) {
            dateInput.setAttribute("aria-invalid", "true");
            dateInput.focus();
            ctx.showToast("Выберите будущее время в пределах года", "error");
            return;
        }
        dateInput.removeAttribute("aria-invalid");
        reminderButton.disabled = true;
        reminderButton.textContent = "Сохраняем…";
        try {
            const saved = await ctx.createReminder(leadId, { remind_at: date.toISOString(), message: messageInput.value.trim() || null });
            if (saved && ctx.state.reminders.leadId === leadId) { dateInput.value = ""; messageInput.value = ""; }
        } finally { reminderButton.disabled = false; reminderButton.textContent = "Добавить"; }
    });
}
