from __future__ import annotations

# LEGAL-02: "terms" is acceptance of the user agreement — the contract the
# core features process personal data under (Law 99-З, art. 6). It is
# ended by deleting the account, not revoked like a consent.
VALID_CONSENT_TYPES = {"ai_analysis", "pd_processing", "cross_border", "terms"}
CURRENT_POLICY_VERSION = "2026.4"
KNOWN_POLICY_VERSIONS = ("2026.1", "2026.2", "2026.3", "2026.4")
