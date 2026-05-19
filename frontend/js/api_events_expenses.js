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
        if (!displayAmount || displayAmount <= 0) {
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
                await ctx.createExpense(leadId, { expense_type: type, amount_byn: displayAmount, notes });
                if (ctx.elements.expenseAmountInput) ctx.elements.expenseAmountInput.value = "";
                if (ctx.elements.expenseNotesInput) ctx.elements.expenseNotesInput.value = "";
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
}
