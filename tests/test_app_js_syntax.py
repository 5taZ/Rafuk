from __future__ import annotations

import re
import subprocess
from pathlib import Path

APP_JS = Path("frontend/js/app.js")
JS_DIR = Path("frontend/js")
JS_MODULES = sorted(JS_DIR.glob("*.js"))
CSS_DIR = Path("frontend/css")


def _read_all_css() -> str:
    """Concatenate every CSS file under frontend/css/.

    style.css is now a thin @import loader; the actual rules live
    under parts/. Tests asserting against rule presence shouldn't
    care which partial owns a class, so we glob the whole tree.
    """
    chunks: list[str] = []
    for css_path in sorted(CSS_DIR.rglob("*.css")):
        chunks.append(css_path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


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
    assert "_requestId" in leads_js
    assert "_requestId" in watchlist_js
    # Each loader bumps and then checks the counter before writing state.
    assert "state.leads._requestId" in leads_js
    assert "if (requestId !== state.leads._requestId)" in leads_js
    assert "state.watchlist._requestId" in watchlist_js
    assert "if (requestId !== state.watchlist._requestId)" in watchlist_js


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


def test_make_swipeable_helper_kept_for_future_surfaces() -> None:
    """Swipe-to-promote was removed from watching cards because the
    swipe-bg DOM left thin colored slivers visible at the rounded
    corners on Telegram WebView. The helper itself stays in
    dom_helpers.js (still respects reduced-motion + haptics) so
    future surfaces can opt back in without re-implementing the
    gesture math from scratch.
    """
    text = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    assert "function makeSwipeable" in text
    assert "(prefers-reduced-motion: reduce)" in text
    assert "HapticFeedback" in text
    assert "impactOccurred" in text
    # Watching cards no longer wrap themselves with makeSwipeable —
    # confirm the call was removed and the explanatory comment stays.
    builder_text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "makeSwipeable(card" not in builder_text, (
        "swipe wrap re-introduced — re-evaluate the visual artifact "
        "before shipping"
    )


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
        assert "state.detail._requestId" in text, f"{path} missing detail-request-id guard"
        assert "if (requestId !== state.detail._requestId)" in text, (
            f"{path} missing stale-response check"
        )

    # loadHistory has its own dedicated counter.
    assert "state.charts._historyRequestId" in listings_js


def test_long_press_action_menu_helper_and_listing_card_wiring() -> None:
    """A long press on a listing card surfaces a quick-action sheet
    (В покупки / В избранное / Открыть на Kufar) so the inline
    buttons stay scannable. Tapping the card itself opens the detail
    view, so "Подробнее" is no longer in the long-press menu. The
    helper must:
      * cancel on movement past the tolerance (treat as scroll),
      * suppress the synthetic click after a long press fires,
      * fire haptics on commit (Telegram-native feel),
      * be wired into buildListingNode in render_card_builders.js."""
    helpers = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    assert "function attachLongPress" in helpers
    assert "function showLongPressMenu" in helpers
    assert "function hideLongPressMenu" in helpers
    assert "moveTolerancePx" in helpers
    assert "suppressClick" in helpers
    assert "HapticFeedback" in helpers

    builder = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "attachLongPress(listing" in builder
    # The three action labels must be present in the menu so a casual
    # rename here trips the test instead of silently shipping.
    for label in (
        '"В покупки"',
        '"В избранное"',
        '"Открыть на Kufar"',
    ):
        assert label in builder, f"long-press menu missing item {label}"

    # Tapping .listing-top opens the detail view
    assert 'listing.querySelector(".listing-top")' in builder

    css = _read_all_css()
    for cls in (
        ".lp-menu-overlay",
        ".lp-menu-sheet",
        ".lp-menu-item",
        ".lp-menu-item--accent",
    ):
        assert cls in css, f"missing CSS for {cls}"


def test_watchlist_renders_price_sparkline_when_history_present() -> None:
    """Watchlist cards now show a 30-day price-trend sparkline next
    to the current price. The renderer must:
      * read item.price_history (the inline series the backend
        returns from /api/v1/watchlist),
      * skip rendering when the series has fewer than 2 points,
      * pick a direction (down/up/flat) so CSS can colour the line —
        green for buyer-friendly drops, red for rises, muted for flat."""
    text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    assert "function _buildPriceSparkline" in text
    assert "item.price_history" in text
    # Direction class is built as `wl-sparkline--${direction}` and the
    # three literal direction values must exist for the CSS selectors
    # to match. Stylesheet itself is checked separately.
    assert "wl-sparkline--" in text
    for direction in ('"down"', '"up"', '"flat"'):
        assert direction in text, f"missing direction literal {direction}"

    css_text = _read_all_css()
    for cls in (".wl-sparkline--down", ".wl-sparkline--up", ".wl-sparkline--flat"):
        assert cls in css_text, f"sparkline CSS for {cls} missing"


def test_detail_modal_supports_pinch_zoom_with_swipe_deferral() -> None:
    """The listing-detail modal hosts the photo gallery. Two-finger
    pinch + double-tap zoom on the main image is provided by
    attachPinchZoom (dom_helpers.js); when the image is zoomed
    (`.is-zoomed`) the swipe-between-photos handler in api_events.js
    must defer to the zoom interaction so panning a magnified shot
    doesn't accidentally jump to the next photo.

    The zoom uses a transform-origin: 0 0 model with anchor-point
    math so the zoom focuses on the pinch center, not the image
    center. No getBoundingClientRect() is called on touchmove —
    the base rect is snapshotted once on touchstart."""
    helpers = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    events = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    modals = (JS_DIR / "render_modals.js").read_text(encoding="utf-8")

    assert "function attachPinchZoom" in helpers
    assert "is-zoomed" in helpers
    assert "reset" in helpers

    # Anchor-point model: transform-origin 0 0, viewportToImage helper
    assert 'transformOrigin = "0 0"' in helpers or "transformOrigin: '0 0'" in helpers
    assert "viewportToImage" in helpers
    assert "pinchAnchorPx" in helpers
    assert "pinchAnchorPy" in helpers

    # Base rect is snapshotted on touchstart, not recalculated on move
    assert "baseRect" in helpers
    assert "clampTranslate" in helpers

    # The swipe-between-photos handler must short-circuit while zoomed
    # OR while the user has more than one finger on the screen.
    assert "is-zoomed" in events
    assert "e.touches.length > 1" in events or "touches.length > 1" in events

    # render_modals.js wires the helper at render time and resets the
    # transform on close so the next lot opens at 1×.
    assert "_pinchController" in modals
    assert "attachPinchZoom" in modals


def test_theme_init_does_not_inherit_telegram_palette_colours() -> None:
    """The Mini App keeps its own palette so the brand stays consistent
    across Telegram clients with custom themes / AMOLED / Premium
    gradients. We mirror Telegram's coarse dark/light preference at
    startup but never bridge the per-colour theme params (bg_color,
    text_color, button_color, …) onto our CSS variables."""
    text = (JS_DIR / "app_core.js").read_text(encoding="utf-8")
    # We DO honour the binary dark/light hint…
    assert "colorScheme" in text
    assert '"themeChanged"' in text
    # …but we DO NOT pull individual palette colours.
    forbidden_keys = (
        "themeParams.bg_color",
        "themeParams.text_color",
        "themeParams.button_color",
        "themeParams.hint_color",
        "applyTelegramThemeColors",
    )
    for needle in forbidden_keys:
        assert needle not in text, (
            f"theme integration leaked back: {needle} must not be referenced — "
            "the Mini App keeps its own palette"
        )
