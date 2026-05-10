"""BE-C5 Wave 18: dedupe helpers for AI payloads.

Both Gemini 2.5 Flash and the legacy Gemma fallback sometimes
paraphrase the same observation across ``condition.notes``,
``watch_out``, and ``red_flags``, producing visible duplicates in the
analysis modal (e.g. "Лакокрасочное покрытие без видимых значительных
дефектов" + "Лакокрасочное покрытие без видимых значительных
дефектов, диски чистые"). We collapse them server-side so the UI
doesn't need to repeat the logic and the behaviour is model-agnostic.

Extracted from ``api/services/ai_service.py`` so the sanitization
and collapsing rules live next to each other rather than buried in a
1700-line service. Re-exported from ``api.services.ai_service`` for
back-compat with existing importers
(``api/services/ai_analysis_pipeline.py``, ``tests/test_ai_analysis.py``).
"""

from __future__ import annotations


def _normalize_for_dedupe(text: str) -> str:
    """Lowercase + collapse whitespace + strip trailing punctuation."""
    if not isinstance(text, str):
        return ""
    return " ".join(text.lower().split()).rstrip(".,;:!?·…—–-").strip()


def _is_paraphrase(short: str, longer: str, *, min_prefix: int = 25) -> bool:
    """True when `short` is essentially a truncated version of `longer`.

    Used to drop "Автомобиль выглядит чистым и ухоженным" when we
    already kept "Автомобиль выглядит чистым и ухоженным на всех 18
    фото" — it's the same observation, just truncated.

    The prefix match must end on a word boundary so we don't collapse
    different words that share a stem (e.g. "покрытие" vs "покрытием").
    """
    if not short or not longer:
        return False
    if len(short) > len(longer):
        return False
    if len(short) < min_prefix:
        return short == longer
    if not longer.startswith(short):
        return False
    if len(longer) == len(short):
        return True
    next_char = longer[len(short)]
    # Allow whitespace, punctuation, and common separators; reject letters/digits
    # which would mean the prefix cuts a word in half.
    return not next_char.isalnum()


def _dedupe_text_list(items: list, *, seen: set[str] | None = None) -> list:
    """Drop near-duplicate strings from a list, preserving order.

    Keeps the longer, more informative wording when two items are
    paraphrases of the same observation. Pre-existing entries in
    ``seen`` (normalized form) are also filtered out — used for
    cross-field dedupe.
    """
    if not isinstance(items, list):
        return items
    seen = set(seen) if seen else set()
    kept: list[str] = []
    kept_norm: list[str] = []
    for raw in items:
        text = raw if isinstance(raw, str) else ""
        norm = _normalize_for_dedupe(text)
        if not norm or norm in seen:
            continue
        # If a previously-kept item is a paraphrase of this one (or
        # vice versa), keep the longer form.
        replaced = False
        for i, existing_norm in enumerate(kept_norm):
            if _is_paraphrase(existing_norm, norm):
                kept[i] = raw
                kept_norm[i] = norm
                seen.discard(existing_norm)
                seen.add(norm)
                replaced = True
                break
            if _is_paraphrase(norm, existing_norm):
                replaced = True
                break
        if replaced:
            continue
        kept.append(raw)
        kept_norm.append(norm)
        seen.add(norm)
    return kept


def _dedupe_dict_list(items: list, *, key_fields: tuple[str, ...]) -> list:
    """Drop near-duplicate dicts based on the joined text of key fields.

    Used for ``watch_out`` (objects with ``point`` and ``why``).
    """
    if not isinstance(items, list):
        return items
    kept: list = []
    kept_norm: list[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        joined = " ".join(
            str(entry.get(field) or "").strip() for field in key_fields
        ).strip()
        norm = _normalize_for_dedupe(joined)
        if not norm:
            continue
        replaced = False
        for i, existing_norm in enumerate(kept_norm):
            if _is_paraphrase(existing_norm, norm):
                kept[i] = entry
                kept_norm[i] = norm
                replaced = True
                break
            if _is_paraphrase(norm, existing_norm):
                replaced = True
                break
        if replaced:
            continue
        kept.append(entry)
        kept_norm.append(norm)
    return kept


def _dedupe_listing_payload(result: dict) -> dict:
    """Collapse duplicates inside the seller-side listing draft.

    Same paraphrase logic as the analysis dedupe, applied to the
    list-of-strings (`selling_points`, `photo_tips`) and list-of-dicts
    (`negotiation_playbook`) fields the listing assistant returns.
    """
    if not isinstance(result, dict):
        return result
    for key in ("selling_points", "photo_tips"):
        value = result.get(key)
        if isinstance(value, list):
            result[key] = _dedupe_text_list(value)
    playbook = result.get("negotiation_playbook")
    if isinstance(playbook, list):
        result["negotiation_playbook"] = _dedupe_dict_list(
            playbook, key_fields=("scenario", "response")
        )
    return result


def dedupe_analysis_payload(merged: dict) -> dict:
    """Collapse paraphrase duplicates inside the AI analysis payload.

    Cross-field rule: anything already covered in ``condition.notes``
    is removed from ``watch_out`` and ``red_flags`` so the same
    observation doesn't render in both the "Состояние по фото" block
    and the "Что перепроверить" / "Красные флаги" blocks.
    """
    cond = merged.get("condition")
    if isinstance(cond, dict) and isinstance(cond.get("notes"), list):
        cond["notes"] = _dedupe_text_list(cond["notes"])

    condition_notes_norm: set[str] = set()
    if isinstance(cond, dict):
        condition_notes_norm = {
            _normalize_for_dedupe(n) for n in cond.get("notes") or [] if isinstance(n, str)
        }
        condition_notes_norm.discard("")

    watch_out = merged.get("watch_out")
    if isinstance(watch_out, list):
        # Cross-field: drop watch_out items that just restate condition notes.
        filtered_watch: list = []
        for entry in watch_out:
            if isinstance(entry, dict):
                point = _normalize_for_dedupe(str(entry.get("point") or ""))
                why = _normalize_for_dedupe(str(entry.get("why") or ""))
                if any(
                    _is_paraphrase(cn, point) or _is_paraphrase(point, cn)
                    or _is_paraphrase(cn, why) or _is_paraphrase(why, cn)
                    for cn in condition_notes_norm
                ):
                    continue
            filtered_watch.append(entry)
        merged["watch_out"] = _dedupe_dict_list(
            filtered_watch, key_fields=("point", "why")
        )

    for key in ("meeting_checklist", "red_flags", "negotiation_tips"):
        value = merged.get(key)
        if isinstance(value, list):
            merged[key] = _dedupe_text_list(value, seen=condition_notes_norm)

    return merged


__all__ = [
    "_normalize_for_dedupe",
    "_is_paraphrase",
    "_dedupe_text_list",
    "_dedupe_dict_list",
    "_dedupe_listing_payload",
    "dedupe_analysis_payload",
]
