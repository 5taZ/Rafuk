from __future__ import annotations

import re
import subprocess
from pathlib import Path

APP_JS = Path("frontend/js/app.js")
JS_MODULES = [
    APP_JS,
    Path("frontend/js/app_core.js"),
    Path("frontend/js/app_renderers.js"),
    Path("frontend/js/app_actions.js"),
]


def test_file_is_not_empty() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert len(text.strip()) > 200


def test_defines_analytics_app_function() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert re.search(r"function\s+analyticsApp\b|analyticsApp\s*=\s*function", text)


def test_has_required_methods_and_endpoints() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert re.search(r"\bsearch\s*\(", text)
    assert re.search(r"\bloadListings\s*\(", text)
    assert re.search(r"\bloadDeals\s*\(", text)
    assert re.search(r"\brenderChart\b|\brenderBoxPlot\b", text)
    assert "/api/v1/price-stats" in text
    assert "/api/v1/price-history" in text
    assert "/api/v1/listings" in text
    assert "Telegram.WebApp" in text


def test_node_syntax_check() -> None:
    for script in JS_MODULES:
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"
