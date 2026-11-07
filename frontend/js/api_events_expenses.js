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

    _bindReminderEvents(ctx);
}

function _bindReminderEvents(ctx) {
    const { elements, state } = ctx;
    const modal = elements.remindersModal;
    if (!modal) return;
    const dateInput = elements.reminderDateInput;
    const timeInput = elements.reminderTimeInput;
    const messageInput = elements.reminderMessageInput;
    const button = elements.saveReminderButton;
    const error = elements.reminderFormError;
    const feedback = elements.reminderSubmitStatus;
    const chips = modal.querySelectorAll("[data-reminder-offset]");
    elements.remindersClose?.addEventListener("click", () => ctx.closeRemindersModal());
    elements.remindersOverlay?.addEventListener("click", () => ctx.closeRemindersModal());
    function clearFeedback() {
        error.hidden = true;
        feedback.hidden = true;
        dateInput.removeAttribute("aria-invalid");
        timeInput.removeAttribute("aria-invalid");
    }
    function syncChips() {
        for (const chip of chips) {
            const date = new Date();
            date.setDate(date.getDate() + Number.parseInt(chip.dataset.reminderOffset, 10));
            const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
            chip.setAttribute("aria-pressed", String(dateInput.value === local.toISOString().slice(0, 10)));
        }
    }
    elements.reminderForm.addEventListener("input", clearFeedback);
    dateInput.addEventListener("change", syncChips);
    for (const chip of chips) {
        chip.addEventListener("click", () => {
            clearFeedback();
            const date = new Date();
            date.setDate(date.getDate() + Number.parseInt(chip.dataset.reminderOffset, 10));
            const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
            dateInput.value = local.toISOString().slice(0, 10);
            if (!timeInput.value) timeInput.value = "10:00";
            syncChips();
        });
    }
    elements.reminderForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const leadId = state.reminders.leadId;
        const sessionId = state.reminders.sessionId;
        if (!leadId || button.disabled) return;
        clearFeedback();
        const invalid = [dateInput, timeInput].find(input => !input.value || !input.validity.valid);
        const date = new Date(`${dateInput.value}T${timeInput.value}`);
        if (invalid || !Number.isFinite(date.getTime()) || date <= new Date() || date.getTime() > Date.now() + 366 * 86400000) {
            const target = invalid || timeInput;
            error.textContent = invalid ? "Укажите дату и время напоминания." : "Выберите будущее время в пределах года.";
            error.hidden = false;
            target.setAttribute("aria-invalid", "true");
            target.focus();
            return;
        }
        elements.reminderFields.disabled = true;
        button.disabled = true;
        button.textContent = "Добавляем…";
        try {
            const saved = await ctx.createReminder(leadId, { remind_at: date.toISOString(), message: messageInput.value.trim() || null });
            if (saved && state.reminders.leadId === leadId && state.reminders.sessionId === sessionId) {
                messageInput.value = "";
                feedback.textContent = "Напоминание добавлено.";
                feedback.hidden = false;
                const card = modal.querySelector(`[data-reminder-id="${saved.id}"]`);
                card?.focus({ preventScroll: true });
                card?.scrollIntoView({ block: "nearest", behavior: _prefersReducedMotion() ? "auto" : "smooth" });
            }
        } finally {
            elements.reminderFields.disabled = false;
            button.disabled = false;
            button.textContent = "Добавить напоминание";
        }
    });
}
