from __future__ import annotations

import re
import subprocess
from pathlib import Path

APP_JS = Path("frontend/js/app.js")
JS_DIR = Path("frontend/js")
JS_MODULES = sorted(JS_DIR.glob("*.js"))


def test_file_is_not_empty() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert len(text.strip()) > 200


def test_defines_analytics_app_function() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert re.search(r"function\s+analyticsApp\b|analyticsApp\s*=\s*function", text)


def test_has_required_methods_and_endpoints() -> None:
    all_js = "\n".join(p.read_text(encoding="utf-8") for p in JS_MODULES)
    assert re.search(r"\bsearch\s*\(", all_js)
    assert re.search(r"\bloadListings\s*\(", all_js)
    assert re.search(r"\bloadDeals\s*\(", all_js)
    assert re.search(r"\brenderChart\b|\brenderBoxPlot\b", all_js)
    assert "/api/v1/price-stats" in all_js
    assert "/api/v1/price-history" in all_js
    assert "/api/v1/listings" in all_js
    assert "Telegram.WebApp" in all_js


def test_node_syntax_check() -> None:
    for script in JS_MODULES:
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_close_ai_modal_cancels_polling() -> None:
    """closeAIModal used to leave the AI polling loop running for up to 6
    minutes, blocking re-opening AI Analysis. Verify the cancel-by-session
    pattern is wired up so a regression here is caught statically."""
    text = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")
    assert "_aiPollSession" in text, "session counter for poll cancellation is missing"
    # closeAIModal must bump the session and release the loading slot.
    close_block = re.search(r"function closeAIModal\(\)\s*\{[^}]+\}", text, re.DOTALL)
    assert close_block, "closeAIModal definition not found"
    body = close_block.group(0)
    assert "_aiPollSession" in body, "closeAIModal must invalidate the poll session"
    assert "_aiLoading = false" in body, "closeAIModal must release _aiLoading"
