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


def test_load_leads_and_watchlist_have_stale_response_guard() -> None:
    """Rapid tab toggling (Покупки ↔ Избранное) used to issue overlapping
    loadLeads()/loadWatchlist() — a stale response could resolve last
    and overwrite a fresher state. Both loaders must compare the request
    id captured at start against the latest one before assigning state."""
    leads_js = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")

    # state holds the monotonic counters; their names are referenced by the loaders.
    assert "_leadsRequestId" in leads_js
    assert "_watchlistRequestId" in watchlist_js
    # Each loader bumps and then checks the counter before writing state.
    assert "state._leadsRequestId" in leads_js
    assert "if (requestId !== state._leadsRequestId)" in leads_js
    assert "state._watchlistRequestId" in watchlist_js
    assert "if (requestId !== state._watchlistRequestId)" in watchlist_js


def test_lead_and_watchlist_mutations_have_inflight_guard() -> None:
    """A double-click on "В покупки" / "В избранное" used to fire two
    POST /api/v1/leads or /api/v1/watchlist round-trips because state
    hadn't reloaded between clicks. Each mutation must short-circuit
    when the same ad/row is already mid-flight."""
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")

    assert "_inflightAdMutations" in actions_js
    assert "_inflightAdMutations.has(item.ad_id)" in actions_js

    assert "_inflightAd" in watchlist_js
    assert "_inflightWatchId" in watchlist_js
    # promoteWatchlistToLead and deleteWatchlistItem both guard by row id.
    assert "_inflightWatchId.has(item.id)" in watchlist_js
    assert "_inflightWatchId.has(watchlistId)" in watchlist_js


def test_unified_item_card_replaces_lead_and_watchlist_builders() -> None:
    """Roadmap milestone: lead/watchlist surfaces share one builder.
    The wrappers stay only as thin aliases for the unified function."""
    text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    # The unified entry point exists.
    assert "function buildItemCard(" in text
    # The wrappers became thin one-liners delegating to it.
    assert 'buildItemCard(lead, { mode: "lead" })' in text
    assert 'buildItemCard(item, { mode: "watching"' in text
    # buildItemCard is exported alongside the legacy names.
    assert "buildItemCard," in text


def test_make_swipeable_respects_reduced_motion_and_haptics() -> None:
    """Swipe-to-promote on watching cards must be opt-in by motion
    preference and trigger Telegram haptics when committing the action."""
    text = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    assert "function makeSwipeable" in text
    # Skip the gesture entirely under reduced-motion.
    assert "(prefers-reduced-motion: reduce)" in text
    # Haptic feedback when the swipe commits.
    assert "HapticFeedback" in text
    assert "impactOccurred" in text
    # Wired up in the watching branch of the unified builder.
    builder_text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "makeSwipeable(card" in builder_text
    assert "promoteWatchlistToLead" in builder_text
    assert "deleteWatchlistItem" in builder_text


def test_listing_detail_loaders_share_stale_response_guard() -> None:
    """Three loaders open the listing-detail modal: openListingDetail
    (search results), openLeadDetail, openWatchlistDetail. They share
    state.detail and state.detailAi, so without a unified guard the
    older fetch can resolve last and pop the wrong content into the
    modal. Ensure all three bump the same _detailRequestId."""
    listings_js = (JS_DIR / "api_listings.js").read_text(encoding="utf-8")
    leads_js = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")

    surfaces = (
        ("api_listings.js", listings_js),
        ("api_leads.js", leads_js),
        ("api_watchlist.js", watchlist_js),
    )
    for path, text in surfaces:
        assert "state._detailRequestId" in text, f"{path} missing detail-request-id guard"
        assert "if (requestId !== state._detailRequestId)" in text, (
            f"{path} missing stale-response check"
        )

    # loadHistory and loadComparison have their own dedicated counters.
    assert "state._historyRequestId" in listings_js
    assert "state._comparisonRequestId" in listings_js


def test_telegram_theme_params_bridged_to_css_variables() -> None:
    """Telegram exposes the user's client palette via WebApp.themeParams.
    We bridge those colours onto our CSS custom properties so the Mini
    App visually blends into the surrounding chat (custom themes,
    AMOLED, premium gradients), and we listen for themeChanged so the
    binding updates live."""
    text = (JS_DIR / "app_core.js").read_text(encoding="utf-8")
    assert "applyTelegramThemeColors" in text
    # Required Telegram theme keys must be honoured.
    for key in ("bg_color", "text_color", "hint_color", "button_color"):
        assert key in text, f"themeParams.{key} not bridged"
    # Live updates on theme changes.
    assert '"themeChanged"' in text
    # Manual toggle should clear the Telegram-set inline overrides so the
    # user choice wins.
    assert "removeProperty" in text
