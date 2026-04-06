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
    assert any("alpine" in script.lower() for script in scripts)
    assert any("chart" in script.lower() for script in scripts)
    assert any("app.js" in script for script in scripts)


def test_html_has_stats_and_listings(soup: BeautifulSoup) -> None:
    assert len(soup.find_all(attrs={"x-text": True})) >= 4
    canvases = soup.find_all("canvas")
    assert len(canvases) >= 2
    assert len(soup.find_all(attrs={"x-for": True})) >= 1
    assert soup.find(attrs={"data-view": "deals"}) is not None


def test_css_has_required_building_blocks(css_text: str) -> None:
    assert ":root" in css_text
    assert "[data-theme=\"dark\"]" in css_text
    assert "@keyframes skeleton-pulse" in css_text
    assert "@media" in css_text
