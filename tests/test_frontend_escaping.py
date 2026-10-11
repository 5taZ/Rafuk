"""Runtime checks of the Mini App's XSS guards: escapeHtml, safeUrl and the html`` tag.

app_core.js and app_renderers.js are loaded into a node vm with a minimal DOM stub, the same
way test_frontend_js.py does it. The tests are skipped when node is not installed (CI has it).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[1] / "frontend" / "js"
JS_MODULES = [JS_DIR / "app_core.js", JS_DIR / "app_renderers.js"]

ESCAPE_INPUTS = [
    '<script>alert("x")</script>',
    "Tom & Jerry",
    "it's `tick`",
    '" onmouseover="alert(1)',
    "&amp; stays escaped once",
    "Цена — 250 р.",
    None,
    0,
    42,
    False,
]

SAFE_URL_INPUTS = [
    "https://www.kufar.by/item/123",
    "  http://example.com/a b  ",
    "HTTPS://Example.COM",
    'https://example.com/?q="><script>alert(1)</script>',
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "ftp://example.com/file",
    "//evil.example/x",
    "/item/123",
    "item/123",
    "",
    "   ",
    None,
    42,
    {"href": "https://example.com"},
]

# Every user-controlled field of a listing card carries a payload; the second card has safe
# URLs with characters that must be encoded or escaped inside attributes.
HOSTILE_LISTING = {
    "ad_id": 1,
    "title": "<img src=x onerror=alert(1)>",
    "condition": "<b>cond</b>",
    "seller_type": "shop",
    "price": 250,
    "price_vs_median": -12,
    "fair_price_label": "<u>label</u>",
    "fair_price_band": 'x" onmouseover="alert(1)',
    "deal_verdict": '"><svg onload=alert(1)>',
    "anomaly_labels": ["<i>anomaly</i>", "b & c"],
    "thumbnail": "javascript:alert(1)",
    "link": "javascript:alert(1)",
}
SAFE_LISTING = {
    "ad_id": 2,
    "title": "iPhone 13 «128 ГБ»",
    "price": 1500,
    "thumbnail": 'https://img.example/a.jpg?x="y"',
    "link": "https://www.kufar.by/item/2?a=1&b=2",
}

NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function makeElement(tagName) {
    return {
        tagName,
        className: "",
        innerHTML: "",
        textContent: "",
        children: [],
        attributes: {},
        appendChild(child) { this.children.push(child); return child; },
        setAttribute(name, value) { this.attributes[name] = String(value); },
        removeAttribute(name) { delete this.attributes[name]; },
        getAttribute(name) { return this.attributes[name] ?? null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
    };
}

const documentStub = {
    addEventListener() {},
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement: makeElement,
    createTextNode(text) { return { textContent: String(text) }; },
    activeElement: null,
    documentElement: { dataset: {}, setAttribute() {}, getAttribute() { return null; } },
};
// URL is a browser/node global, not an ECMAScript builtin, so a vm context lacks it.
const context = { document: documentStub, console, Intl, URL, setTimeout, clearTimeout };
context.window = context;
vm.createContext(context);
for (const file of process.argv.slice(3)) {
    vm.runInContext(fs.readFileSync(file, "utf8"), context, { filename: file });
}

const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const core = vm.runInContext("createAppCore()", context);
const renderers = vm.runInContext("createAppRenderers", context)({ ...core, actions: {} });

const container = makeElement("div");
const badge = makeElement("span");
const rendered = renderers.renderListingsCollection(input.listings, container, badge, "empty");

process.stdout.write(JSON.stringify({
    escaped: input.escape.map((value) => renderers.escapeHtml(value)),
    urls: input.urls.map((value) => renderers.safeUrl(value)),
    rendered,
    badge: badge.textContent,
    cards: container.children.map((child) => ({ tag: child.tagName, html: child.innerHTML })),
}));
"""


