"""Static checks of the Mini App markup and stylesheet.

The HTML is parsed with the standard library, so these tests need neither a browser
nor node. They check the real structure the JS modules depend on instead of marker
strings: script order, view panels, every element app_core.js looks up, accessibility
attributes and the theme palette contrast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
HTML_FILE = FRONTEND / "index.html"
CSS_FILE = FRONTEND / "css" / "style.css"
CORE_JS = FRONTEND / "js" / "app_core.js"
MARKET_SIGNALS = ROOT / "api" / "services" / "market_signals.py"

VIEWS = ("overview", "ads", "tracking", "cheap", "monitoring", "deals")
APP_MODULES = ("js/app_core.js", "js/app_renderers.js", "js/app_actions.js", "js/app.js")
PANELS = ("geography", "distribution", "history", "comparison", "segments")
FAIR_PRICE_BANDS = ("below_market", "fair", "above_market", "high")

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "source", "track", "wbr",
}  # fmt: skip


# ---- A tiny DOM built with html.parser ------------------------------------------


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    parent: Element | None = None
    children: list[Element] = field(default_factory=list)
    text: str = ""

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def iter(self):
        for child in self.children:
            yield child
            yield from child.iter()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def full_text(self) -> str:
        return self.text + "".join(child.full_text() for child in self.children)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {})
        self.stack = [self.root]
        self.order: list[Element] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {name: value or "" for name, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(element)
        self.order.append(element)
        if tag not in VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].text += data


@dataclass
class Document:
    raw: str
    elements: list[Element]

    def by_id(self, element_id: str) -> Element | None:
        matches = [el for el in self.elements if el.attrs.get("id") == element_id]
        return matches[0] if matches else None

    def select(self, selector: str) -> list[Element]:
        """Matches a simple compound selector: tag, #id, .class, [attr], [attr="v"]."""
        pattern = re.compile(
            r"(?P<tag>^[a-z][a-z0-9]*)"
            r"|#(?P<id>[\w-]+)"
            r"|\.(?P<cls>[\w-]+)"
            r"|\[(?P<attr>[\w-]+)(?:=(?P<q>[\"']?)(?P<val>[^\"'\]]*)(?P=q))?\]"
        )
        position = 0
        checks = []
        while position < len(selector):
            match = pattern.match(selector, position)
            if match is None:
                raise ValueError(f"unsupported selector: {selector!r}")
            checks.append(match)
            position = match.end()

        def matches(element: Element) -> bool:
            for check in checks:
                if check["tag"] and element.tag != check["tag"]:
                    return False
                if check["id"] and element.attrs.get("id") != check["id"]:
                    return False
                if check["cls"] and check["cls"] not in element.classes:
                    return False
                if check["attr"]:
                    if check["attr"] not in element.attrs:
                        return False
                    if check["val"] is not None and element.attrs[check["attr"]] != check["val"]:
                        return False
            return True

        return [element for element in self.elements if matches(element)]


@pytest.fixture(scope="module")
def doc() -> Document:
    raw = HTML_FILE.read_text(encoding="utf-8")
    builder = _TreeBuilder()
    builder.feed(raw)
    builder.close()
    return Document(raw=raw, elements=builder.order)


@pytest.fixture(scope="module")
def css_text() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS_FILE.read_text(encoding="utf-8"), flags=re.S)


def _local_path(url: str) -> str:
    return url.split("?", 1)[0]


# ---- Document basics ------------------------------------------------------------


def test_html_has_doctype_lang_and_viewport(doc: Document) -> None:
    assert doc.raw.strip().lower().startswith("<!doctype html")
    html = doc.select("html")[0]
    assert html.attrs.get("lang") == "ru"
    viewport = doc.select('meta[name="viewport"]')
    assert viewport and "width=device-width" in viewport[0].attrs["content"]


def test_element_ids_are_unique(doc: Document) -> None:
    ids = [el.attrs["id"] for el in doc.elements if "id" in el.attrs]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    assert duplicates == []


def test_no_alpine_leftovers(doc: Document) -> None:
    scripts = [el.attrs.get("src", "") for el in doc.select("script")]
    assert not any("alpine" in src.lower() for src in scripts)
    alpine_attrs = [
        (el.tag, name)
        for el in doc.elements
        for name in el.attrs
        if name.startswith(("x-", "@")) or name == ":class"
    ]
    assert alpine_attrs == []
    assert not doc.select("template")


# ---- Scripts and assets -----------------------------------------------------------


