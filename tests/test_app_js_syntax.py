from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[1] / "frontend" / "js"
APP_JS = JS_DIR / "app.js"
# Load order from index.html: each module only uses factories defined before it.
JS_MODULES = [
    JS_DIR / "app_core.js",
    JS_DIR / "app_renderers.js",
    JS_DIR / "app_actions.js",
    APP_JS,
]
FACTORIES = {
    "app_core.js": "createAppCore",
    "app_renderers.js": "createAppRenderers",
    "app_actions.js": "createAppActions",
    "app.js": "analyticsApp",
}
# Endpoints the Mini App must keep calling; the API side is checked in test_frontend_js.py.
REQUIRED_ENDPOINTS = (
    "/api/v1/price-stats",
    "/api/v1/price-history",
    "/api/v1/listings",
    "/api/v1/segments",
    "/api/v1/geography",
    "/api/v1/compare",
    "/api/v1/currency-rates",
    "/api/v1/listing-detail",
    "/api/v1/trackers",
    "/api/v1/tracker-events",
    "/api/v1/leads",
    "/api/v1/watchlist",
    "/api/v1/watchlist/refresh",
    "/api/v1/opportunity-board",
)


def _all_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in JS_MODULES)


def test_file_is_not_empty() -> None:
    for path in JS_MODULES:
        assert len(path.read_text(encoding="utf-8").strip()) > 200, path.name


def test_each_module_defines_its_factory() -> None:
    for path in JS_MODULES:
        factory = FACTORIES[path.name]
        text = path.read_text(encoding="utf-8")
        assert re.search(rf"^function\s+{factory}\s*\(", text, flags=re.M), path.name


def test_app_wires_modules_in_order() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    core = text.index("createAppCore(")
    renderers = text.index("createAppRenderers(")
    actions = text.index("createAppActions(")
    assert core < renderers < actions
    assert 'addEventListener("DOMContentLoaded"' in text


def test_has_required_methods_and_endpoints() -> None:
    text = _all_sources()
    assert re.search(r"\bfunction\s+search\s*\(", text)
    assert re.search(r"\bfunction\s+loadListings\s*\(", text)
    assert re.search(r"\bfunction\s+loadDeals\s*\(", text)
    assert re.search(r"\bfunction\s+renderChart\s*\(", text)
    for endpoint in REQUIRED_ENDPOINTS:
        assert endpoint in text, endpoint
    assert re.search(r"\bTelegram\??\.WebApp\b", text)


def test_no_test_only_markers_in_app() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert "runtimeMarkers" not in text
    assert "/api/v1/" not in text


def test_node_syntax_check() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for script in JS_MODULES:
        result = subprocess.run(
            [node, "--check", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{script.name}: {result.stderr}"
