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

logger = logging.getLogger(__name__)


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
    # Use subn() so we can count hits per pattern and log the totals.
    text, role_hits = _PROMPT_ROLE_MARKERS.subn("", text)
    text, injection_hits = _PROMPT_INJECTION_PATTERNS.subn("[удалено]", text)
    text = _TRIPLE_BACKTICK_RE.sub("`", text)
    text = _MULTILINE_COLLAPSE_RE.sub("\n\n", text)
    # Strip control characters but keep newlines and tabs.
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 0x20)
    text = text.strip()
    if role_hits or injection_hits:
        # WARNING level so it shows up in standard log scrapers; do
        # NOT include the cleaned text — could itself contain attacker-
        # controlled content. Just counts + context.
        logger.warning(
            "ai.prompt_injection_detected context=%s role_hits=%d injection_hits=%d",
            context, role_hits, injection_hits,
        )
    if not text:
        return None
    if max_length > 0 and len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


__all__ = [
    "sanitize_user_text",
    "_PROMPT_ROLE_MARKERS",
    "_PROMPT_INJECTION_PATTERNS",
    "_TRIPLE_BACKTICK_RE",
    "_MULTILINE_COLLAPSE_RE",
]