def test_scripts_load_in_dependency_order(doc: Document) -> None:
    sources = [el.attrs["src"] for el in doc.select("script") if el.attrs.get("src")]
    assert len(sources) == 2 + len(APP_MODULES), sources
    assert sources[0].endswith("/telegram-web-app.js")
    assert re.search(r"/chart\.js@\d+\.\d+\.\d+/", sources[1]), sources[1]
    assert [_local_path(src) for src in sources[2:]] == list(APP_MODULES)


def test_deferred_scripts_keep_execution_order(doc: Document) -> None:
    scripts = [el for el in doc.select("script") if el.attrs.get("src")]
    telegram, *deferred = scripts
    # The inline theme script reads Telegram.WebApp synchronously.
    assert "defer" not in telegram.attrs and "async" not in telegram.attrs
    for script in deferred:
        assert "defer" in script.attrs, script.attrs["src"]
        assert "async" not in script.attrs, script.attrs["src"]


def test_inline_theme_script_runs_before_first_paint(doc: Document) -> None:
    scripts = doc.select("script")
    inline = [el for el in scripts if not el.attrs.get("src")]
    assert len(inline) == 1
    theme = inline[0]
    assert any(ancestor.tag == "head" for ancestor in theme.ancestors())
    telegram_index = next(
        i for i, el in enumerate(scripts) if "telegram" in el.attrs.get("src", "")
    )
    assert scripts.index(theme) == telegram_index + 1
    code = theme.full_text()
    assert "colorScheme" in code
    assert "prefers-color-scheme" in code
    assert "dataset.theme" in code


def test_local_assets_share_one_version(doc: Document) -> None:
    urls = [el.attrs["href"] for el in doc.select('link[rel="stylesheet"]')]
    urls += [el.attrs["src"] for el in doc.select("script") if el.attrs.get("src")]
    local = [url for url in urls if not url.startswith(("http:", "https:", "//"))]
    assert sorted(_local_path(url) for url in local) == sorted(["css/style.css", *APP_MODULES])
    versions = {re.search(r"[?&]v=([\w.-]+)", url)[1] for url in local if "?v=" in url}
    assert len(versions) == 1, local
    assert all("?v=" in url for url in local), local


# ---- Structure the JS modules depend on --------------------------------------------


def test_every_view_tab_has_a_panel(doc: Document) -> None:
    tabs = doc.select("button[data-view]")
    assert [tab.attrs["data-view"] for tab in tabs] == list(VIEWS)
    panels = doc.select(".view-panel")
    assert sorted(panel.attrs["id"] for panel in panels) == sorted(f"{v}-view" for v in VIEWS)
    for tab in tabs:
        view = tab.attrs["data-view"]
        assert tab.attrs.get("aria-controls") == f"{view}-view"
        assert any(ancestor.tag == "nav" for ancestor in tab.ancestors())


def test_initial_view_is_overview(doc: Document) -> None:
    active = [tab for tab in doc.select("button[data-view]") if "active" in tab.classes]
    assert [tab.attrs["data-view"] for tab in active] == ["overview"]
    assert active[0].attrs.get("aria-current") == "page"
    for tab in doc.select("button[data-view]"):
        if tab is not active[0]:
            assert tab.attrs.get("aria-current") == "false"
    visible = [
        panel.attrs["id"] for panel in doc.select(".view-panel") if "hidden" not in panel.attrs
    ]
    assert visible == ["overview-view"]


def _core_lookups() -> tuple[list[str], list[str]]:
    source = CORE_JS.read_text(encoding="utf-8")
    ids = re.findall(r"getElementById\(\s*[\"']([^\"']+)[\"']\s*\)", source)
    selectors = re.findall(r"querySelector(?:All)?\(\s*[\"']([^\"']+)[\"']\s*\)", source)
    return ids, selectors


def test_every_element_app_core_looks_up_exists(doc: Document) -> None:
    ids, selectors = _core_lookups()
    assert len(ids) > 50 and len(selectors) >= 5, "app_core.js lookups were not parsed"
    missing_ids = sorted({element_id for element_id in ids if doc.by_id(element_id) is None})
    assert missing_ids == []
    empty_selectors = sorted({selector for selector in selectors if not doc.select(selector)})
    assert empty_selectors == []


def test_panel_toggles_control_their_bodies(doc: Document) -> None:
    toggles = doc.select("button[data-panel-toggle]")
    assert sorted(t.attrs["data-panel-toggle"] for t in toggles) == sorted(PANELS)
    for toggle in toggles:
        body_id = f"{toggle.attrs['data-panel-toggle']}-body"
        body = doc.by_id(body_id)
        assert body is not None and "section-body" in body.classes
        assert toggle.attrs.get("aria-controls") == body_id
        # renderPanels() keeps aria-expanded in sync with the hidden attribute.
        expected = "false" if "hidden" in body.attrs else "true"
        assert toggle.attrs.get("aria-expanded") == expected


