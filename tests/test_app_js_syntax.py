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


def test_telegram_back_button_stack_is_registered() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert "setupTelegramBackButton" in text
    assert "BackButton" in text
    assert ".onClick(onBack)" in text
    assert ".offClick?.(onBack)" in text
    assert "MutationObserver" in text


def test_frontend_requests_and_ai_polling_have_jittered_retries() -> None:
    api_core = (JS_DIR / "api_core.js").read_text(encoding="utf-8")
    api_ai = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")
    assert "RETRYABLE_STATUSES" in api_core
    assert "retry-after" in api_core
    assert "Math.random()" in api_core
    assert "nextPollDelay" in api_ai
    assert "Math.random()" in api_ai


def test_pull_to_refresh_does_not_translate_app_root() -> None:
    text = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    setup = text[text.index("function setupPullToRefresh"):text.index("/* ─── Pinch-zoom")]
    assert "document.querySelector('.app')" not in setup
    assert "target.style.transform" not in setup
    assert "indicatorEl.style.transform" in setup


def test_pinch_zoom_caches_viewport_size_for_gestures() -> None:
    text = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    assert "let viewportSize = null" in text
    assert "function ensureViewportSize()" in text
    assert "const viewport = ensureViewportSize()" in text
    pinch = text[
        text.index("function attachPinchZoom"):
        text.index("/* ─── Long-press action menu")
    ]
    assert "getBoundingClientRect" not in pinch
    assert "style.transform = \"none\"" not in pinch