@pytest.fixture(scope="module")
def escaping_runtime(tmp_path_factory: pytest.TempPathFactory) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    workdir = tmp_path_factory.mktemp("escaping")
    harness = workdir / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    payload = workdir / "input.json"
    payload.write_text(
        json.dumps(
            {
                "escape": ESCAPE_INPUTS,
                "urls": SAFE_URL_INPUTS,
                "listings": [HOSTILE_LISTING, SAFE_LISTING],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(harness), str(payload), *(str(path) for path in JS_MODULES)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_escape_html_escapes_markup_and_quote_characters(escaping_runtime: dict) -> None:
    assert escaping_runtime["escaped"] == [
        "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;",
        "Tom &amp; Jerry",
        "it&#39;s &#96;tick&#96;",
        "&quot; onmouseover=&quot;alert(1)",
        "&amp;amp; stays escaped once",
        "Цена — 250 р.",
        "",
        "0",
        "42",
        "false",
    ]


def test_safe_url_allows_only_absolute_http_urls(escaping_runtime: dict) -> None:
    assert escaping_runtime["urls"] == [
        "https://www.kufar.by/item/123",
        "http://example.com/a%20b",
        "https://example.com/",
        "https://example.com/?q=%22%3E%3Cscript%3Ealert(1)%3C/script%3E",
        "",  # javascript:
        "",  # mixed-case javascript:
        "",  # javascript: after whitespace
        "",  # javascript: split by a tab, which the URL parser strips
        "",  # data:
        "",  # vbscript:
        "",  # file:
        "",  # ftp:
        "",  # protocol-relative
        "",  # root-relative
        "",  # relative
        "",
        "",
        "",  # null
        "",  # number
        "",  # object
    ]


def test_listing_card_escapes_text_and_attributes(escaping_runtime: dict) -> None:
    assert escaping_runtime["rendered"] is True
    assert escaping_runtime["badge"] == "2 из 2"
    cards = escaping_runtime["cards"]
    assert [card["tag"] for card in cards] == ["article", "article"]
    markup = cards[0]["html"]

    # Text payloads are rendered as text.
    assert "&lt;img src=x onerror=alert(1)&gt;" in markup
    assert '<span class="tag">&lt;b&gt;cond&lt;/b&gt;</span>' in markup
    assert '<span class="tag">Магазин</span>' in markup
    assert "&lt;u&gt;label&lt;/u&gt;" in markup
    assert "&quot;&gt;&lt;svg onload=alert(1)&gt;" in markup
    # Arrays of nested html`` results are joined without double escaping.
    assert '<span class="listing-badge warn">&lt;i&gt;anomaly&lt;/i&gt;</span>' in markup
    assert '<span class="listing-badge warn">b &amp; c</span>' in markup
    assert '<div class="listing-badges">' in markup
    # An attribute payload cannot close the quoted attribute.
    assert 'class="listing-badge x&quot; onmouseover=&quot;alert(1)"' in markup
    assert 'class="listing-badge verdict-neutral"' in markup

    for raw in ("<img src=x", "<b>", "<u>", "<svg", "<i>", 'onmouseover="'):
        assert raw not in markup, raw
    # Non-http(s) URLs produce neither a link nor an image.
    assert "javascript:" not in markup
    assert "<a " not in markup
    assert '<div class="listing-thumb placeholder">' in markup
    assert "<img" not in markup
    assert "250 р." in markup


def test_listing_card_keeps_safe_urls_escaped_inside_attributes(escaping_runtime: dict) -> None:
    markup = escaping_runtime["cards"][1]["html"]

    assert '<img class="listing-thumb" src="https://img.example/a.jpg?x=%22y%22"' in markup
    assert (
        '<a class="listing-btn listing-btn--accent" '
        'href="https://www.kufar.by/item/2?a=1&amp;b=2" '
        'target="_blank" rel="noreferrer noopener">Kufar</a>'
    ) in markup
    assert '<span class="listing-name">iPhone 13 «128 ГБ»</span>' in markup
    assert "1.5 тыс. р." in markup