def test_rate_strip_has_the_label_renderers_rewrite(doc: Document) -> None:
    strip = doc.by_id("rate-strip")
    assert strip is not None and "hidden" in strip.attrs
    assert any("rate-label" in el.classes for el in strip.iter())


def test_chart_canvases_exist(doc: Document) -> None:
    for canvas_id in ("priceChart", "historyChart"):
        canvas = doc.by_id(canvas_id)
        assert canvas is not None and canvas.tag == "canvas"
        assert any("chart-box" in ancestor.classes for ancestor in canvas.ancestors())


# ---- Accessibility -----------------------------------------------------------------


def _accessible_name(doc: Document, element: Element) -> str:
    if element.attrs.get("aria-label"):
        return element.attrs["aria-label"]
    if element.attrs.get("aria-labelledby"):
        target = doc.by_id(element.attrs["aria-labelledby"])
        return target.full_text().strip() if target else ""
    element_id = element.attrs.get("id")
    if element_id:
        for label in doc.select(f'label[for="{element_id}"]'):
            return label.full_text().strip()
    for ancestor in element.ancestors():
        if ancestor.tag == "label":
            return ancestor.full_text().strip()
    return _visible_text(element).strip()


def _visible_text(element: Element) -> str:
    if element.attrs.get("aria-hidden") == "true":
        return ""
    return element.text + "".join(_visible_text(child) for child in element.children)


def test_page_has_a_heading(doc: Document) -> None:
    headings = doc.select("h1")
    assert len(headings) == 1
    assert headings[0].full_text().strip()


def test_form_controls_have_names(doc: Document) -> None:
    controls = doc.select("input") + doc.select("select") + doc.select("textarea")
    unnamed = [el.attrs.get("id", el.tag) for el in controls if not _accessible_name(doc, el)]
    assert unnamed == []


def test_search_inputs_have_real_labels(doc: Document) -> None:
    for input_id in ("search-input", "compare-input"):
        labels = doc.select(f'label[for="{input_id}"]')
        assert labels and labels[0].full_text().strip(), input_id
        assert "visually-hidden" in labels[0].classes


def test_label_targets_exist(doc: Document) -> None:
    for label in doc.select("label[for]"):
        assert doc.by_id(label.attrs["for"]) is not None, label.attrs["for"]


def test_buttons_have_type_and_name(doc: Document) -> None:
    for button in doc.select("button"):
        assert button.attrs.get("type") == "button", button.attrs
        assert _accessible_name(doc, button), button.attrs


def test_images_have_alt(doc: Document) -> None:
    for image in doc.select("img"):
        assert "alt" in image.attrs, image.attrs


def test_decorative_icons_are_hidden(doc: Document) -> None:
    icons = doc.select(".view-tab-icon") + doc.select(".view-hero-icon") + doc.select("svg")
    assert icons
    for icon in icons:
        assert icon.attrs.get("aria-hidden") == "true", icon.attrs


def test_live_regions(doc: Document) -> None:
    toasts = doc.by_id("toast-container")
    assert toasts is not None and toasts.attrs.get("aria-live") == "polite"
    error_bar = doc.by_id("error-bar")
    assert error_bar is not None and error_bar.attrs.get("role") == "alert"


def test_detail_modal_is_a_labelled_dialog(doc: Document) -> None:
    dialogs = doc.select('[role="dialog"]')
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.attrs.get("aria-modal") == "true"
    assert doc.by_id(dialog.attrs["aria-labelledby"]) is not None
    assert any(ancestor.attrs.get("id") == "detail-modal" for ancestor in dialog.ancestors())


# ---- Stylesheet ---------------------------------------------------------------------


def _rules(css: str) -> list[tuple[str, str]]:
    return [
        (selector.strip(), body) for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    ]


def _declarations(body: str) -> dict[str, str]:
    result = {}
    for declaration in body.split(";"):
        if ":" in declaration:
            name, value = declaration.split(":", 1)
            result[name.strip()] = value.strip()
    return result