def test_node_syntax_check() -> None:
    for script in JS_MODULES:
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_inert_walk_handles_nested_modals() -> None:
    """Wave 25.3 regression test: _applyInertToSiblings must walk DOWN
    from <body> to the modal, inerting siblings at each level. The
    Wave 22 version assumed modals were direct children of <body>,
    but in this codebase they live inside ``<div class="app">``.
    The broken version inerted ``<div class="app">`` and the
    inheritance propagated to the modal itself — symptom was
    no scroll / no clicks inside any modal.

    This test runs the actual JS function against a minimal DOM
    mock through Node, exercising both the flat case (modal as
    body child) and the nested case (modal inside #app-root).
    """
    helpers_src = (JS_DIR / "dom_helpers.js").read_text(encoding="utf-8")
    # Extract just the two inert functions so the runner doesn't need
    # to load the rest of dom_helpers.js (which references browser-
    # specific APIs we don't mock).
    apply_match = re.search(
        r"function _applyInertToSiblings\(modalEl\)\s*\{.*?\n\}\n",
        helpers_src, re.DOTALL,
    )
    restore_match = re.search(
        r"function _restoreInertSiblings\(modalEl\)\s*\{.*?\n\}\n",
        helpers_src, re.DOTALL,
    )
    assert apply_match and restore_match, "inert helpers not found"

    harness = r"""
// Minimal element mock supporting the operations the inert helpers use.
function makeEl(name) {
    const el = {
        _name: name,
        _parent: null,
        children: [],
        _attrs: new Map(),
        isConnected: true,
        setAttribute(k, v) { this._attrs.set(k, v); },
        removeAttribute(k) { this._attrs.delete(k); },
        hasAttribute(k) { return this._attrs.has(k); },
        contains(other) {
            if (other === this) return true;
            for (const c of this.children) if (c.contains(other)) return true;
            return false;
        },
        _appendChild(child) { child._parent = this; this.children.push(child); },
    };
    return el;
}

function isInert(el) { return el.hasAttribute("inert"); }

// __APPLY__
// __RESTORE__

// ── Case 1: flat (modal is direct body child) ─────────────────────────
{
    const body = makeEl("body");
    const offline = makeEl("offline");
    const ptr = makeEl("ptr");
    const modal = makeEl("modal");
    body._appendChild(offline);
    body._appendChild(ptr);
    body._appendChild(modal);
    globalThis.document = { body };

    _applyInertToSiblings(modal);
    if (isInert(modal)) throw new Error("flat: modal must not be inert");
    if (!isInert(offline)) throw new Error("flat: offline must be inert");
    if (!isInert(ptr)) throw new Error("flat: ptr must be inert");

    _restoreInertSiblings(modal);
    if (isInert(offline) || isInert(ptr)) throw new Error("flat: restore failed");
}

// ── Case 2: nested (modal inside #app-root, matching real index.html) ─
{
    const body = makeEl("body");
    const offline = makeEl("offline");
    const ptr = makeEl("ptr");
    const appRoot = makeEl("appRoot");
    const header = makeEl("header");
    const main = makeEl("main");
    const modal = makeEl("modal");
    body._appendChild(offline);
    body._appendChild(ptr);
    body._appendChild(appRoot);
    appRoot._appendChild(header);
    appRoot._appendChild(main);
    appRoot._appendChild(modal);
    globalThis.document = { body };

    _applyInertToSiblings(modal);
    if (isInert(modal)) throw new Error("nested: modal itself must not be inert");
    if (isInert(appRoot)) throw new Error("nested: app-root must not be inert (it's on the path)");
    if (!isInert(offline)) throw new Error("nested: offline must be inert");
    if (!isInert(ptr)) throw new Error("nested: ptr must be inert");
    if (!isInert(header)) throw new Error("nested: header must be inert");
    if (!isInert(main)) throw new Error("nested: main must be inert");

    _restoreInertSiblings(modal);
    if (isInert(offline) || isInert(ptr) || isInert(header) || isInert(main)) {
        throw new Error("nested: restore must un-inert everything we set");
    }
}

// ── Case 3: nested modal opened over another modal ────────────────────
{
    const body = makeEl("body");
    const appRoot = makeEl("appRoot");
    const header = makeEl("header");
    const outer = makeEl("outer");
    const inner = makeEl("inner");
    body._appendChild(appRoot);
    appRoot._appendChild(header);
    appRoot._appendChild(outer);
    appRoot._appendChild(inner);  // sibling of outer at app-root level
    globalThis.document = { body };

    _applyInertToSiblings(outer);
    if (!isInert(inner)) throw new Error("stacked: inner must be inert after outer opens");
    if (!isInert(header)) throw new Error("stacked: header must be inert");
    if (isInert(outer)) throw new Error("stacked: outer must not be inert");

    _applyInertToSiblings(inner);
    if (isInert(inner)) throw new Error("stacked: inner must not be inert after IT opens (lift)");
    if (!isInert(outer)) throw new Error("stacked: outer stays inert (it's behind inner)");
    if (!isInert(header)) throw new Error("stacked: header stays inert");

    _restoreInertSiblings(inner);
    // Inner closing restores the state-before-inner-opened: inner was
    // inert (from outer's sweep), outer was active (it was the open
    // modal), header was inert.
    if (!isInert(inner)) throw new Error("stacked: closing inner re-applies inert it lifted");
    if (isInert(outer)) throw new Error("stacked: outer active modal is inert");
    if (!isInert(header)) throw new Error("stacked: header stays inert (outer is still open)");

    _restoreInertSiblings(outer);
    if (isInert(inner) || isInert(outer) || isInert(header)) {
        throw new Error("stacked: closing outer un-inerts everything");
    }
}

console.log("OK");
"""
    harness = harness.replace("// __APPLY__", apply_match.group(0))
    harness = harness.replace("// __RESTORE__", restore_match.group(0))

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
        tmp.write(harness)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=15,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    assert result.returncode == 0, (
        f"inert behaviour test failed:\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "OK" in result.stdout, f"unexpected output: {result.stdout!r}"


def test_no_modal_close_bypasses_close_modal_animated() -> None:
    """Wave 25.5 regression test: every modal close path must go through
    ``closeModalAnimated`` (or a wrapper that ultimately does) — never
    directly set ``modalEl.hidden = true``. The direct-hidden shortcut
    skips ``unlockBodyScroll``, the focus-trap cleanup, and the
    ``_restoreInertSiblings`` call, leaving the page scroll-locked
    and every background element ``inert``. User-visible symptom: UI
    appears frozen, no clicks register anywhere.

    The bug that triggered this test: the global Escape handler in
    api_events.js had a fast-path for the listing-assistant modal
    that did ``laModal.hidden = true`` directly. The listing-assistant
    module already had its OWN Escape handler that called
    ``closeModal()`` correctly; the duplicate global handler did its
    direct-hide first and the local handler then no-op'd (because
    modal was already hidden=true), leaving cleanup undone.

    This test scans api_events.js for any ``hidden = true`` set on a
    DOM element variable that LOOKS like a modal reference.
    """
    events_src = (JS_DIR / "api_events.js").read_text(encoding="utf-8")
    # Strip line comments so commented-out historical examples don't
    # trigger the matcher.
    lines = [
        ln for ln in events_src.splitlines()
        if not ln.strip().startswith("//")
    ]
    stripped_src = "\n".join(lines)
    bad_matches = re.findall(
        r"\b([a-zA-Z_]\w*[Mm]odal)\.hidden\s*=\s*true",
        stripped_src,
    )
    assert not bad_matches, (
        f"api_events.js sets ``.hidden = true`` directly on modals: "
        f"{sorted(set(bad_matches))}. Use the matching closeXModal() "
        f"helper instead — direct-hidden bypasses scroll-lock release "
        f"and the inert/focus-trap cleanup."
    )


def test_close_ai_modal_cancels_polling() -> None:
    """closeAIModal used to leave the AI polling loop running for up to 6
    minutes, blocking re-opening AI Analysis. Verify the cancel-by-session
    pattern is wired up so a regression here is caught statically.

    UX-M8 (Wave 25): api_ai.js was split — closeAIModal lives in
    api_ai_modal.js and the session counter is now aiCtx.pollSession
    (shared mutable state owned by the orchestrator)."""
    modal_text = (JS_DIR / "api_ai_modal.js").read_text(encoding="utf-8")
    orchestrator_text = (JS_DIR / "api_ai.js").read_text(encoding="utf-8")

    assert "aiCtx.pollSession" in modal_text, (
        "session counter for poll cancellation is missing from api_ai_modal.js"
    )
    assert "aiCtx.pollSession" in orchestrator_text, (
        "orchestrator must read aiCtx.pollSession for isCancelled"
    )

    # closeAIModal in the modal module must bump the session and
    # release the loading slot.
    close_block = re.search(r"function closeAIModal\(\)\s*\{[^}]+\}", modal_text, re.DOTALL)
    assert close_block, "closeAIModal definition not found in api_ai_modal.js"
    body = close_block.group(0)
    assert "aiCtx.pollSession" in body, "closeAIModal must invalidate the poll session"
    assert "aiCtx.loading = false" in body, "closeAIModal must release aiCtx.loading"


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


def test_lead_and_watchlist_mutations_resolve_versions_before_patch() -> None:
    leads_js = (JS_DIR / "api_leads.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    core_js = (JS_DIR / "api_core.js").read_text(encoding="utf-8")

    assert "async function _resolveLeadVersion" in leads_js
    for fn_name in (
        "confirmLead",
        "closeDeal",
        "revertLeadStage",
        "updateLeadMeta",
        "markLeadAsSold",
    ):
        fn_start = leads_js.index(f"function {fn_name}")
        fn_body = leads_js[fn_start:leads_js.index("\n    // ──", fn_start + 1)]
        assert "_resolveLeadVersion" in fn_body, f"{fn_name} can PATCH without fresh version"
        assert "version," in fn_body, f"{fn_name} must send resolved version"

    assert "async function _resolveWatchlistVersion" in watchlist_js
    for fn_name in ("updateWatchlistMeta", "promoteWatchlistToLead"):
        fn_start = watchlist_js.index(f"function {fn_name}")
        fn_body = watchlist_js[fn_start:watchlist_js.index("\n    // ──", fn_start + 1)]
        assert "_resolveWatchlistVersion" in fn_body, f"{fn_name} can PATCH without version"
        assert "version," in fn_body, f"{fn_name} must send resolved version"

    add_lead_start = actions_js.index("async function addLeadFromListing")
    add_lead_body = actions_js[add_lead_start:actions_js.index("\n    // Make", add_lead_start)]
    assert "_resolveWatchingVersion" in add_lead_body
    assert "version," in add_lead_body
    assert "Lead version is required for updates" in core_js
    assert "Данные устарели" in core_js


def test_collection_actions_show_immediate_pending_toasts() -> None:
    actions_js = (JS_DIR / "app_actions.js").read_text(encoding="utf-8")
    watchlist_js = (JS_DIR / "api_watchlist.js").read_text(encoding="utf-8")

    add_lead = actions_js[
        actions_js.index("async function addLeadFromListing"):
        actions_js.index("\n    // Make", actions_js.index("async function addLeadFromListing"))
    ]
    assert 'showToast("Добавляю…", "info"' in add_lead
    assert 'showToast("В покупках", "success"' in add_lead
    assert "dismissToast(pendingToast)" in add_lead

    for fn_name, success_text in (
        ("addWatchlistFromListing", "В избранном"),
        ("promoteWatchlistToLead", "В покупках"),
    ):
        fn_start = watchlist_js.index(f"function {fn_name}")
        fn_body = watchlist_js[fn_start:watchlist_js.index("\n    // ──", fn_start + 1)]
        assert 'showToast("Добавляю…", "info"' in fn_body
        assert f'showToast("{success_text}", "success"' in fn_body
        assert "dismissToast(pendingToast)" in fn_body


def test_toasts_are_minimal_and_fast() -> None:
    render_core = (JS_DIR / "render_core.js").read_text(encoding="utf-8")
    css = _read_all_css()

    assert "toast-dot" in render_core
    assert "iconMap" not in render_core
    assert "const animationDuration = prefersReducedMotion ? 10 : 120" in render_core
    assert ".toast-dot" in css
    assert ".toast-icon" not in css
    assert ".toast.entering:nth-child(2)" not in css
    assert "animation: toast-in 120ms" in css


def test_unified_item_card_replaces_lead_and_watchlist_builders() -> None:
    """Roadmap milestone: lead/watchlist surfaces share one builder.
    The wrappers stay only as thin aliases for the unified function."""
    text = (JS_DIR / "render_card_builders.js").read_text(encoding="utf-8")
    # The unified entry point exists.
    assert "function buildItemCard(" in text
    # The wrappers became thin one-liners delegating to it.
    assert 'buildItemCard(lead, { mode: "lead", signal })' in text
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

    # Tapping .listing-top opens the detail view via delegation
    assert "listing._item = item" in builder
    cards = (JS_DIR / "render_cards.js").read_text(encoding="utf-8")
    assert "_delegateListingClick" in cards

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
    center. No getBoundingClientRect() is called in the pinch helper;
    it clamps against cached viewport dimensions instead."""
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

    # Clamping uses cached viewport dimensions instead of layout reads
    pinch = helpers[
        helpers.index("function attachPinchZoom"):
        helpers.index("/* ─── Long-press action menu")
    ]
    assert "getBoundingClientRect" not in pinch
    assert "style.transform = \"none\"" not in pinch
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
