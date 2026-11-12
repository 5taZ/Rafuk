// LEGAL-01: the public terms/privacy pages take the operator's details from
// the server configuration, so the documents never drift from .env.
(() => {
    "use strict";

    function setText(root, selector, value) {
        if (!value) return;
        for (const node of root.querySelectorAll(selector)) node.textContent = value;
    }

    function fillLegalDetails(root, info) {
        setText(root, '[data-operator="name"]', info.operator_name);
        setText(root, '[data-operator="address"]', info.operator_address);
        setText(root, "[data-policy-version]", info.policy_version);
        setText(root, "[data-ai-provider]", info.ai_provider);
        const days = Number(info.inactive_account_retention_days);
        setText(root, "[data-retention-days]", days > 0 ? String(days) : "без ограничения");
        if (info.operator_email) {
            for (const node of root.querySelectorAll('[data-operator="email"]')) {
                const link = document.createElement("a");
                link.href = `mailto:${info.operator_email}`;
                link.textContent = info.operator_email;
                node.replaceChildren(link);
            }
        }
    }

    fetch("/api/v1/legal/operator", { credentials: "same-origin" })
        .then((response) => (response.ok ? response.json() : null))
        .then((info) => { if (info) fillLegalDetails(document, info); })
        .catch(() => {});
})();
