from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

FRONTEND = Path("frontend")
HTML_FILE = FRONTEND / "index.html"
CSS_FILE = FRONTEND / "css" / "style.css"


@pytest.fixture(scope="module")
def soup() -> BeautifulSoup:
    assert HTML_FILE.exists()
    return BeautifulSoup(HTML_FILE.read_text(encoding="utf-8"), "html.parser")


@pytest.fixture(scope="module")
def css_text() -> str:
    assert CSS_FILE.exists()
    return CSS_FILE.read_text(encoding="utf-8")


def test_html_has_doctype() -> None:
    raw = HTML_FILE.read_text(encoding="utf-8")
    assert raw.strip().lower().startswith("<!doctype html")


def test_html_has_viewport_meta(soup: BeautifulSoup) -> None:
    meta = soup.find("meta", attrs={"name": "viewport"})
    assert meta is not None
    assert "width=device-width" in (meta.get("content") or "")


def test_html_loads_required_scripts(soup: BeautifulSoup) -> None:
    scripts = [script.get("src", "") for script in soup.find_all("script")]
    assert any("telegram-web-app.js" in script for script in scripts)
    assert any("chart" in script.lower() for script in scripts)
    assert any("app.js" in script for script in scripts)


def test_html_has_stats_and_listings(soup: BeautifulSoup) -> None:
    canvases = soup.find_all("canvas")
    assert len(canvases) >= 2
    assert soup.find(attrs={"data-view": "deals"}) is not None


def test_css_has_required_building_blocks(css_text: str) -> None:
    assert ":root" in css_text
    # Dark defaults are in :root; light theme overrides in [data-theme="light"]
    assert '[data-theme="light"]' in css_text
    assert "@keyframes skeleton-loading" in css_text
    assert "@media" in css_text


def test_css_uses_color_tokens_outside_theme_blocks(css_text: str) -> None:
    """No hard-coded hex colour outside the :root / [data-theme] blocks.

    The codebase used to scatter `var(--red, #e11d48)` fallbacks and
    raw `#16a34a` greens in dozens of rules; that broke theme switching
    because the literals didn't shift with the dark/light palette.
    Lock that down: the palette lives in the theme blocks, every other
    rule must reach for a CSS variable."""
    import re as _re

    lines = css_text.split("\n")
    in_theme_block = False
    depth = 0
    offenders: list[tuple[int, str]] = []

    for i, line in enumerate(lines, 1):
        if ":root" in line or "[data-theme=" in line:
            in_theme_block = True
        if in_theme_block and "{" in line:
            depth += line.count("{")
        if in_theme_block:
            if "}" in line:
                depth -= line.count("}")
                if depth <= 0:
                    in_theme_block = False
                    depth = 0
            continue
        for match in _re.findall(r"#[0-9a-fA-F]{6}\b", line):
            # White / black are intentional on coloured backgrounds where
            # we want guaranteed contrast regardless of the theme.
            if match.lower() in {"#ffffff", "#000000"}:
                continue
            offenders.append((i, line.strip()[:100]))
            break

    assert not offenders, "Hex literals outside theme blocks: " + "; ".join(
        f"L{i}: {snippet}" for i, snippet in offenders
    )


def test_css_defines_motion_tokens(css_text: str) -> None:
    """Standard transition durations + easing live as variables so future
    components don't reinvent the timing every time."""
    assert "--t-fast:" in css_text
    assert "--t-base:" in css_text
    assert "--t-slow:" in css_text
    assert "--easing-standard:" in css_text
