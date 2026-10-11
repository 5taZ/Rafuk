"""Checks of the Mini App JS modules against the API and, when node is available, at runtime."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[1] / "frontend" / "js"
# Same order as the <script defer> tags in index.html.
JS_MODULES = [
    JS_DIR / "app_core.js",
    JS_DIR / "app_renderers.js",
    JS_DIR / "app_actions.js",
    JS_DIR / "app.js",
]

_SEGMENT = r"(?:[\w-]+|\$\{[^}]+\})"
ENDPOINT_RE = re.compile(rf"/api/v1(?:/{_SEGMENT})+")
HELPER_RE = re.compile(
    r"\b(getJson|postJson|deleteJson|requestJson|runTrackedRequest)\(\s*[`\"']$"
)
HELPER_METHODS = {"getJson": "GET", "postJson": "POST", "deleteJson": "DELETE"}


def _frontend_calls() -> set[tuple[str, str]]:
    """Returns (method, path) for every /api/v1 URL literal in the JS modules."""
    calls = set()
    for path in JS_MODULES:
        text = path.read_text(encoding="utf-8")
        for match in ENDPOINT_RE.finditer(text):
            helper = HELPER_RE.search(text[max(0, match.start() - 60) : match.start()])
            method = "GET"
            if helper and helper[1] in HELPER_METHODS:
                method = HELPER_METHODS[helper[1]]
            elif helper and helper[1] == "requestJson":
                explicit = re.search(r"method:\s*\"(\w+)\"", text[match.end() : match.end() + 200])
                method = explicit[1] if explicit else "GET"
            calls.add((method, re.sub(r"\$\{[^}]+\}", "1", match[0])))
    return calls


def _route_pattern(path: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", path)
    regex = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{regex}$")


def test_every_frontend_endpoint_exists_in_the_api() -> None:
    from fastapi.routing import APIRoute

    from api.main import create_app

    app = create_app()
    routes = [
        (_route_pattern(route.path), route.methods)
        for route in app.routes
        if isinstance(route, APIRoute)
    ]
    calls = _frontend_calls()
    assert len(calls) >= 15, calls

    missing = sorted(
        f"{method} {path}"
        for method, path in calls
        if not any(pattern.match(path) and method in methods for pattern, methods in routes)
    )
    assert missing == []


# ---- Runtime checks (need node) ------------------------------------------------------

NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const listeners = [];
const documentStub = {
    addEventListener(type) { listeners.push(type); },
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    documentElement: { dataset: {}, setAttribute() {}, getAttribute() { return null; } },
};
const context = { document: documentStub, console, Intl, setTimeout, clearTimeout };
context.window = context;
vm.createContext(context);
for (const file of process.argv.slice(2)) {
    vm.runInContext(fs.readFileSync(file, "utf8"), context, { filename: file });
}
const factories = {};
for (const name of ["createAppCore", "createAppRenderers", "createAppActions", "analyticsApp"]) {
    factories[name] = vm.runInContext(`typeof ${name}`, context);
}
const core = vm.runInContext("createAppCore()", context);
process.stdout.write(JSON.stringify({
    factories,
    listeners,
    prices: [
        core.formatPrice(250, "BYN"),
        core.formatPrice(1500, "BYN"),
        core.formatPrice(15000, "BYN"),
        core.formatPrice(1234, "USD"),
        core.formatPrice(null, "BYN"),
        core.formatPrice("abc", "USD"),
    ],
    deltas: [core.formatDelta(12.4), core.formatDelta(-3), core.formatDelta(0.2)],
    deltaClasses: [core.deltaClass(5), core.deltaClass(-5), core.deltaClass(0)],
    hasInitData: core.hasTelegramInitData(),
}));
"""


@pytest.fixture(scope="module")
def node_runtime(tmp_path_factory: pytest.TempPathFactory) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = tmp_path_factory.mktemp("node") / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [node, str(harness), *(str(path) for path in JS_MODULES)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_modules_load_together_and_wait_for_dom(node_runtime: dict) -> None:
    assert set(node_runtime["factories"].values()) == {"function"}
    # app.js only registers the DOMContentLoaded hook at load time.
    assert node_runtime["listeners"] == ["DOMContentLoaded"]


def test_core_formatters(node_runtime: dict) -> None:
    assert node_runtime["prices"] == [
        "250 р.",
        "1.5 тыс. р.",
        "15 тыс. р.",
        "$1,234",
        "—",
        "—",
    ]
    assert node_runtime["deltas"] == ["+12%", "-3%", ""]
    assert node_runtime["deltaClasses"] == ["over", "under", ""]
    assert node_runtime["hasInitData"] is False
