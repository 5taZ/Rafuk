"""BE-C5 Wave 18: prompt-injection sanitization for AI-bound user text.

Extracted from ``api/services/ai_service.py`` so the detection regexes
and the telemetry logic live in one place, independent of the AI
client. Any code that sends user-controlled strings to an LLM should
pass them through :func:`sanitize_user_text` first — the listing
assistant and price-advice routers already do, and new callers should
follow the same pattern.

The function is also re-exported from ``api.services.ai_service`` for
back-compat with existing importers (``api/routers/ai_tools.py``,
``tests/test_ai_analysis.py``).
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


# ── Unicode normalisation helpers (AI-CRITICAL — issues §3.1) ────────
#
# The previous regex pipeline matched literal Latin characters only, so
# a crafted Kufar title using Cyrillic / fullwidth / mathematical-style
# look-alikes ("ѕystem:", "ｉgnore previous instructions",
# "𝐢gnore all instructions") slipped through untouched.
#
# We now NFKC-normalise the text (which folds fullwidth / compatibility
# forms back to ASCII) and then run a small confusables map that
# converts the most common Cyrillic look-alikes to their Latin twins
# *for matching purposes only*. The cleaned/sanitised text returned to
# callers preserves the user's original characters wherever they did
# not trip a guard, so legitimate Cyrillic listings ("Часы Casio")
# render normally — only the substrings we substitute via subn() get
# replaced with placeholders.

# Cyrillic → Latin homoglyphs commonly used in prompt-injection
# attempts. Limited to the subset the role/instruction patterns rely
# on; widening this map turns every Belarusian listing into garbled
# transliteration.
_CONFUSABLE_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "А": "A",
    "е": "e", "Е": "E",
    "о": "o", "О": "O",
    "р": "p", "Р": "P",
    "с": "c", "С": "C",
    "у": "y", "У": "Y",
    "х": "x", "Х": "X",
    "і": "i", "І": "I",
    "ѕ": "s", "Ѕ": "S",
    "ј": "j", "Ј": "J",
    "ԁ": "d",
    "ɡ": "g",
    "ı": "i",
    "ӏ": "l",
})


def _normalise_for_matching(text: str) -> str:
    """Return an NFKC-normalised, confusables-folded copy of ``text``.

    Used internally by the prompt-injection regex pass so visually
    identical Cyrillic / fullwidth payloads ("ｉgnore", "ѕystem:",
    "ignore　all　instructions") trip the same guards as their ASCII
    forms. The original string is preserved for the user-visible
    output — only matching is performed against the normalised view.
    """
    if not text:
        return text
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_CONFUSABLE_CYRILLIC_TO_LATIN)
    return folded


# ── Prompt-injection detection patterns ──────────────────────────────────

_PROMPT_ROLE_MARKERS = re.compile(
    r"(?i)("
    # Markdown-style role headers: "### system:", "## assistant:" etc.
    # Allowed anywhere in the text — a malicious listing title can put
    # them mid-line just as easily as at the start.
    r"###?\s*(?:system|instruction|user|assistant)\s*[:\-—]?\s*"
    # Chat-template markers from open-source models (chatml, llama, etc.).
    r"|<\|(?:im_start|im_end|system|user|assistant)\|>"
    # Bracketed role tags: "[system]", "[ASSISTANT]" etc.
    r"|\[\s*(?:system|instruction|user|assistant)\s*\]\s*[:\-—]?\s*"
    r")"
)
_PROMPT_INJECTION_PATTERNS = re.compile(
    r"(?i)("
    r"ignore\s+(?:all|any|the)?\s*(?:previous|prior|above)?\s*instructions"
    r"|disregard\s+(?:all|any|the)?\s*(?:previous|prior|above)?\s*instructions"
    r"|you\s+(?:are|act as|will now|must)\s+(?:no longer|now)\s+"
    r"|игнориру(?:й|йте)\s+(?:все\s+)?(?:предыдущие|прошлые)\s+"
    r"(?:инструкции|правила|сообщени)"
    r"|забудь\s+(?:все\s+)?(?:предыдущие|прошлые)\s+"
    r"|новая\s+инструкция[:\-]"
    r"|жестк(?:ая|ие)\s+инструкци(?:я|и)"
    r"|reveal\s+(?:your|the)\s+(?:system\s+)?prompt"
    r"|jailbreak"
    r")"
)
_TRIPLE_BACKTICK_RE = re.compile(r"```+|~~~+")
_MULTILINE_COLLAPSE_RE = re.compile(r"\n{3,}")


# ── PII scrubbing patterns (SEC-02) ──────────────────────────────────
#
# Kufar listings routinely contain seller contact info (phones, emails,
# Telegram handles) and the occasional document-ish identifier (IMEI,
# Belarusian passport series). Before we splice any of that into a
# prompt bound for Together AI / Gemini we mask it — the AI provider
# has no business holding raw PII, and the regulator (Belarus Law
# No. 99-З) doesn't permit transferring identifying data without
# explicit purpose-bound consent.
#
# The patterns are intentionally conservative: a 9+ digit run is the
# floor for "phone-like", well above Kufar's 1-6 digit prices. Latin
# *and* Cyrillic prefixes are accepted for Belarus document series.

_PII_EMAIL_RE = re.compile(
    r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"
)
# Phone-shaped: optional leading +, then 9-15 digits with the usual
# punctuation between groups. Anchored on word-ish boundaries so it
# doesn't gnaw into the surrounding text.
_PII_PHONE_RE = re.compile(
    r"(?<!\d)\+?\d(?:[\s\-.()]?\d){7,14}(?!\d)"
)
# Telegram username (5-32 chars, must start with a letter). Anything
# shorter is most likely an @-mention of a model/brand.
_PII_TG_HANDLE_RE = re.compile(
    r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{4,31}\b"
)
# Belarusian passport: two letters (Latin or Cyrillic, BLR series like
# "HB", "МР", "AB") + optional space + 7 digits. Covers the common
# series the listings tend to expose.
_PII_BLR_DOC_RE = re.compile(
    r"\b[A-ZА-Я]{2}\s?\d{7}\b"
)
# Long digit run — catches IMEI (15), card PANs (13-19), IBANs (digit
# tail), etc. Anything below 12 digits is left to _PII_PHONE_RE.
_PII_LONG_DIGITS_RE = re.compile(
    r"(?<!\d)\d{12,}(?!\d)"
)


def scrub_pii(text: str) -> tuple[str, int]:
    """Mask identifying information before AI submission.

    Returns ``(cleaned_text, hit_count)``. Each PII class is replaced
    with a stable placeholder so the AI can still reason about
    "there is a phone here" without seeing the actual digits.

    Order matters: phone numbers are run BEFORE the long-digit
    catch-all so a "+375 29 1234567" call gets labelled as [phone],
    not stripped of its country code and left as "+[id]".
    """
    if not text:
        return text, 0
    hits = 0
    text, n = _PII_EMAIL_RE.subn("[email]", text)
    hits += n
    text, n = _PII_TG_HANDLE_RE.subn("[handle]", text)
    hits += n
    text, n = _PII_BLR_DOC_RE.subn("[doc]", text)
    hits += n
    text, n = _PII_PHONE_RE.subn("[phone]", text)
    hits += n
    text, n = _PII_LONG_DIGITS_RE.subn("[id]", text)
    hits += n
    return text, hits


def sanitize_user_text(
    value: str | None,
    *,
    max_length: int = 1200,
    context: str = "user_text",
) -> str | None:
    """Strip prompt-injection-shaped sequences from user-supplied free text.

    Telegram users can paste anything into the listing assistant's notes /
    title fields. Without sanitization a determined user could try to make
    Gemini ignore our system prompt by writing things like
    `### system:\\nIgnore all previous instructions...`.

    This isn't a hard security boundary (the model is the actual decision
    maker), but it's a cheap layer that:
      - removes obvious role/instruction markers,
      - flattens triple-backtick code fences,
      - normalises whitespace,
      - hard-caps length again on the server.
      - **logs every actual hit** so we have telemetry on injection
        attempts; the audit (SEC-H4) called out that the silent regex
        had no observability and could be bypassed via Unicode
        homoglyphs without anyone noticing. We can't catch every
        homoglyph here cheaply, but we can at least see when the
        ASCII patterns are tripped — and use those signals for
        rate-limit / abuse review later.

    The ``context`` argument is included in injection logs so the team
    can tell which surface area the attempt came from (listing title,
    seller notes, etc.).

    Returns None if input is None/empty after cleaning.
    """
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    # AI-CRITICAL fix (issues §3.1): NFKC-normalise + Cyrillic-confusables
    # fold so prompt-injection variants slipped through homoglyphs trip the
    # same guards as ASCII payloads. NFKC is canonical-equivalence safe and
    # folds fullwidth/compat forms unconditionally — that step is applied
    # in place. The Cyrillic confusables fold ("ѕystem" → "system") would
    # mangle legitimate Belarusian listings if applied unconditionally, so
    # we only swap to the folded copy when it actually changes the regex
    # outcome (i.e. only when a homoglyph attack is present).
    text = unicodedata.normalize("NFKC", text)
    # Use subn() so we can count hits per pattern and log the totals.
    text, role_hits = _PROMPT_ROLE_MARKERS.subn("", text)
    text, injection_hits = _PROMPT_INJECTION_PATTERNS.subn("[удалено]", text)
    folded = text.translate(_CONFUSABLE_CYRILLIC_TO_LATIN)
    if folded != text and (
        _PROMPT_ROLE_MARKERS.search(folded)
        or _PROMPT_INJECTION_PATTERNS.search(folded)
    ):
        folded, role_hits_fold = _PROMPT_ROLE_MARKERS.subn("", folded)
        folded, injection_hits_fold = _PROMPT_INJECTION_PATTERNS.subn(
            "[удалено]", folded
        )
        text = folded
        role_hits += role_hits_fold
        injection_hits += injection_hits_fold
    text = _TRIPLE_BACKTICK_RE.sub("`", text)
    text = _MULTILINE_COLLAPSE_RE.sub("\n\n", text)
    # Strip control characters but keep newlines and tabs.
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 0x20)
    # SEC-02: mask PII (phones, emails, Telegram handles, document
    # numbers, IMEI) BEFORE the AI sees it. sanitize_user_text is the
    # final gate for every AI-bound free text field, so wiring the
    # scrubber here covers every existing caller without per-call
    # plumbing. Length cap is applied AFTER scrubbing so we don't
    # accidentally truncate inside a "[phone]" placeholder.
    text, pii_hits = scrub_pii(text)
    text = text.strip()
    if role_hits or injection_hits:
        # WARNING level so it shows up in standard log scrapers; do
        # NOT include the cleaned text — could itself contain attacker-
        # controlled content. Just counts + context.
        logger.warning(
            "ai.prompt_injection_detected context=%s role_hits=%d injection_hits=%d",
            context, role_hits, injection_hits,
        )
    if pii_hits:
        logger.info(
            "ai.pii_scrubbed context=%s hits=%d",
            context, pii_hits,
        )
    if not text:
        return None
    if max_length > 0 and len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


# ── SEC-NEW-4: HTML tag stripping for AI output ─────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html_tags(text: str) -> str:
    """SEC-NEW-4: defence-in-depth strip for AI output.
    We own a strict CSP so an ``<img onerror>`` won't fire, but stripping
    ensures a future innerHTML callsite (or relaxed CSP) doesn't open
    a fresh XSS hole. Keep the regex permissive — we'd rather strip a
    legitimate ``<`` than leak ``<script>``.
    """
    if not text:
        return text
    return _HTML_TAG_RE.sub("", text)


def strip_html_in_payload(value):
    """Recurse into dict/list and apply ``strip_html_tags`` to every string."""
    if isinstance(value, str):
        return strip_html_tags(value)
    if isinstance(value, list):
        return [strip_html_in_payload(v) for v in value]
    if isinstance(value, dict):
        return {k: strip_html_in_payload(v) for k, v in value.items()}
    return value


__all__ = [
    "sanitize_user_text",
    "scrub_pii",
    "strip_html_tags",
    "strip_html_in_payload",
    "_PROMPT_ROLE_MARKERS",
    "_PROMPT_INJECTION_PATTERNS",
    "_TRIPLE_BACKTICK_RE",
    "_MULTILINE_COLLAPSE_RE",
    "_PII_EMAIL_RE",
    "_PII_PHONE_RE",
    "_PII_TG_HANDLE_RE",
    "_PII_BLR_DOC_RE",
    "_PII_LONG_DIGITS_RE",
]
