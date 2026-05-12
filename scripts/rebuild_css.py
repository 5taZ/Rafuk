"""Rebuild frontend/css/style.css from parts/*.css.

Why not @import? @import inside a linked stylesheet forces a
sequential network waterfall — every partial blocks first paint
behind the previous one's parse. On slow mobile connections this
adds ~500-1500ms to the time-to-first-paint.

Instead we keep the partials as a developer navigation aid and
ship one concatenated bundle. Edit a partial, then run::

    uv run python scripts/rebuild_css.py

The bundle is checked in so the live site doesn't depend on the
build step at deploy time.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend" / "css"
PARTS_ORDER = ("tokens", "layout", "modals", "pipeline", "states", "ai", "brand")


def build_css_text() -> str:
    header = (
        "/* style.css — single-file bundle.\n"
        " *\n"
        " * Authoritative stylesheet served to the browser. Source-of-\n"
        " * truth for editing lives under ./parts/ for navigability;\n"
        " * rebuild with ``uv run python scripts/rebuild_css.py`` after\n"
        " * touching a partial.\n"
        " *\n"
        " * The earlier @import-shell version caused a serial CSS\n"
        " * waterfall (style.css → tokens.css → layout.css → …) which\n"
        " * delayed first paint on slow connections. This concatenated\n"
        " * form ships in one round-trip and the browser parses it\n"
        " * monolithically.\n"
        " */\n\n"
    )

    chunks: list[str] = [header]
    for name in PARTS_ORDER:
        partial = ROOT / "parts" / f"{name}.css"
        if not partial.exists():
            raise SystemExit(f"missing partial: {partial}")
        body = partial.read_text(encoding="utf-8")
        chunks.append(f"/* ===== {name} ===== */\n")
        chunks.append(body)
        if not body.endswith("\n"):
            chunks.append("\n")
        chunks.append("\n")

    return "".join(chunks)


def rebuild() -> None:
    css = build_css_text()
    (ROOT / "style.css").write_text(css, encoding="utf-8")
    total = css.count("\n")
    print(f"style.css: {total} lines from {len(PARTS_ORDER)} partials")


if __name__ == "__main__":
    rebuild()