def _rule(css: str, selector: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    for selectors, body in _rules(css):
        if selector in [part.strip() for part in selectors.split(",")]:
            merged.update(_declarations(body))
    return merged


def test_hidden_attribute_always_wins(css_text: str) -> None:
    assert _rule(css_text, "[hidden]").get("display") == "none !important"


def test_theme_blocks_set_color_scheme(css_text: str) -> None:
    assert _rule(css_text, '[data-theme="dark"]').get("color-scheme") == "dark"
    assert _rule(css_text, '[data-theme="light"]').get("color-scheme") == "light"
    assert _rule(css_text, ":root").get("color-scheme") == "dark"


def test_no_hard_coded_option_color(css_text: str) -> None:
    option = _rule(css_text, ".deal-select option")
    assert option.get("color", "").startswith("var(")
    assert "#111" not in css_text


def test_focus_is_visible(css_text: str) -> None:
    ring = _rule(css_text, ":focus-visible")
    assert ring.get("outline", "").startswith("2px solid")
    assert ".strict-toggle input:focus-visible + .strict-toggle-pill" in css_text
    assert ".search-wrap:focus-within" in css_text
    assert ".deal-range-input-wrap:focus-within" in css_text


def test_small_controls_have_tap_targets(css_text: str) -> None:
    tap = _rule(css_text, ":root").get("--tap", "")
    assert tap.endswith("px") and int(tap[:-2]) >= 36
    for selector in (".cur-btn", ".s-tab", ".quick-chip", ".listing-btn", ".section-toggle"):
        assert _rule(css_text, selector).get("min-height") == "var(--tap)", selector
    shared = _rule(css_text, ".primary-link")
    assert shared.get("min-height") == "var(--tap)"


def test_every_fair_price_band_has_a_badge_style(css_text: str) -> None:
    source = MARKET_SIGNALS.read_text(encoding="utf-8")
    for band in FAIR_PRICE_BANDS:
        assert f'"{band}"' in source, band
        assert f".listing-badge.{band}" in css_text, band


def test_board_does_not_overflow_narrow_screens(css_text: str) -> None:
    assert _rule(css_text, ".board-grid").get("grid-template-columns") == "minmax(0, 1fr)"
    assert _rule(css_text, ".listing-actions").get("flex-wrap") == "wrap"


def test_dead_selectors_are_gone(css_text: str) -> None:
    for dead in ("[x-cloak]", ".sk-", ".empty-", ".feature-", ".status-badge", "skeleton-pulse"):
        assert dead not in css_text, dead


def test_every_custom_property_is_defined(css_text: str) -> None:
    used = set(re.findall(r"var\((--[\w-]+)\s*\)", css_text))
    defined = set(re.findall(r"(--[\w-]+)\s*:", css_text))
    assert sorted(used - defined) == []


# ---- Palette contrast (WCAG 2.x, AA for normal text) -------------------------------

TEXT_TOKENS = (
    "--text", "--text-muted", "--text-dim", "--accent-text", "--green", "--red", "--amber",
)  # fmt: skip
SURFACE_TOKENS = ("--bg", "--bg-card", "--bg-elevated", "--bg-input", "--bg-hover")
TINT_TOKENS = ("--accent-soft", "--green-soft", "--red-soft", "--amber-soft", "--surface-tint")


def _rgba(value: str) -> tuple[float, float, float, float]:
    value = value.strip()
    if value.startswith("#") and len(value) == 7:
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 1.0)
    match = re.fullmatch(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", value)
    assert match, f"unsupported color {value!r}"
    red, green, blue, alpha = match.groups()
    return (int(red), int(green), int(blue), float(alpha))


def _over(color: tuple[float, float, float, float], base: tuple) -> tuple:
    alpha = color[3]
    return tuple(color[i] * alpha + base[i] * (1 - alpha) for i in range(3)) + (1.0,)


def _luminance(color: tuple) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in color[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: tuple, second: tuple) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_tokens_meet_wcag_aa(css_text: str, theme: str) -> None:
    tokens = _rule(css_text, f'[data-theme="{theme}"]')
    surfaces = {name: _rgba(tokens[name]) for name in SURFACE_TOKENS}
    for tint in TINT_TOKENS:
        for base in ("--bg", "--bg-card"):
            surfaces[f"{tint} on {base}"] = _over(_rgba(tokens[tint]), surfaces[base])

    failures = []
    for text in TEXT_TOKENS:
        color = _rgba(tokens[text])
        for surface_name, surface in surfaces.items():
            ratio = _contrast(color, surface)
            if ratio < 4.5:
                failures.append(f"{text} on {surface_name}: {ratio:.2f}")
    for fill in ("--accent", "--accent-hover"):
        ratio = _contrast(_rgba(tokens["--on-accent"]), _rgba(tokens[fill]))
        if ratio < 4.5:
            failures.append(f"--on-accent on {fill}: {ratio:.2f}")
    assert failures == []
