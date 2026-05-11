(function (window) {
"use strict";
window.App = window.App || {};


;
// FE-M14: shared UI/UX timing constants. Each value is named where
// the previous "magic number" was duplicated across ≥2 modules and
// the magnitude (multi-second / sub-second) carries product meaning.
// Adding a new one is fine; mutating an existing one needs a comment
// explaining the user-visible effect.

// In-flight optimistic-mutation guard. ad_ids and watchlist row ids
// stay in the dedupe set for this long after a click that fires a
// POST/PATCH/DELETE — long enough to absorb a slow 3G round-trip
// plus the local re-render, short enough that a stuck request
// doesn't permanently lock the user out of re-clicking. Used by
// app_actions.js (cross-pipeline ad-mutation guard) and
// api_watchlist.js (per-row guard).
const INFLIGHT_GUARD_MS = 30_000;

// HapticFeedback requires Telegram WebApp version >= 6.1.
// Returns the HapticFeedback object if available, null otherwise.
function _tgHaptic() {
    const tg = window.Telegram?.WebApp;
    if (!tg?.HapticFeedback) return null;
    if (tg.version && parseFloat(tg.version) < 6.1) return null;
    return tg.HapticFeedback;
}

function _prefersReducedMotion() {
    return typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function trapFocus(container) {
    const sel = [
        'button:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        'a[href]',
        '[tabindex]:not([tabindex="-1"])',
    ].join(", ");
    const focusable = container.querySelectorAll(sel);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    function handleKeydown(e) {
        if (e.key !== "Tab") return;
        if (e.shiftKey) {
            if (document.activeElement === first) {
                e.preventDefault();
                last.focus();
            }
        } else {
            if (document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    }

    container.addEventListener("keydown", handleKeydown);
    first.focus();
    return () => container.removeEventListener("keydown", handleKeydown);
}

function domAppend(target) {
    if (!target) return target;

    const appendChild = (child) => {
        if (child == null || child === false) return;
        if (Array.isArray(child)) {
            for (const nested of child) appendChild(nested);
            return;
        }
        if (child instanceof Node) {
            target.appendChild(child);
            return;
        }
        target.appendChild(document.createTextNode(String(child)));
    };

    for (let i = 1; i < arguments.length; i++) {
        appendChild(arguments[i]);
    }
    return target;
}

function domEl(tagName, options) {
    const node = document.createElement(tagName);
    const config = options || {};

    if (config.className) node.className = config.className;
    if (config.text != null) node.textContent = String(config.text);
    if (config.hidden != null) node.hidden = Boolean(config.hidden);
    if (config.type) node.type = config.type;
    if (config.value != null) node.value = String(config.value);

    if (config.attrs) {
        for (const [name, value] of Object.entries(config.attrs)) {
            if (value == null || value === false) continue;
            node.setAttribute(name, value === true ? "" : String(value));
        }
    }

    if (config.dataset) {
        for (const [name, value] of Object.entries(config.dataset)) {
            if (value == null) continue;
            node.dataset[name] = String(value);
        }
    }

    for (let i = 2; i < arguments.length; i++) {
        domAppend(node, arguments[i]);
    }
    return node;
}

function domClear(node) {
    if (node) node.replaceChildren();
}

function domFragment() {
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < arguments.length; i++) {
        domAppend(fragment, arguments[i]);
    }
    return fragment;
}

/* ─── Modal scroll lock ─────────────────────────────────────────────────
 * `body.modal-open` uses `position: fixed` + `overflow: hidden` to stop
 * iOS Safari and Telegram WebView from double-scrolling. The downside of
 * `position: fixed` is that the page jumps to the top on close.
 *
 * We solve this by:
 *  - storing window.scrollY before locking,
 *  - pinning the body via `top: -scrollY` so visually nothing moves,
 *  - restoring the scroll position synchronously after unlocking.
 *
 * Counter-based so nested modals (e.g. AI modal opened on top of detail
 * modal) don't unlock the body until the last one is closed.
 */
let __scrollLockCount = 0;
let __scrollLockY = 0;

function lockBodyScroll() {
    if (__scrollLockCount === 0) {
        __scrollLockY = window.scrollY || window.pageYOffset || 0;
        document.body.style.top = `-${__scrollLockY}px`;
        document.body.classList.add("modal-open");
    }
    __scrollLockCount += 1;
}

function unlockBodyScroll() {
    if (__scrollLockCount === 0) return;
    __scrollLockCount -= 1;
    if (__scrollLockCount > 0) return;
    document.body.classList.remove("modal-open");
    document.body.style.top = "";
    // Use instant scroll — the modal already animated out, we don't want
    // a second visible scroll-jump after that.
    window.scrollTo({ top: __scrollLockY, left: 0, behavior: "instant" });
}

/* ─── Animated modal open/close helpers ────────────────────────────────
 * The CSS uses pure @keyframes (`modal-overlay-in`, `modal-sheet-in`,
 * etc.) which always replay from frame 0 when the element transitions
 * from `display: none` (via `[hidden]`) to `display: block`. That means
 * we don't have to fight the browser's paint pipeline with reflow tricks
 * — toggling `hidden` is enough to play the entry animation cleanly.
 *
 * For close we add `.is-closing`, which swaps to the reverse keyframes,
 * then wait for `animationend` before flipping `[hidden]=true` so the
 * slide-down actually plays.
 */
// UX-M2: while a modal is open, mark everything ELSE on the page as
// ``inert`` so the user can't tab/click through to background chrome
// and assistive tech doesn't announce a mix of dialog + page. ``inert``
// is a one-shot equivalent of the older "set tabindex=-1 +
// aria-hidden=true on every sibling" dance and is supported by every
// browser the Mini App targets (Telegram WebView is Blink/WebKit,
// both shipped inert in 2022).
//
// Wave 25.3: the original Wave 22 implementation only inerted **direct
// children of <body>** and assumed the modal itself was one of them.
// In this codebase the modals live INSIDE ``<div class="app"
// id="app-root">``, which IS a direct child of body — so the loop
// inerted ``#app-root`` too, and ``inert`` inherits down the subtree.
// Result: the modal that was supposed to stay interactive became
// inert along with everything else. Symptom: no scroll / no clicks
// inside any modal.
//
// Correct algorithm: walk DOWN from <body> to the modal. At each
// level, inert the children that are NOT on the path to the modal.
// For nested modals (e.g. AI modal opened over the detail sheet),
// also LIFT inert from the modal itself + each ancestor on the path,
// because an outer modal's walk would have inerted them at THIS
// modal's body-level. Both the "newly inerted" and "lifted from"
// sets are stashed so close-time restore is exact.
function _applyInertToSiblings(modalEl) {
    if (!document.body) return;
    if (!document.body.contains(modalEl)) return;

    const newlyInert = [];   // we set inert here — must remove on close
    const liftedInert = [];  // we removed inert here — must restore on close

    let current = document.body;
    while (true) {
        // Find which child of `current` contains (or is) the modal —
        // this is the next step on the path.
        let pathChild = null;
        for (const child of current.children) {
            if (child === modalEl || child.contains(modalEl)) {
                pathChild = child;
                break;
            }
        }
        if (!pathChild) break;

        // Inert everything else at this level.
        for (const sibling of current.children) {
            if (sibling === pathChild) continue;
            if (sibling.hasAttribute("inert")) continue;  // outer modal already inerted it
            sibling.setAttribute("inert", "");
            newlyInert.push(sibling);
        }

        // Lift inert from pathChild if some outer modal had set it
        // (nested-modal case). Without this, the modal subtree stays
        // inert and the user can't interact with it.
        if (pathChild.hasAttribute("inert")) {
            pathChild.removeAttribute("inert");
            liftedInert.push(pathChild);
        }

        if (pathChild === modalEl) break;
        current = pathChild;
    }

    modalEl._inertSiblings = newlyInert;
    modalEl._liftedInert = liftedInert;
}

function _restoreInertSiblings(modalEl) {
    const newlyInert = modalEl._inertSiblings;
    const liftedInert = modalEl._liftedInert;
    if (Array.isArray(newlyInert)) {
        for (const sibling of newlyInert) {
            // Only remove inert if WE applied it — never strip an inert
            // that some outer modal placed on top of us.
            if (sibling.isConnected) {
                sibling.removeAttribute("inert");
            }
        }
    }
    if (Array.isArray(liftedInert)) {
        for (const sibling of liftedInert) {
            // We had temporarily un-inerted these because an outer
            // modal had inerted them. Now that this modal is closing,
            // hand them back to the outer modal in their inert state.
            if (sibling.isConnected) {
                sibling.setAttribute("inert", "");
            }
        }
    }
    modalEl._inertSiblings = null;
    modalEl._liftedInert = null;
}

function openModalAnimated(modalEl, { lockScroll = true } = {}) {
    if (!modalEl) return;
    // If the modal is already on-screen (e.g. re-render after state update),
    // don't acquire a second scroll-lock — the matching closeModalAnimated
    // would only release one count and leave body.modal-open stuck on.
    const wasHidden = modalEl.hidden;
    // Save the element that had focus before the modal opened so we can
    // restore it on close (WCAG 2.4.3 focus order).
    if (wasHidden && document.activeElement && document.activeElement !== document.body) {
        modalEl._previousFocus = document.activeElement;
    }
    // Make sure no leftover closing class from a previous run blocks the
    // entry animation.
    modalEl.classList.remove("is-closing");
    modalEl.hidden = false;
    if (lockScroll && wasHidden) lockBodyScroll();
    if (wasHidden) {
        // UX-M2: hide the page from assistive tech BEFORE focus moves
        // into the modal so screen readers announce only the dialog
        // content, not a mix of dialog + still-visible page chrome.
        _applyInertToSiblings(modalEl);
        const cleanup = trapFocus(modalEl);
        if (typeof cleanup === "function") {
            modalEl._focusTrapCleanup = cleanup;
        }
    }
}

function closeModalAnimated(modalEl, { lockScroll = true } = {}) {
    if (!modalEl || modalEl.hidden) return;

    // Bottom-sheet modals use `.detail-sheet`; centred dialogs use
    // `.modal-content`. Either way we wait for the inner panel's
    // animation to finish before flipping `[hidden]` back.
    const inner =
        modalEl.querySelector(".detail-sheet") || modalEl.querySelector(".modal-content");
    const prefersReducedMotion = _prefersReducedMotion();

    const finalize = () => {
        modalEl.hidden = true;
        modalEl.classList.remove("is-closing");
        if (lockScroll) unlockBodyScroll();
        if (typeof modalEl._focusTrapCleanup === "function") {
            modalEl._focusTrapCleanup();
            modalEl._focusTrapCleanup = null;
        }
        // UX-M2: pop ``inert`` from the siblings BEFORE restoring focus
        // — otherwise the previously-focused element is still inside an
        // inert subtree at the moment ``focus()`` is called and the
        // browser would refuse the focus move.
        _restoreInertSiblings(modalEl);
        const prev = modalEl._previousFocus;
        if (prev && typeof prev.focus === "function") {
            try { prev.focus(); } catch (_) { /* element may have been removed */ }
        }
        modalEl._previousFocus = null;
    };

    if (prefersReducedMotion || !inner) {
        finalize();
        return;
    }

    let done = false;
    const onEnd = (event) => {
        if (event && event.target !== inner) return;
        if (done) return;
        done = true;
        inner.removeEventListener("animationend", onEnd);
        finalize();
    };
    inner.addEventListener("animationend", onEnd);

    modalEl.classList.add("is-closing");

    // Belt-and-braces: even if animationend never fires (e.g. another
    // CSS animation overrides ours mid-run), finalise after the longest
    // animation we've defined (~280ms) + some slack.
    setTimeout(() => {
        if (done) return;
        done = true;
        inner.removeEventListener("animationend", onEnd);
        finalize();
    }, 360);
}

/* ─── Swipe-to-action gesture (Telegram-style) ──────────────────────────
 * Wraps any card in a swipe track, drags it horizontally with rubber-band
 * resistance, and triggers an action when released past the threshold.
 *
 * Usage:
 *   const wrapped = makeSwipeable(cardEl, {
 *       onSwipeLeft:  { label: 'Удалить',   className: 'swipe-bg--danger', action: () => ... },
 *       onSwipeRight: { label: 'В покупки', className: 'swipe-bg--accent', action: () => ... },
 *   });
 *   container.appendChild(wrapped);
 *
 * Returns the wrapper element. If the user prefers reduced motion the
 * card is returned unchanged — interactive buttons inside the card are
 * still functional, swipe is purely additive.
 *
 * `touch-action: pan-y` on the card lets the browser keep doing vertical
 * scrolling while we capture horizontal gestures, so the page never
 * feels stuck while a touch is in progress.
 */
function makeSwipeable(card, options) {
    if (!card) return card;
    if (_prefersReducedMotion()) return card;

    const onSwipeLeft = options?.onSwipeLeft || null;
    const onSwipeRight = options?.onSwipeRight || null;
    if (!onSwipeLeft && !onSwipeRight) return card;

    const thresholdPx = options?.thresholdPx ?? 80;

    const wrap = document.createElement("div");
    wrap.className = "swipe-wrap";

    function buildBg(spec, side) {
        if (!spec) return null;
        const bg = document.createElement("div");
        bg.className = `swipe-bg swipe-bg--${side} ${spec.className || ""}`.trim();
        const label = document.createElement("span");
        label.className = "swipe-bg-label";
        label.textContent = spec.label || "";
        bg.appendChild(label);
        return bg;
    }

    const bgRight = buildBg(onSwipeRight, "right"); // revealed by leftward drag
    const bgLeft = buildBg(onSwipeLeft, "left"); // revealed by rightward drag
    if (bgRight) wrap.appendChild(bgRight);
    if (bgLeft) wrap.appendChild(bgLeft);
    wrap.appendChild(card);
    card.classList.add("swipe-target");

    let startX = 0;
    let startY = 0;
    let dragging = false;
    let decided = false; // committed to horizontal vs vertical scroll
    let deltaX = 0;

    function reset() {
        card.classList.remove("swipe-dragging");
        card.style.transform = "";
        deltaX = 0;
    }

    function applyTransform(dx) {
        const limit = thresholdPx * 2;
        // Rubber-band past the limit so the gesture doesn't feel runaway.
        const limited =
            Math.abs(dx) > limit
                ? Math.sign(dx) * (limit + (Math.abs(dx) - limit) * 0.3)
                : dx;
        card.style.transform = `translateX(${limited}px)`;
    }

    function complete(direction) {
        const spec = direction === "left" ? onSwipeLeft : onSwipeRight;
        if (!spec || typeof spec.action !== "function") {
            reset();
            return;
        }
        // Light haptic so the user feels the action commit.
        const haptic = _tgHaptic();
        try {
            haptic?.impactOccurred?.("medium");
        } catch (_) {
            /* haptics not available outside Telegram */
        }
        // Slide the card off-screen, then call the action. The action is
        // expected to remove the row from state — the next render drops
        // the wrapper entirely so we don't need to clean up here.
        card.classList.add("swipe-completing");
        const sign = direction === "left" ? -1 : 1;
        card.style.transform = `translateX(${sign * 110}%)`;
        setTimeout(() => spec.action(), 180);
    }

    card.addEventListener(
        "touchstart",
        (event) => {
            if (event.touches.length !== 1) return;
            startX = event.touches[0].clientX;
            startY = event.touches[0].clientY;
            dragging = false;
            decided = false;
        },
        { passive: true },
    );

    card.addEventListener(
        "touchmove",
        (event) => {
            if (event.touches.length !== 1) return;
            const dx = event.touches[0].clientX - startX;
            const dy = event.touches[0].clientY - startY;
            if (!decided) {
                if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
                if (Math.abs(dy) > Math.abs(dx)) {
                    // It's a vertical scroll — bow out so the page can scroll.
                    decided = true;
                    return;
                }
                decided = true;
                dragging = true;
                card.classList.add("swipe-dragging");
            }
            if (dragging) {
                deltaX = dx;
                // Suppress drag in directions with no configured action.
                if (dx < 0 && !onSwipeLeft) deltaX = 0;
                if (dx > 0 && !onSwipeRight) deltaX = 0;
                applyTransform(deltaX);
            }
        },
        { passive: true },
    );

    function onEnd() {
        if (!dragging) {
            reset();
            return;
        }
        const finalDelta = deltaX;
        if (Math.abs(finalDelta) >= thresholdPx) {
            complete(finalDelta < 0 ? "left" : "right");
        } else {
            reset();
        }
        dragging = false;
        decided = false;
    }
    card.addEventListener("touchend", onEnd);
    card.addEventListener("touchcancel", () => {
        reset();
        dragging = false;
        decided = false;
    });

    return wrap;
}

/* ─── Pull-to-refresh (page-level, view-aware) ──────────────────────────
 * Native-feeling vertical pull at the top of the page that triggers a
 * refresh action. The handler is page-scoped — there's a single touch
 * listener tracking window.scrollY, and the refresh function is picked
 * by `getRefreshHandler()` based on the active view. That keeps each
 * surface's "what do I refresh" logic where it already lives without
 * fanning out a dozen separate gesture instances.
 *
 * Behaviour:
 *  - Only arms when the page is scrolled to the very top (scrollY ≤ 1).
 *  - Only commits when the user pans DOWN by ≥ THRESHOLD_PX (pulling up
 *    is a regular scroll, not a refresh).
 *  - Vertical-vs-horizontal detection on first move so it doesn't
 *    fight with the swipe-to-promote gesture on watching cards.
 *  - Skips when prefers-reduced-motion is on (the indicator depends on
 *    the rubber-band animation — without it the affordance is gone).
 *  - Skips when a modal is open (body.modal-open) so the gesture never
 *    fires inside a sheet.
 */
function setupPullToRefresh(options) {
    const {
        getRefreshHandler,
        thresholdPx = 70,
        maxPullPx = 140,
        indicatorEl,
    } = options || {};
    if (typeof getRefreshHandler !== "function") return () => {};

    if (_prefersReducedMotion()) return () => {};

    let startY = 0;
    let startX = 0;
    let dragging = false;
    let decided = false;
    let pullDistance = 0;
    let refreshing = false;

    function setIndicatorState(stage, ratio = 0) {
        if (!indicatorEl) return;
        indicatorEl.dataset.stage = stage;
        if (stage === "idle") {
            indicatorEl.style.transform = "";
            indicatorEl.style.opacity = "0";
        } else if (stage === "pulling") {
            const opacity = Math.min(1, ratio * 1.4).toFixed(2);
            indicatorEl.style.transform = `translateY(${pullDistance * 0.45}px)`;
            indicatorEl.style.opacity = opacity;
        } else if (stage === "ready") {
            indicatorEl.style.transform = `translateY(${pullDistance * 0.45}px)`;
            indicatorEl.style.opacity = "1";
        } else if (stage === "refreshing") {
            indicatorEl.style.transform = `translateY(${thresholdPx * 0.45}px)`;
            indicatorEl.style.opacity = "1";
        }
    }

    function reset() {
        pullDistance = 0;
        setIndicatorState("idle");
        dragging = false;
        decided = false;
    }

    function isEligible() {
        if (refreshing) return false;
        // Only arm at the very top of the page — anywhere else this is
        // a normal scroll gesture.
        if ((window.scrollY || window.pageYOffset || 0) > 1) return false;
        // Don't fire while a modal/sheet is up — the gesture would fight
        // the modal's own scroll lock.
        if (document.body.classList.contains("modal-open")) return false;
        // Don't capture inside form controls or scrollable inner panels
        // — let them keep working.
        return true;
    }

    function onTouchStart(event) {
        if (event.touches.length !== 1) return;
        if (!isEligible()) return;
        if (event.target.closest('.search-wrap, input, textarea, select')) return;
        startY = event.touches[0].clientY;
        startX = event.touches[0].clientX;
        dragging = false;
        decided = false;
        pullDistance = 0;
    }

    function onTouchMove(event) {
        if (event.touches.length !== 1) return;
        if (!isEligible() && !dragging) return;
            const dy = event.touches[0].clientY - startY;
            const dx = event.touches[0].clientX - startX;
            if (!decided) {
                if (Math.abs(dy) < 6 && Math.abs(dx) < 6) return;
                if (Math.abs(dx) > Math.abs(dy)) {
                    decided = true;
                    return;
                }
                if (dy <= 0) {
                    decided = true;
                    return;
                }
                decided = true;
                dragging = true;
            }
            if (!dragging) return;
            // Rubber-band so the pull feels like a finger-on-elastic.
            pullDistance = Math.min(maxPullPx, dy * 0.6);
            const ratio = pullDistance / thresholdPx;
            setIndicatorState(ratio >= 1 ? "ready" : "pulling", ratio);
    }

    document.addEventListener("touchstart", onTouchStart, { passive: true });
    document.addEventListener("touchmove", onTouchMove, { passive: true });

    function onEnd() {
        if (!dragging) {
            reset();
            return;
        }
        const committed = pullDistance >= thresholdPx;
        if (!committed) {
            // Spring back without firing.
            setIndicatorState("idle");
            setTimeout(() => {
                reset();
            }, 240);
            return;
        }
        // Commit.
        const haptic = _tgHaptic();
        try {
            haptic?.impactOccurred?.("light");
        } catch (_) {
            /* haptics not available outside Telegram */
        }
        refreshing = true;
        setIndicatorState("refreshing");

        const handler = getRefreshHandler();
        const cleanup = () => {
            setIndicatorState("idle");
            setTimeout(() => {
                refreshing = false;
                reset();
            }, 240);
        };
        Promise.resolve()
            .then(() => (typeof handler === "function" ? handler() : undefined))
            .catch(() => {
                /* refresh handler failure is surfaced via the regular
                   error toast; we just unstick the gesture. */
            })
            .finally(cleanup);
    }

    document.addEventListener("touchend", onEnd, { passive: true });
    document.addEventListener("touchcancel", reset, { passive: true });

    // Return an uninstall hook so callers/tests can unwire if they
    // really need to. Not used today but cheap to keep.
    return function uninstall() {
        document.removeEventListener("touchstart", onTouchStart);
        document.removeEventListener("touchmove", onTouchMove);
        document.removeEventListener("touchend", onEnd);
        document.removeEventListener("touchcancel", reset);
    };
}

/* ─── Pinch-zoom + pan for a single image ───────────────────────────────
 * Two-finger pinch scales an image up (1×–4×); a single-finger drag
 * pans it once it's zoomed; a double-tap toggles between 1× and 2×.
 * Sets `img.classList.add('is-zoomed')` while scale > 1 so adjacent
 * gestures (e.g. the swipe-between-photos handler in api_events.js)
 * can defer to the zoom interaction.
 *
 * Returns a small controller with .reset() so the caller can clear the
 * zoom when navigating to a different photo or closing the modal.
 *
 * Transform model:
 *   transform: translate3d(tx, ty, 0) scale(s)
 *   transform-origin: 0 0
 *
 * The image is positioned so that the point (px, py) in image-local
 * coordinates stays at the same viewport position when scale changes.
 * This gives a natural "zoom towards the pinch center" feel.
 *
 *   tx = viewportX - px * s
 *   ty = viewportY - py * s
 *
 * where (viewportX, viewportY) is the desired viewport position and
 * (px, py) is the point in the image's own coordinate system.
 */
function attachPinchZoom(img, options) {
    if (!img) return { reset: () => {} };
    const MIN = options?.minScale ?? 1;
    const MAX = options?.maxScale ?? 4;
    const DOUBLE_TAP = options?.doubleTapScale ?? 2;

    let s = 1;       // current scale
    let tx = 0;      // current translate X (viewport px)
    let ty = 0;      // current translate Y (viewport px)

    // Pinch gesture state
    let pinchStartDist = 0;
    let pinchStartScale = 1;
    let pinchAnchorPx = 0;   // image-local X of the pinch center
    let pinchAnchorPy = 0;   // image-local Y of the pinch center

    // Pan gesture state
    let panStartX = 0;
    let panStartY = 0;
    let panBaseTx = 0;
    let panBaseTy = 0;

    // Double-tap detection
    let lastTapTs = 0;
    let lastTapX = 0;
    let lastTapY = 0;

    let viewportSize = null;

    // Active gesture type to prevent pinch→pan jump
    let activeGesture = null; // "pinch" | "pan" | null

    function ensureViewportSize() {
        if (viewportSize) return viewportSize;
        const parent = img.closest(".detail-sheet-content, .ai-modal-body")
            || img.parentElement;
        viewportSize = {
            width: parent ? parent.clientWidth : window.innerWidth,
            height: parent ? parent.clientHeight : window.innerHeight,
        };
        return viewportSize;
    }

    function apply() {
        img.style.transformOrigin = "0 0";
        img.style.transform = `translate3d(${tx}px,${ty}px,0) scale(${s})`;
        if (s > 1.001) {
            img.classList.add("is-zoomed");
            img.style.willChange = "transform";
        } else {
            img.classList.remove("is-zoomed");
            img.style.willChange = "";
        }
    }

    function reset(animate) {
        s = 1;
        tx = 0;
        ty = 0;
        activeGesture = null;
        viewportSize = null;
        if (animate) {
            img.style.transition = "transform 220ms cubic-bezier(0.2,0.8,0.2,1)";
        } else {
            img.style.transition = "";
        }
        apply();
        if (animate) {
            setTimeout(() => { img.style.transition = ""; }, 240);
        }
    }

    /** Convert viewport coords to image-local coords using current state. */
    function viewportToImage(vx, vy) {
        return { x: (vx - tx) / s, y: (vy - ty) / s };
    }

    /** Clamp translate so the image cannot be dragged entirely off-screen.
     *  Uses the cached viewport dimensions as the untransformed image box:
     *  detail images fill their modal viewport, so this avoids a
     *  transform mutation plus a forced layout read. */
    function clampTranslate() {
        if (s <= 1) {
            tx = 0;
            ty = 0;
            return;
        }
        const viewport = ensureViewportSize();
        const vw = viewport.width;
        const vh = viewport.height;
        const iw = vw * s;
        const ih = vh * s;

        // The image rectangle in viewport coords is (tx, ty, iw, ih).
        // Require at least 40px of the image to remain visible on each side.
        const margin = 40;
        const left = tx;
        const right = tx + iw;
        const top = ty;
        const bottom = ty + ih;

        if (iw <= vw) {
            // Image narrower than viewport — center it
            tx = (vw - iw) / 2;
        } else if (left > margin) {
            tx = margin;
        } else if (right < vw - margin) {
            tx = vw - margin - iw;
        }

        if (ih <= vh) {
            ty = (vh - ih) / 2;
        } else if (top > margin) {
            ty = margin;
        } else if (bottom < vh - margin) {
            ty = vh - margin - ih;
        }
    }

    function dist(touches) {
        const dx = touches[0].clientX - touches[1].clientX;
        const dy = touches[0].clientY - touches[1].clientY;
        return Math.hypot(dx, dy);
    }

    // ── touchstart ──────────────────────────────────────────────────────
    img.addEventListener("touchstart", (e) => {
        if (e.touches.length === 2) {
            e.preventDefault();
            activeGesture = "pinch";
            ensureViewportSize();

            pinchStartDist = dist(e.touches);
            pinchStartScale = s;

            // Pinch center in viewport coords
            const cvx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
            const cvy = (e.touches[0].clientY + e.touches[1].clientY) / 2;

            // Convert to image-local coords — this is the anchor point
            const local = viewportToImage(cvx, cvy);
            pinchAnchorPx = local.x;
            pinchAnchorPy = local.y;

        } else if (e.touches.length === 1) {
            const t = e.touches[0];
            const now = Date.now();
            const dx = t.clientX - lastTapX;
            const dy = t.clientY - lastTapY;

            if (now - lastTapTs < 300 && Math.abs(dx) < 30 && Math.abs(dy) < 30) {
                e.preventDefault();
                if (s > 1.001) {
                    reset(true);
                } else {
                    ensureViewportSize();
                    // Zoom towards the tap point
                    const local = viewportToImage(t.clientX, t.clientY);
                    s = DOUBLE_TAP;
                    tx = t.clientX - local.x * s;
                    ty = t.clientY - local.y * s;
                    clampTranslate();
                    img.style.transition = "transform 220ms cubic-bezier(0.2,0.8,0.2,1)";
                    apply();
                    setTimeout(() => { img.style.transition = ""; }, 240);
                }
                lastTapTs = 0;
                return;
            }

            lastTapTs = now;
            lastTapX = t.clientX;
            lastTapY = t.clientY;

            if (s > 1.001) {
                activeGesture = "pan";
                ensureViewportSize();
                panStartX = t.clientX;
                panStartY = t.clientY;
                panBaseTx = tx;
                panBaseTy = ty;
            }
        }
    }, { passive: false });

    // ── touchmove ───────────────────────────────────────────────────────
    img.addEventListener("touchmove", (e) => {
        if (e.touches.length === 2 && activeGesture === "pinch") {
            e.preventDefault();
            if (pinchStartDist <= 0) return;

            const d = dist(e.touches);
            let nextS = pinchStartScale * (d / pinchStartDist);
            nextS = Math.max(MIN, Math.min(MAX, nextS));

            // Recompute the viewport position of the pinch center
            // so it tracks the moving fingers
            const cvx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
            const cvy = (e.touches[0].clientY + e.touches[1].clientY) / 2;

            // Keep the anchor image point under the pinch center
            tx = cvx - pinchAnchorPx * nextS;
            ty = cvy - pinchAnchorPy * nextS;
            s = nextS;

            clampTranslate();
            apply();

        } else if (e.touches.length === 1 && activeGesture === "pan" && s > 1.001) {
            e.preventDefault();
            tx = panBaseTx + (e.touches[0].clientX - panStartX);
            ty = panBaseTy + (e.touches[0].clientY - panStartY);
            clampTranslate();
            apply();
        }
    }, { passive: false });

    // ── touchend ────────────────────────────────────────────────────────
    img.addEventListener("touchend", (e) => {
        // If one finger lifted from a pinch and one remains, switch to pan
        if (e.touches.length === 1 && activeGesture === "pinch") {
            activeGesture = "pan";
            panStartX = e.touches[0].clientX;
            panStartY = e.touches[0].clientY;
            panBaseTx = tx;
            panBaseTy = ty;
        }
        // All fingers lifted — snap back if barely zoomed
        if (e.touches.length === 0) {
            if (s < 1.05 && s !== 1) {
                reset(true);
            }
            activeGesture = null;
        }
        pinchStartDist = 0;
    }, { passive: true });

    img.addEventListener("touchcancel", () => reset(true), { passive: true });

    return {
        reset: (animate = true) => reset(animate),
        get scale() { return s; },
    };
}

/* ─── Long-press action menu ────────────────────────────────────────────
 * Holding a touch on a target for ≥ THRESHOLD_MS pops a small bottom-
 * sheet menu with up to a handful of actions. Useful for cluttered
 * listing cards where dedicating screen space to "В покупки" /
 * "В избранное" / "Скрыть" buttons would steal too many pixels.
 *
 * Behaviour:
 *  - Cancels the press if the user moves more than ~10 px (treat as a
 *    pan/scroll, not an intent to invoke the menu).
 *  - Cancels if a second finger touches down (probably a pinch
 *    elsewhere on the page).
 *  - Suppresses the synthetic click that would otherwise fire on
 *    touchend after a long press, so a card's tap handler doesn't
 *    also navigate.
 *  - Single shared overlay <div> attached to <body> on first call —
 *    avoids creating a fresh menu node per interaction.
 *  - Closing: tap on backdrop, Escape key, or selecting an item.
 */

let _longPressOverlay = null;
let _longPressEscHandler = null;

function _ensureLongPressOverlay() {
    if (_longPressOverlay) return _longPressOverlay;
    const overlay = document.createElement("div");
    overlay.className = "lp-menu-overlay";
    overlay.setAttribute("hidden", "");
    overlay.setAttribute("role", "presentation");
    const sheet = document.createElement("div");
    sheet.className = "lp-menu-sheet";
    sheet.setAttribute("role", "menu");
    overlay.appendChild(sheet);
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (event) => {
        if (event.target === overlay) hideLongPressMenu();
    });
    _longPressOverlay = overlay;
    return overlay;
}

function showLongPressMenu(items) {
    if (!Array.isArray(items) || !items.length) return;
    const overlay = _ensureLongPressOverlay();
    const sheet = overlay.querySelector(".lp-menu-sheet");
    sheet.replaceChildren();
    for (const item of items) {
        if (!item || typeof item.label !== "string") continue;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = `lp-menu-item ${item.tone ? `lp-menu-item--${item.tone}` : ""}`.trim();
        btn.setAttribute("role", "menuitem");
        if (item.icon) {
            const iconBox = document.createElement("span");
            iconBox.className = "lp-menu-icon";
            const svg = String(item.icon);
            if (svg.startsWith("<svg")) {
                try {
                    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
                    const svgEl = doc.querySelector("svg");
                    if (svgEl && !doc.querySelector("parsererror")) {
                        const allowedTags = new Set([
                            "svg", "path", "circle", "rect", "line", "polyline",
                            "polygon", "ellipse", "g", "defs", "title", "desc",
                        ]);
                        const allowedAttrs = new Set([
                            "viewbox", "width", "height", "fill", "stroke",
                            "stroke-width", "stroke-linecap", "stroke-linejoin",
                            "d", "cx", "cy", "r", "x", "y", "x1", "y1", "x2", "y2",
                            "points", "rx", "ry", "xmlns", "fill-rule", "clip-rule",
                            "opacity", "transform",
                        ]);
                        let safe = true;
                        const walk = (node) => {
                            if (node.nodeType === 1) {
                                if (!allowedTags.has(node.tagName.toLowerCase())) {
                                    safe = false; return;
                                }
                                for (const attr of Array.from(node.attributes)) {
                                    if (!allowedAttrs.has(attr.name.toLowerCase())) {
                                        safe = false; return;
                                    }
                                }
                            }
                            for (const child of node.childNodes) walk(child);
                        };
                        walk(svgEl);
                        if (safe) iconBox.innerHTML = svgEl.outerHTML;
                    }
                } catch (_) { /* reject unparseable SVG */ }
            }
            btn.appendChild(iconBox);
        }
        const label = document.createElement("span");
        label.className = "lp-menu-label";
        label.textContent = item.label;
        btn.appendChild(label);
        btn.addEventListener("click", (event) => {
            event.stopPropagation();
            hideLongPressMenu();
            try {
                if (typeof item.onSelect === "function") item.onSelect();
            } catch (err) {
                console.error("long-press menu action failed", err);
            }
        });
        sheet.appendChild(btn);
    }
    overlay.removeAttribute("hidden");
    document.body.classList.add("lp-menu-open");
    // Esc closes too — reuse the existing modal-close idiom.
    _longPressEscHandler = (event) => {
        if (event.key === "Escape") hideLongPressMenu();
    };
    document.addEventListener("keydown", _longPressEscHandler);
}

function hideLongPressMenu() {
    if (!_longPressOverlay) return;
    _longPressOverlay.setAttribute("hidden", "");
    document.body.classList.remove("lp-menu-open");
    if (_longPressEscHandler) {
        document.removeEventListener("keydown", _longPressEscHandler);
        _longPressEscHandler = null;
    }
}

function attachLongPress(target, getItems, options) {
    if (!target || typeof getItems !== "function") return;
    const thresholdMs = options?.thresholdMs ?? 480;
    const moveTolerancePx = options?.moveTolerancePx ?? 10;

    let startX = 0;
    let startY = 0;
    let timer = null;
    let suppressClick = false;

    function clear() {
        if (timer !== null) {
            clearTimeout(timer);
            timer = null;
        }
    }

    target.addEventListener(
        "touchstart",
        (event) => {
            if (event.touches.length !== 1) {
                clear();
                return;
            }
            startX = event.touches[0].clientX;
            startY = event.touches[0].clientY;
            suppressClick = false;
            clear();
            timer = setTimeout(() => {
                timer = null;
                suppressClick = true;
                const haptic = _tgHaptic();
                try {
                    haptic?.impactOccurred?.("medium");
                } catch (_) {
                    /* haptics not available */
                }
                const items = getItems() || [];
                if (items.length) showLongPressMenu(items);
            }, thresholdMs);
        },
        { passive: true },
    );

    target.addEventListener(
        "touchmove",
        (event) => {
            if (timer === null) return;
            if (event.touches.length !== 1) {
                clear();
                return;
            }
            const dx = event.touches[0].clientX - startX;
            const dy = event.touches[0].clientY - startY;
            if (Math.abs(dx) > moveTolerancePx || Math.abs(dy) > moveTolerancePx) {
                clear();
            }
        },
        { passive: true },
    );

    target.addEventListener("touchend", clear, { passive: true });
    target.addEventListener("touchcancel", clear, { passive: true });

    target.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const items = getItems() || [];
        if (items.length) showLongPressMenu(items);
    });

    // FE-H5/UX-H2: Keyboard support for elements wearing role="button".
    //  * ContextMenu / Shift+F10 → open the action sheet (matches
    //    right-click), so keyboard users can reach the actions that
    //    were previously only exposed via long-press.
    //  * Enter / Space → same (WAI-ARIA 1.2 "button" pattern requires
    //    these to activate the element). Without this the listing
    //    cards we mark with role=button were a dead-end for screen
    //    readers: focus-visible showed but pressing Enter did nothing.
    // Guards: we only react when the target is the card itself, so
    // typing in the inline <input> or hitting Space on a nested
    // <button> stays untouched.
    target.addEventListener("keydown", (e) => {
        const isActionKey =
            e.key === "ContextMenu" ||
            (e.shiftKey && e.key === "F10") ||
            e.key === "Enter" ||
            e.key === " ";
        if (!isActionKey) return;
        if (e.target !== target) return;
        // Space scrolls the page by default; stop both just for cards.
        e.preventDefault();
        const items = getItems() || [];
        if (items.length) showLongPressMenu(items);
    });

    // The synthesised mousedown→mouseup→click sequence still fires
    // after a touchend, so guard the host element's click handlers
    // against a long-press that already opened the menu.
    target.addEventListener(
        "click",
        (event) => {
            if (suppressClick) {
                event.preventDefault();
                event.stopImmediatePropagation();
                suppressClick = false;
            }
        },
        true, // capture phase so we beat the action handlers below
    );
}


;
const APP_REGIONS = {
    "Минск": ["Заводской", "Ленинский", "Московский", "Октябрьский", "Партизанский", "Первомайский", "Советский", "Фрунзенский", "Центральный"],
    "Брестская область": ["Брест", "Барановичи", "Береза", "Ганцевичи", "Дрогичин", "Жабинка", "Иваново", "Ивацевичи", "Каменец", "Кобрин", "Лунинец", "Ляховичи", "Малорита", "Пинск", "Пружаны", "Столин"],
    "Витебская область": ["Витебск", "Бешенковичи", "Браслав", "Верхнедвинск", "Глубокое", "Городок", "Докшицы", "Дубровно", "Лепель", "Лиозно", "Миоры", "Новополоцк", "Орша", "Полоцк", "Поставы", "Россоны", "Сенно", "Толочин", "Ушачи", "Чашники", "Шарковщина", "Шумилино"],
    "Гомельская область": ["Гомель", "Брагин", "Буда-Кошелево", "Ветка", "Добруш", "Ельск", "Житковичи", "Жлобин", "Калинковичи", "Корма", "Лельчицы", "Лоев", "Мозырь", "Наровля", "Октябрьский", "Петриков", "Речица", "Рогачев", "Светлогорск", "Хойники", "Чечерск"],
    "Гродненская область": ["Гродно", "Берестовица", "Волковыск", "Вороново", "Дятлово", "Зельва", "Ивье", "Кореличи", "Лида", "Мосты", "Новогрудок", "Островец", "Ошмяны", "Свислочь", "Слоним", "Сморгонь", "Щучин"],
    "Минская область": ["Минский", "Березино", "Борисов", "Вилейка", "Воложин", "Дзержинск", "Жодино", "Клецк", "Копыль", "Крупки", "Логойск", "Любань", "Марьина Горка", "Молодечно", "Мядель", "Несвиж", "Слуцк", "Смолевичи", "Солигорск", "Старые Дороги", "Столбцы", "Узда", "Червень"],
    "Могилевская область": ["Могилев", "Белыничи", "Бобруйск", "Быхов", "Глуск", "Горки", "Дрибин", "Кировск", "Климовичи", "Кличев", "Костюковичи", "Краснополье", "Кричев", "Круглое", "Мстиславль", "Осиповичи", "Славгород", "Хотимск", "Чаусы", "Чериков", "Шклов"],
};

function cacheAppElements(elements) {
    elements.searchInput = document.getElementById("search-input");
    elements.searchButton = document.getElementById("search-btn");
    elements.searchButtonLabel = document.getElementById("search-btn-label");
    elements.strictSearchToggle = document.getElementById("strict-search-toggle");
    elements.errorBar = document.getElementById("error-bar");
    elements.errorText = document.getElementById("error-text");
    elements.errorRetry = document.getElementById("error-retry");
    elements.helperPanel = document.getElementById("helper-panel");
    elements.summaryStrip = document.getElementById("summary-strip");
    elements.summaryQuery = document.getElementById("summary-query");
    elements.summarySignal = document.getElementById("summary-signal");
    elements.summaryMedian = document.getElementById("summary-median");
    elements.summaryRange = document.getElementById("summary-range");
    elements.summaryFair = document.getElementById("summary-fair");
    elements.summaryRefinements = document.getElementById("summary-refinements");
    elements.summaryRefinementsChips = document.getElementById("summary-refinements-chips");
    elements.viewTabs = Array.from(document.querySelectorAll("[data-view]"));
    elements.views = {
        overview: document.getElementById("overview-view"),
        ads: document.getElementById("ads-view"),
        tracking: document.getElementById("tracking-view"),
        deals: document.getElementById("deals-view"),
    };
    elements.dealsControls = document.getElementById("deals-controls");
    elements.statsSection = document.getElementById("stats-section");
    elements.chartSection = document.getElementById("chart-section");
    elements.priceChartCanvas = document.getElementById("priceChart");
    elements.historyChartCanvas = document.getElementById("historyChart");
    elements.historySection = document.getElementById("history-section");
    elements.historyEmpty = document.getElementById("history-empty");
    elements.historyBadge = document.getElementById("history-badge");
    elements.historySummary = document.getElementById("history-summary");
    elements.historyRangeButtons = Array.from(document.querySelectorAll("[data-history-days]"));
    elements.segmentsSection = document.getElementById("segments-section");
    elements.geographySection = document.getElementById("geography-section");
    elements.geographyGrid = document.getElementById("geography-grid");
    elements.geographyNote = document.getElementById("geography-note");
    elements.listingsSection = document.getElementById("listings-section");
    elements.marketTotalBadge = document.getElementById("market-total-badge");
    elements.listingsTotalBadge = document.getElementById("listings-total-badge");
    elements.listingsFallbackBadge = document.getElementById("listings-fallback-badge");
    elements.stats = {
        median: document.getElementById("stat-median"),
        mean: document.getElementById("stat-mean"),
        min: document.getElementById("stat-min"),
        max: document.getElementById("stat-max"),
        coverage: document.getElementById("stat-coverage"),
        fairRange: document.getElementById("stat-fair-range"),
    };
    elements.segmentsGrid = document.getElementById("segments-grid");
    elements.listingsList = document.getElementById("listings-list");
    elements.sortButtons = Array.from(document.querySelectorAll("[data-sort]"));
    elements.discountButtons = Array.from(document.querySelectorAll("[data-discount-from]"));
    elements.trackerEventFilterButtons = Array.from(document.querySelectorAll("[data-event-filter]"));
    // Per-filter count badges, keyed by filter name ("all" | "price_drop" |
    // "new_listing"). Updated in renderTrackerEventFilters so the user
    // sees how many alerts each tab represents before tapping it.
    elements.trackerEventFilterCounts = {};
    for (const node of document.querySelectorAll("[data-event-filter-count]")) {
        elements.trackerEventFilterCounts[node.dataset.eventFilterCount] = node;
    }
    elements.dealFromInput = document.getElementById("deal-from-input");
    elements.dealToInput = document.getElementById("deal-to-input");
    elements.dealApplyButton = document.getElementById("deal-apply-btn");
    elements.trackerPanel = document.getElementById("tracker-panel");
    elements.trackQueryButton = document.getElementById("track-query-btn");
    elements.trackerMinDiscountInput = document.getElementById("tracker-min-discount-input");
    elements.trackerMaxPriceInput = document.getElementById("tracker-max-price-input");
    elements.trackerSellerSelect = document.getElementById("tracker-seller-select");
    elements.trackerConditionSelect = document.getElementById("tracker-condition-select");
    elements.trackerRegionSelect = document.getElementById("tracker-region-select");
    elements.trackerConfigInput = document.getElementById("tracker-config-input");
    elements.trackerStatus = document.getElementById("tracker-status");
    elements.trackersList = document.getElementById("trackers-list");
    elements.trackerEventsList = document.getElementById("tracker-events-list");
    elements.clearEventsButton = document.getElementById("clear-events-btn");
    elements.trackerEventTrackerSelect = document.getElementById("tracker-event-tracker-select");
    elements.leadInboxSection = document.getElementById("lead-inbox-section");
    elements.leadFilterButtons = Array.from(document.querySelectorAll("[data-lead-filter]"));
    elements.leadInboxList = document.getElementById("lead-inbox-list");
    elements.clearAllLeadsButton = document.getElementById("clear-all-leads-btn");
    // Watchlist section is gone — its items live inside the unified
    // "Мои объявления" list now. We keep the deleteAllWatchlistButton
    // reference so JS can still bind a handler when the chip "Слежу"
    // is the active filter.
    elements.deleteAllWatchlistButton = document.getElementById("delete-all-watchlist-btn");
    elements.itemsFilterRow = document.getElementById("items-filter-row");
    elements.itemsFilterButtons = Array.from(document.querySelectorAll("[data-items-filter]"));
    elements.itemsCountBadges = {};
    for (const node of document.querySelectorAll("[data-items-count]")) {
        elements.itemsCountBadges[node.dataset.itemsCount] = node;
    }
    elements.trackingHeroStats = document.getElementById("tracking-hero-stats");
    elements.dealsHeroStats = document.getElementById("deals-hero-stats");
    elements.detailModal = document.getElementById("detail-modal");
    elements.detailOverlay = document.getElementById("detail-overlay");
    elements.detailClose = document.getElementById("detail-close");
    elements.detailMainImage = document.getElementById("detail-main-image");
    elements.detailMedia = document.getElementById("detail-media");
    elements.detailNoImage = document.getElementById("detail-no-image");
    elements.detailThumbs = document.getElementById("detail-thumbs");
    elements.detailTitle = document.getElementById("detail-title");
    elements.detailPrice = document.getElementById("detail-price");
    elements.detailMeta = document.getElementById("detail-meta");
    elements.detailDescription = document.getElementById("detail-description");
    elements.detailProfitBlock = document.getElementById("detail-profit-block");
    elements.detailProfit = document.getElementById("detail-profit");
    elements.detailLiquidityBlock = document.getElementById("detail-liquidity-block");
    elements.detailLiquidity = document.getElementById("detail-liquidity");
    elements.detailAddLeadButton = document.getElementById("detail-add-lead-btn");
    elements.detailAddWatchlistButton = document.getElementById("detail-add-watchlist-btn");
    elements.detailLink = document.getElementById("detail-link");
    elements.detailParamsBlock = document.getElementById("detail-params-block");
    elements.detailParams = document.getElementById("detail-params");
    elements.detailSellerBlock = document.getElementById("detail-seller-block");
    elements.detailSeller = document.getElementById("detail-seller");
    elements.detailAiBlock = document.getElementById("detail-ai-block");
    elements.detailAiContent = document.getElementById("detail-ai-content");
    elements.detailAiBtn = document.getElementById("detail-ai-btn");
    elements.expensesModal = document.getElementById("expenses-modal");
    elements.expensesOverlay = document.getElementById("expenses-overlay");
    elements.expensesClose = document.getElementById("expenses-close");
    elements.expensesTitle = document.getElementById("expenses-title");
    elements.expensesSubtitle = document.getElementById("expenses-subtitle");
    elements.expensesList = document.getElementById("expenses-list");
    elements.expenseFormWrap = document.getElementById("expense-form-wrap");
    elements.expenseTypeSelect = document.getElementById("expense-type-select");
    elements.expenseAmountInput = document.getElementById("expense-amount-input");
    elements.expenseNotesInput = document.getElementById("expense-notes-input");
    elements.saveExpenseButton = document.getElementById("save-expense-btn");
    elements.cancelExpenseButton = document.getElementById("cancel-expense-btn");
    elements.aiModal = document.getElementById("ai-modal");
    elements.aiOverlay = document.getElementById("ai-overlay");
    elements.aiModalClose = document.getElementById("ai-modal-close");
    elements.aiModalSubtitle = document.getElementById("ai-modal-subtitle");
    elements.aiModalLoading = document.getElementById("ai-modal-loading");
    elements.aiLoaderText = document.getElementById("ai-loader-text");
    elements.aiProgressBar = document.getElementById("ai-progress-bar");
    elements.aiProgressPct = document.getElementById("ai-progress-pct");
    elements.aiModalError = document.getElementById("ai-modal-error");
    elements.aiModalResult = document.getElementById("ai-modal-result");
    elements.profitDashboardSection = document.getElementById("profit-dashboard-section");
    elements.profitCards = document.getElementById("profit-cards");
    elements.profitChartBox = document.getElementById("profit-chart-box");
    elements.analyticsPeriodButtons = Array.from(
        document.querySelectorAll("[data-analytics-period]"),
    );
    elements.historyDealsSection = document.getElementById("history-deals-section");
    elements.historyDealsCount = document.getElementById("history-deals-count");
    elements.historyDealsList = document.getElementById("history-deals-list");
    elements.toastContainer = document.getElementById("toast-container");
    elements.recentSection = document.getElementById("recent-section");
    elements.recentList = document.getElementById("recent-list");
    elements.recentClearBtn = document.getElementById("recent-clear-btn");
    elements.filterBtn = document.getElementById("filter-btn");
    elements.filterDropdown = document.getElementById("filter-dropdown");
    elements.filterCategories = document.getElementById("filter-categories");
    elements.filterConditions = document.getElementById("filter-conditions");
    elements.filterSellers = document.getElementById("filter-sellers");
    elements.filterMinPrice = document.getElementById("filter-min-price");
    elements.filterMaxPrice = document.getElementById("filter-max-price");
    elements.filterRegion = document.getElementById("filter-region");
    elements.filterApplyBtn = document.querySelector(".filter-btn--apply");
    elements.filterCancelBtn = document.querySelector(".filter-btn--cancel");
    elements.editTrackerModal = document.getElementById("edit-tracker-modal");
    elements.editTrackerQuery = document.getElementById("edit-tracker-query");
    elements.editStrictModeToggle = document.getElementById("edit-strict-mode-toggle");
    elements.editMinDiscountInput = document.getElementById("edit-min-discount-input");
    elements.editMaxPriceInput = document.getElementById("edit-max-price-input");
    elements.editSellerSelect = document.getElementById("edit-seller-select");
    elements.editConditionSelect = document.getElementById("edit-condition-select");
    elements.editRegionSelect = document.getElementById("edit-region-select");
    elements.editConfigInput = document.getElementById("edit-config-input");
    elements.closeEditModal = document.getElementById("close-edit-modal");
    elements.saveTrackerBtn = document.getElementById("save-tracker-btn");
    elements.cancelEditBtn = document.getElementById("cancel-edit-btn");
    elements.panelToggles = Array.from(document.querySelectorAll("[data-panel-toggle]"));
    elements.panelBodies = {
        distribution: document.getElementById("distribution-body"),
        history: document.getElementById("history-body"),
        segments: document.getElementById("segments-body"),
        geography: document.getElementById("geography-body"),
        historyDeals: document.getElementById("history-deals-body"),
    };
}

function populateRegionSelectOptions(selectEl, currentValue) {
    if (!selectEl) return;
    // Requires: domEl() from dom_helpers.js (loaded before this file)
    const prev = currentValue || "";
    const fragment = document.createDocumentFragment();
    fragment.appendChild(domEl("option", { value: "", text: "Любой" }));
    for (const [region, cities] of Object.entries(APP_REGIONS)) {
        const group = domEl("optgroup", { attrs: { label: region } });
        group.appendChild(domEl("option", { value: region, text: `${region} (все)` }));
        for (const city of cities) {
            group.appendChild(domEl("option", { value: city, text: city }));
        }
        fragment.appendChild(group);
    }
    selectEl.replaceChildren(fragment);
    selectEl.value = prev;
}


;
function createAppCore() {
    const state = {
        ui: {
            loading: false,
            error: null,
            activeView: "overview",
            dirtyViews: new Set(),
            _allDirty: true,
        },
        search: {
            query: "",
            strictSearch: true,
            searchRequestId: 0,
            sort: "newest",
            recentSearches: [],
            searchAbortController: null,
        },
        filters: {
            category: null, // selected category id (int or null)
            categories: [], // category distribution from last search [{id, label, count}]
            condition: "", // filter by condition: "", "new", "used"
            sellerType: "", // filter by seller: "", "private", "shop"
            minPrice: null, // filter by min price (number or null)
            maxPrice: null, // filter by max price (number or null)
            regionName: "", // filter by region name
            // Pending filter values (before Apply is clicked)
            pendingCategory: null,
            pendingCondition: "",
            pendingSellerType: "",
            pendingMinPrice: null,
            pendingMaxPrice: null,
            pendingRegionName: "",
            filterDropdownOpen: false,
            discountFromPercent: 10,
            discountToPercent: 30,
        },
        listings: {
            items: [],
            _loadedAt: 0,
            _loadedSort: null,
            total: 0,
            hasMore: false,
            loading: false,
            loadingMore: false,
            _requestId: 0,
            _pending: false,
            fallbackUsed: false,
        },
        trackers: {
            items: [],
            events: [],
            eventFilter: "all",
            eventFilterTrackerId: null,
            status: "",
            statusKind: "info",
            editingId: null,
            creating: false,
            minDiscountPercent: 10,
            maxPriceByn: null,
            sellerType: "",
            condition: "",
            regionName: "",
            configKeyword: "",
        },
        leads: {
            items: [],
            filter: "all",
            _requestId: 0,
            pipelineStep: "active",
            itemsFilter: "purchases",
        },
        watchlist: {
            items: [],
            filter: "all",
            _requestId: 0,
        },
        detail: {
            data: null,
            imageIndex: 0,
            fromWatchlist: false,
            _requestId: 0,
            ai: {
                adId: null,
                loading: false,
                result: null,
                error: "",
                source: "",
            },
            aiLoadingTimer: null,
        },
        expenses: {
            items: [],
            loading: false,
            currentLeadId: null,
        },
        analytics: {
            periodDays: 90,
            dashboard: null,
            loading: false,
            profitData: null,
            profitChart: null,
        },
        panels: {
            distribution: false,
            history: true,
            segments: false,
            geography: false,
            historyDeals: false,
            listingAssistant: false,
        },
        charts: {
            distribution: null,
            history: null,
            historyData: [],
            _historyRequestId: 0,
        },
        misc: {
            historyDays: 7,
            currency: "BYN",
            segments: null,
            geography: [],
            listingAssistantResult: null,
            modalCleanup: null,
            stats: null,

        },
    };

    const elements = {};

    function cacheElements() {
        cacheAppElements(elements);
    }

    /**
     * Pick a starting theme. We mirror Telegram's coarse dark/light
     * preference (so a user who has Telegram in light mode opens the
     * Mini App in light mode by default), but we DO NOT inherit
     * Telegram's individual theme colours — the Mini App keeps its
     * own palette so the brand stays consistent across clients.
     *
     * Manual `localStorage.theme` always wins over the heuristic so a
     * user who explicitly toggled the theme keeps their choice.
     */
    function initTelegramTheme() {
        const saved = localStorage.getItem("theme");
        const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
        if (saved === "light" || saved === "dark") {
            document.documentElement.setAttribute("data-theme", saved);
        }
        if (tg) {
            try {
                tg.expand();
                tg.ready();
            } catch (_) {
                // ready/expand can throw outside a real Telegram client
            }
            // Override Telegram's injected background color with our own.
            // Telegram WebApp JS sets body.style.backgroundColor to the
            // user's theme bg_color (often blue), which breaks our dark
            // palette. We force our own bg after Telegram has initialised.
            const isDark = (saved || (tg.colorScheme !== "light")) === "dark";
            const ourBg = isDark ? "#0a0a0b" : "#ffffff";
            // setBackgroundColor requires Telegram WebApp version >= 6.1
            if (tg.version && parseFloat(tg.version) >= 6.1) {
                try { tg.setBackgroundColor(ourBg); } catch (_) {}
            }
            document.body.style.backgroundColor = ourBg;

            if (!saved) {
                const scheme = tg.colorScheme;
                document.documentElement.setAttribute(
                    "data-theme",
                    scheme === "light" ? "light" : "dark"
                );
            }
            // React to the user toggling dark/light in the Telegram
            // client without a reload — but only swap our binary mode,
            // never override individual palette variables.
            try {
                tg.onEvent?.("themeChanged", () => {
                    if (localStorage.getItem("theme")) return;
                    const s = tg.colorScheme;
                    document.documentElement.setAttribute(
                        "data-theme",
                        s === "light" ? "light" : "dark"
                    );
                    // Re-assert our background colour after Telegram
                    // re-injects its theme params on themeChanged.
                    const bg = s === "light" ? "#ffffff" : "#0a0a0b";
                    try {
                        if (tg.version && parseFloat(tg.version) >= 6.1) {
                            tg.setBackgroundColor(bg);
                        }
                    } catch (_) {}
                    document.body.style.backgroundColor = bg;
                });
            } catch (_) {
                // onEvent missing on older WebApp builds — non-fatal
            }
            return;
        }
        document.documentElement.setAttribute("data-theme", "dark");
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute("data-theme");
        const next = current === "light" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", next);
        localStorage.setItem("theme", next);
        // Re-assert our background colour so Telegram's injected
        // bg_color doesn't leak through after a theme toggle.
        const bg = next === "light" ? "#ffffff" : "#0a0a0b";
        document.body.style.backgroundColor = bg;
        try {
            const tg = window.Telegram && window.Telegram.WebApp;
            if (tg && tg.version && parseFloat(tg.version) >= 6.1) {
                tg.setBackgroundColor(bg);
            }
        } catch (_) {}
    }

    function formatPrice(value, priceType) {
        if (priceType === "negotiable" || (value == null && priceType !== "free")) return "Договорная";
        if (priceType === "free" || value === 0) return "Бесплатно";
        const numeric = Number(value);
        if (Number.isNaN(numeric)) return "Договорная";

        if (numeric >= 10000) {
            const formatted = Number(numeric / 1000).toLocaleString("ru-RU", {
                maximumFractionDigits: 1,
                minimumFractionDigits: 0,
            });
            return `${formatted} тыс. р.`;
        }
        if (numeric >= 1000) {
            const formatted = Number(numeric / 1000).toLocaleString("ru-RU", {
                maximumFractionDigits: 2,
                minimumFractionDigits: 0,
            });
            return `${formatted} тыс. р.`;
        }
        if (numeric >= 100) {
            return `${Math.round(numeric)} р.`;
        }
        // Small amounts — show up to 2 decimals (e.g. 6.5 р., 0.99 р.)
        return `${numeric.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} р.`;
    }

    function formatCondition(condition) {
        const map = {
            "Новый": "Новый",
            "Б/у": "Б/у",
            new: "Новый",
            used: "Б/у",
            "1": "Б/у",
            "2": "Новый",
        };
        return map[condition] || "";
    }

    function formatSeller(seller) {
        const map = {
            "Частное лицо": "Частное",
            "Магазин": "Магазин",
            private: "Частное",
            shop: "Магазин",
        };
        return map[seller] || "";
    }

    function formatDelta(delta) {
        if (delta == null || Math.abs(delta) < 0.5) return "";
        const sign = delta > 0 ? "+" : "";
        return `${sign}${Math.round(delta)}%`;
    }

    function deltaClass(delta) {
        if (!delta || Math.abs(delta) < 0.5) return "";
        return delta > 0 ? "over" : "under";
    }

    const _dateFormatter = new Intl.DateTimeFormat("ru-BY", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
    });

    function formatDate(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return _dateFormatter.format(date);
    }

    function hasTelegramInitData() {
        return Boolean(
            window.Telegram &&
            window.Telegram.WebApp &&
            window.Telegram.WebApp.initData
        );
    }

    function loadRecentSearches() {
        try {
            const stored = localStorage.getItem("recentSearches");
            if (stored) {
                state.search.recentSearches = JSON.parse(stored).slice(0, 10);
            }
        } catch (_) {
            state.search.recentSearches = [];
        }
    }

    function saveRecentSearches() {
        try {
            localStorage.setItem("recentSearches", JSON.stringify(state.search.recentSearches));
        } catch (e) {
            if (e.name === "QuotaExceededError" && state.search.recentSearches.length > 1) {
                state.search.recentSearches = state.search.recentSearches.slice(0, Math.ceil(state.search.recentSearches.length / 2));
                try {
                    localStorage.setItem("recentSearches", JSON.stringify(state.search.recentSearches));
                } catch (_) {
                    // Give up after retry
                }
            }
        }
    }

    function addRecentSearch(query) {
        if (!query || query.trim().length < 2) return;
        const trimmed = query.trim();
        // Remove if already exists
        state.search.recentSearches = state.search.recentSearches.filter((q) => q !== trimmed);
        // Add to front
        state.search.recentSearches.unshift(trimmed);
        // Keep only last 10
        state.search.recentSearches = state.search.recentSearches.slice(0, 10);
        saveRecentSearches();
    }

    function clearRecentSearches() {
        state.search.recentSearches = [];
        saveRecentSearches();
    }

    /**
     * Performance monitoring utility — measures render time in development.
     * Returns elapsed ms when the cleanup function is called.
     *
     * @param {string} name - Human-readable operation name
     * @param {number} [thresholdMs=100] - Warn threshold for console.warn
     * @returns {Function} Cleanup function that returns elapsed ms
     */
    function measureRender(name, thresholdMs = 100) {
        const start = performance.now();
        return function () {
            const elapsed = performance.now() - start;
            // Slow-render diagnostic is available via the returned elapsed
            // value; console.warn removed to avoid noise on weak devices.
            return elapsed;
        };
    }

    /**
     * Mark one or more views as needing re-render.
     * Views: 'error','loading','currency','strict','tabs','panels','summary',
     * 'helper','views','trackingHero','dealsHero',
     * 'sort','discount','eventFilters','dealInputs','trackerInputs','stats',
     * 'history','segments','geography','recent','listings','deals',
     * 'rates','trackerStatus','trackers','trackerEvents','leads','watchlist',
     * 'profit'.
     * Call without args or with 'all' to mark everything dirty.
     */
    function markDirty() {
        const args = Array.prototype.slice.call(arguments);
        if (args.length === 0 || args.includes('all')) {
            // Mark all known views dirty
            state.ui._allDirty = true;
            state.ui.dirtyViews.clear();
        } else {
            state.ui._allDirty = false;
            for (const v of args) {
                state.ui.dirtyViews.add(v);
            }
        }
    }

    function isDirty(view) {
        if (state.ui._allDirty) return true;
        return state.ui.dirtyViews.has(view);
    }

    function clearDirty() {
        state.ui._allDirty = false;
        state.ui.dirtyViews.clear();
    }

    function populateRegionSelect(selectEl, currentValue) {
        populateRegionSelectOptions(selectEl, currentValue);
    }

    const _loadedScripts = new Set();
    function _loadScript(src) {
        if (_loadedScripts.has(src)) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = src;
            s.onload = () => { _loadedScripts.add(src); resolve(); };
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    return {
        state,
        elements,
        cacheElements,
        REGIONS: APP_REGIONS,
        initTelegramTheme,
        toggleTheme,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        loadRecentSearches,
        saveRecentSearches,
        addRecentSearch,
        clearRecentSearches,
        measureRender,
        markDirty,
        isDirty,
        clearDirty,
        populateRegionSelect,
        _loadScript,
    };
}


;
/**
 * render_core.js — Infrastructure renders: toast, error bar, loading,
 * view tabs, panels, summary, helper.
 */

function createRenderCore(context) {
    const { state, elements } = context;

    /**
     * Escape HTML special characters to prevent XSS attacks.
     * Uses a singleton DOM element to avoid creating new elements on every call.
     */
    function escapeHtml(str) {
        if (str == null) return "";
        return String(str).replace(/[&<>"']/g, (char) => {
            const map = {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#39;",
            };
            return map[char] || char;
        });
    }

    /**
     * Validate URL is safe for href/src attributes.
     * Blocks javascript:, data:, and other dangerous schemes.
     */
    function safeUrl(url) {
        if (!url || typeof url !== "string") return "";
        const trimmed = url.trim().toLowerCase();
        if (trimmed.startsWith("https://") || trimmed.startsWith("http://") || trimmed.startsWith("/")) {
            return url.trim();
        }
        return "";
    }

    // Image-proxy router. We had a proxy that transcoded Kufar's
    // JPEG to WebP/AVIF, but on first paint the user has to wait
    // for the proxy to fetch + Pillow-encode every thumbnail —
    // that's worse latency than just letting the browser pull the
    // original JPEG and rely on its native cache. The original is
    // ~25-40 % bigger but parses immediately on Telegram WebView.
    //
    // ``options.useProxy = true`` opts back into transcoding for
    // surfaces that legitimately benefit (the listing-detail
    // gallery, where we serve big photos and the bandwidth
    // savings outweigh the encode time). Default is pass-through.
    const _KUFAR_GALLERY_PREFIX = "https://rms.kufar.by/v1/gallery/";
    function optimizedImage(url, options) {
        if (!url || typeof url !== "string") return "";
        const opts = options || {};
        if (!opts.useProxy) {
            // Pass-through — direct rms.kufar.by URL. Browser cache
            // (and the SW image-asset cache on the same path) does
            // the heavy lifting on repeat hits.
            return url;
        }
        if (!url.startsWith(_KUFAR_GALLERY_PREFIX)) return url;
        const path = url.slice(_KUFAR_GALLERY_PREFIX.length);
        if (!path || path.includes("..") || path.includes("?")) {
            return url;
        }
        const width = Number.isFinite(opts.width) ? Math.round(opts.width) : null;
        const params = [];
        if (width && width > 0) params.push(`w=${width}`);
        const query = params.length ? `?${params.join("&")}` : "";
        return `/api/v1/img/${path}${query}`;
    }

    function safeRender(name, fn) {
        try {
            return fn();
        } catch (err) {
            console.error(`[render] ${name} failed:`, err);
            return null;
        }
    }

    /* ===== Toast ===== */

    // Cap on simultaneously-visible toasts. Anything past this count
    // dismisses the oldest one so the container can never blanket the
    // bottom of the screen during an error storm.
    const MAX_VISIBLE_TOASTS = 3;

    function showToast(message, type = "info", duration = 3000) {
        if (!elements.toastContainer) return null;

        const messageStr = String(message ?? "")
            .replace(/^[\s✓✔✅☑✕✖❌×↩←→★⭐❤🔥⚠\uFE0F]+/u, "")
            .trim();
        const label = {
            success: "Готово",
            error: "Ошибка",
            info: "Статус",
        }[type] || "Статус";

        // Deduplication: if the same (message, type) is already visible
        // and not already in the exit animation, just reset its timer
        // and bump a small "×N" counter on it instead of stacking a
        // duplicate. Cuts the noise when an action retries quickly.
        const existing = Array.from(
            elements.toastContainer.querySelectorAll(`.toast.toast-${type}`)
        ).find((node) => {
            if (node.classList.contains("toast-exit")) return false;
            const msgEl = node.querySelector(".toast-message");
            return msgEl && msgEl.textContent === messageStr;
        });
        if (existing) {
            const previousCount = Number(existing.dataset.toastCount || 1);
            const nextCount = previousCount + 1;
            existing.dataset.toastCount = String(nextCount);
            let badge = existing.querySelector(".toast-count");
            if (!badge) {
                badge = domEl("span", {
                    className: "toast-count",
                    attrs: { "aria-hidden": "true" },
                });
                existing.insertBefore(
                    badge,
                    existing.querySelector(".toast-close"),
                );
            }
            badge.textContent = `×${nextCount}`;
            // Reset the auto-dismiss timer so the latest occurrence
            // gets its full duration on screen.
            const prevTimer = Number(existing.dataset.dismissTimer || 0);
            if (prevTimer) clearTimeout(prevTimer);
            const nextTimer = setTimeout(() => dismissToast(existing), duration);
            existing.dataset.dismissTimer = String(nextTimer);
            return existing;
        }

        const toast = domEl(
            "div",
            {
                className: `toast toast-${type} entering`,
                attrs: { role: "status", "aria-live": "polite" },
            },
            domEl("span", {
                className: `toast-dot ${type}`,
                attrs: { "aria-hidden": "true" },
            }),
            domEl(
                "span",
                { className: "toast-body" },
                domEl("span", { className: "toast-label", text: label }),
                domEl("span", { className: "toast-message", text: messageStr }),
            ),
            domEl("button", {
                className: "toast-close",
                type: "button",
                text: "×",
                attrs: { "aria-label": "Закрыть уведомление" },
            }),
        );

        elements.toastContainer.appendChild(toast);

        // Cap on stacked toasts: if we just exceeded the limit, gently
        // dismiss the oldest one. We pick the first non-exiting node so
        // a toast already in its exit animation isn't fast-tracked
        // through twice.
        const visible = Array.from(
            elements.toastContainer.querySelectorAll(".toast:not(.toast-exit)")
        );
        if (visible.length > MAX_VISIBLE_TOASTS) {
            for (const node of visible) {
                if (node !== toast) {
                    dismissToast(node);
                    break;
                }
            }
        }

        // Remove entering class after animation completes
        const prefersReducedMotion = _prefersReducedMotion();
        const animationDuration = prefersReducedMotion ? 10 : 120;
        setTimeout(() => {
            toast.classList.remove("entering");
        }, animationDuration);

        const dismissTimer = setTimeout(() => dismissToast(toast), duration);
        toast.dataset.dismissTimer = String(dismissTimer);

        // FE-M11: pause the auto-dismiss timer while the user is
        // hovering the toast or has keyboard focus inside it. The
        // remaining time after a pause/resume cycle is what was left
        // when the pause started — so a user who hovers a 3 s toast
        // 1 s in and lets go after another 5 s still gets 2 s to
        // read the message before it slides out. ``pointerenter`` /
        // ``pointerleave`` fire on the same element regardless of
        // mouse vs touch (touch hovers don't fire on iOS Safari
        // mid-tap, but we restart on ``focusout`` from the close
        // button anyway). Mouse-only listeners are intentional;
        // touch users dismiss with the × button or wait it out.
        let _remainingMs = duration;
        let _pauseStart = 0;
        function _pauseDismiss() {
            const timerId = Number(toast.dataset.dismissTimer || 0);
            if (!timerId) return;
            clearTimeout(timerId);
            toast.dataset.dismissTimer = "0";
            _pauseStart = Date.now();
        }
        function _resumeDismiss() {
            if (!_pauseStart) return;
            _remainingMs = Math.max(400, _remainingMs - (Date.now() - _pauseStart));
            _pauseStart = 0;
            const next = setTimeout(() => dismissToast(toast), _remainingMs);
            toast.dataset.dismissTimer = String(next);
        }
        toast.addEventListener("pointerenter", _pauseDismiss);
        toast.addEventListener("pointerleave", _resumeDismiss);
        toast.addEventListener("focusin", _pauseDismiss);
        toast.addEventListener("focusout", _resumeDismiss);

        const closeBtn = toast.querySelector(".toast-close");
        closeBtn.addEventListener("click", () => {
            const timerId = Number(toast.dataset.dismissTimer || 0);
            if (timerId) clearTimeout(timerId);
            dismissToast(toast);
        });

        const _haptic = window.Telegram?.WebApp;
        if (_haptic?.HapticFeedback && (!_haptic.version || parseFloat(_haptic.version) >= 6.1)) {
            if (type === "success") {
                _haptic.HapticFeedback.notificationOccurred("success");
            } else if (type === "error") {
                _haptic.HapticFeedback.notificationOccurred("error");
            }
        }

        return toast;
    }

    function dismissToast(toast) {
        if (!toast || !toast.parentNode) return;
        if (toast.classList.contains("toast-exit")) return;
        const timerId = Number(toast.dataset.dismissTimer || 0);
        if (timerId) clearTimeout(timerId);
        const prefersReducedMotion = _prefersReducedMotion();
        const exitDuration = prefersReducedMotion ? 10 : 120;
        toast.classList.add("toast-exit");
        setTimeout(() => {
            if (toast.parentNode) {
                toast.remove();
            }
        }, exitDuration);
    }

    /* ===== Rates ===== */
    // Removed — currency is always BYN

    /* ===== Error ===== */

    function renderError() {
        return safeRender('renderError', () => {
            const message = typeof state.ui.error === "string" ? state.ui.error.trim() : "";
            if (!message) {
                elements.errorBar.hidden = true;
                elements.errorBar.classList.remove("is-visible");
                elements.errorText.textContent = "";
                return;
            }
            elements.errorText.textContent = message;
            elements.errorBar.hidden = false;
            elements.errorBar.classList.add("is-visible");
        });
    }

    /* ===== Empty state ===== */

    // Tiny SVG icon set for the rich empty states. Picked stroke-only
    // shapes that follow the same Lucide-ish line-weight as the tab
    // icons so the language stays consistent across the app.
    const _EMPTY_STATE_ICONS = {
        watchlist:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
        leads:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-4"/><polyline points="9 11 12 8 15 11"/><line x1="12" y1="2" x2="12" y2="14"/></svg>',
        trackers:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
        events:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
        search:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
        deals:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
        listings:
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    };

    /**
     * Build a richer empty state — icon, title, hint, optional CTA —
     * for surfaces where a single dashed paragraph (.tracker-empty)
     * felt under-served. Returns a freshly-built DOM node ready to
     * appendChild into the section's container.
     *
     * @param {object} opts
     * @param {string} opts.icon         Key into _EMPTY_STATE_ICONS, or
     *                                   raw HTML to embed.
     * @param {string} opts.title        First line, bold.
     * @param {string} [opts.hint]       Second line, muted.
     * @param {string} [opts.actionLabel] CTA button label.
     * @param {Function} [opts.onAction] CTA click handler.
     */
    function buildEmptyState(opts) {
        const { icon, title, hint, actionLabel, onAction } = opts || {};
        const iconHtml = _EMPTY_STATE_ICONS[icon] || "";
        const wrap = domEl("div", {
            className: "empty-state",
            attrs: { role: "status" },
        });
        if (iconHtml) {
            const iconBox = document.createElement("div");
            iconBox.className = "empty-state-icon";
            const parsed = new DOMParser().parseFromString(iconHtml, "image/svg+xml");
            const svg = parsed.querySelector("svg");
            if (svg && !parsed.querySelector("parsererror")) {
                iconBox.appendChild(svg);
            }
            wrap.appendChild(iconBox);
        }
        if (title) {
            wrap.appendChild(
                domEl("p", { className: "empty-state-title", text: title }),
            );
        }
        if (hint) {
            wrap.appendChild(
                domEl("p", { className: "empty-state-hint", text: hint }),
            );
        }
        if (actionLabel && typeof onAction === "function") {
            const button = domEl("button", {
                className: "empty-state-action",
                type: "button",
                text: actionLabel,
            });
            button.addEventListener("click", onAction);
            wrap.appendChild(button);
        }
        return wrap;
    }

    /* ===== Loading ===== */

    /**
     * Build a skeleton card element for loading placeholder.
     */
    function buildSkeletonCard() {
        const card = document.createElement("div");
        card.className = "skeleton-card";
        card.setAttribute("aria-hidden", "true");
        card.appendChild(
            domFragment(
                domEl("div", { className: "skeleton-image" }),
                domEl(
                    "div",
                    { className: "skeleton-content" },
                    domEl("div", { className: "skeleton-text skeleton-title" }),
                    domEl("div", { className: "skeleton-text skeleton-subtitle" }),
                    domEl("div", { className: "skeleton-text skeleton-price" }),
                ),
            )
        );
        return card;
    }

    function renderLoading() {
        return safeRender('renderLoading', () => {
            elements.searchButton.disabled = state.ui.loading || !state.search.query.trim();
            elements.searchInput.disabled = state.ui.loading;
            if (state.ui.loading) {
                elements.searchButtonLabel.replaceChildren(domEl("span", { className: "spin" }));
                elements.listingsSection?.setAttribute('aria-busy', 'true');
                elements.statsSection?.setAttribute('aria-busy', 'true');

                // Show skeleton cards in listing containers during initial load
                if (!state.listings.items.length && elements.listingsList) {
                    domClear(elements.listingsList);
                    for (let i = 0; i < 3; i++) {
                        elements.listingsList.appendChild(buildSkeletonCard());
                    }
                    elements.listingsSection.hidden = false;
                }
            } else {
                elements.searchButtonLabel.textContent = "Найти";
                elements.listingsSection?.removeAttribute('aria-busy');
                elements.statsSection?.removeAttribute('aria-busy');
            }
        });
    }

    /* ===== Currency ===== */
    // Removed — currency is always BYN

    /* ===== Strict Search ===== */

    function renderStrictSearch() {
        return safeRender('renderStrictSearch', () => {
            if (elements.strictSearchToggle) {
                elements.strictSearchToggle.checked = state.search.strictSearch;
            }
        });
    }

    /* ===== View Tabs ===== */

    function renderViewTabs() {
        return safeRender('renderViewTabs', () => {
            for (const button of elements.viewTabs) {
                const isActive = button.dataset.view === state.ui.activeView;
                button.classList.toggle("active", isActive);
                button.setAttribute("aria-selected", String(isActive));
                button.tabIndex = isActive ? 0 : -1;
            }
        });
    }

    /* ===== Panels ===== */

    function renderPanels() {
        return safeRender('renderPanels', () => {
            for (const button of elements.panelToggles) {
                const panelName = button.dataset.panelToggle;
                const isOpen = Boolean(state.panels[panelName]);
                const body = elements.panelBodies[panelName];

                if (body) {
                    body.hidden = !isOpen;
                }

                button.textContent = isOpen ? "Свернуть" : "Показать";
                button.setAttribute("aria-expanded", String(isOpen));
                button.classList.toggle("is-open", isOpen);
            }
        });
    }

    function setPanelOpen(panelName, isOpen, skipLoad) {
        if (!(panelName in state.panels)) {
            return;
        }

        state.panels[panelName] = Boolean(isOpen);
        renderPanels();

        if (panelName === "distribution") {
            if (state.panels.distribution) {
                // renderChart is provided via cross-module hooks
                if (context._hooks?.renderChart) context._hooks.renderChart();
            } else {
                if (context._hooks?.destroyChart) context._hooks.destroyChart();
            }
        }

        if (panelName === "history") {
            if (state.panels.history) {
                if (context._hooks?.renderHistory) context._hooks.renderHistory();
            } else {
                if (context._hooks?.destroyHistoryChart) context._hooks.destroyHistoryChart();
            }
        }
    }

    /* ===== Summary ===== */

    function renderRefinementChips() {
        if (!elements.summaryRefinements || !elements.summaryRefinementsChips) return;
        const refinements = Array.isArray(state.misc.stats?.suggested_refinements)
            ? state.misc.stats.suggested_refinements
            : [];
        domClear(elements.summaryRefinementsChips);        if (!refinements.length) {
            elements.summaryRefinements.hidden = true;
            return;
        }
        for (const token of refinements) {
            if (typeof token !== "string" || !token.trim()) continue;
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "summary-refinement-chip";
            chip.dataset.refinement = token;
            chip.textContent = `+ ${token}`;
            chip.title = `Добавить «${token}» к запросу`;
            elements.summaryRefinementsChips.appendChild(chip);
        }
        elements.summaryRefinements.hidden = false;
    }

    function renderSummary() {
        return safeRender('renderSummary', () => {
            if (!state.misc.stats || !state.search.query) {
                elements.summaryStrip.hidden = true;
                elements.summaryQuery.textContent = "—";
                elements.summarySignal.textContent = "—";
                elements.summaryMedian.textContent = "—";
                elements.summaryRange.textContent = "—";
                elements.summaryFair.textContent = "—";
                if (elements.summaryRefinements) {
                    elements.summaryRefinements.hidden = true;
                    domClear(elements.summaryRefinementsChips);                }
                return;
            }
            renderRefinementChips();

            const totalResults = Number(state.misc.stats.total_results || 0);
            const analyzedCount = Number(state.misc.stats.analyzed_count || state.misc.stats.count || 0);
            const marketMedian = Number(state.misc.stats.median || 0);
            const marketMean = Number(state.misc.stats.mean || 0);
            const marketMin = Number(state.misc.stats.min || 0);
            const marketMax = Number(state.misc.stats.max || 0);
            const fairFrom = state.misc.stats.fair_price_from != null ? Number(state.misc.stats.fair_price_from) : null;
            const fairTo = state.misc.stats.fair_price_to != null ? Number(state.misc.stats.fair_price_to) : null;
            const spreadRatio = marketMedian > 0 ? (marketMax - marketMin) / marketMedian : 0;
            const meanDeltaRatio = marketMedian > 0 ? Math.abs(marketMean - marketMedian) / marketMedian : 0;
            let signal = "Рынок читается ровно, медиана подходит как главный ориентир.";
            if (analyzedCount < 5) {
                signal = "Выборка маленькая, смотрите объявления и сравнивайте вручную.";
            } else if (spreadRatio > 0.8 || meanDeltaRatio > 0.12) {
                signal = "Рынок неоднородный: сначала смотрите медиану, затем историю и сегменты.";
            } else if (totalResults > analyzedCount * 1.6) {
                signal = "Часть рынка без цены, ориентируйтесь на медиану и полный список объявлений.";
            }

            elements.summaryQuery.textContent = state.search.query;
            elements.summarySignal.textContent = signal;
            function shortPrice(v) {
                if (v == null || v === 0) return "—";
                const n = Number(v);
                if (Number.isNaN(n)) return "—";
                if (n >= 1000) {
                    const k = n / 1000;
                    return `${k % 1 === 0 ? k : k.toFixed(1)}к`;
                }
                return `${Math.round(n)} р.`;
            }

            elements.summaryMedian.textContent = shortPrice(state.misc.stats.median);
            elements.summaryRange.textContent = marketMin > 0 && marketMax > 0
                ? `${shortPrice(marketMin)} — ${shortPrice(marketMax)}`
                : "—";
            elements.summaryFair.textContent = fairFrom != null && fairTo != null
                ? `${shortPrice(fairFrom)} — ${shortPrice(fairTo)}`
                : "—";
            elements.summaryStrip.hidden = false;
        });
    }

    /* ===== Helper ===== */

    function renderHelper() {
        return safeRender('renderHelper', () => {
            const shouldShow =
                !state.ui.loading &&
                !state.ui.error &&
                !state.misc.stats &&
                state.ui.activeView !== "tracking" &&
                state.ui.activeView !== "monitoring" &&
                state.ui.activeView !== "deals";
            elements.helperPanel.hidden = !shouldShow;
        });
    }

    /* ===== Views ===== */

    function renderViews() {
        return safeRender('renderViews', () => {
            for (const [name, panel] of Object.entries(elements.views)) {
                if (!panel) continue;
                panel.hidden = state.ui.activeView !== name;
            }
        });
    }

    return {
        escapeHtml,
        safeUrl,
        optimizedImage,
        showToast,
        dismissToast,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
        buildEmptyState,
    };
}


;
function createRenderCardBuilders(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        hasTelegramInitData,
        safeUrl: safeUrl,
        optimizedImage,
        escapeHtml,
    } = context;

    /**
     * Build an <img> for a card thumbnail with native lazy-load and
     * display-aware proxy sizing.
     *
     * The proxy width is set to roughly 3× the rendered display width
     * so retina screens still get a sharp image without forcing the
     * server to transcode huge originals. Explicit width/height
     * attributes give the browser a layout box BEFORE the bytes
     * arrive — that's what makes ``loading="lazy"`` actually defer
     * off-screen fetches instead of fetching everything eagerly.
     */
    function buildMediaNode(
        imageClass,
        placeholderClass,
        placeholderText,
        url,
        altText,
        options,
    ) {
        const validated = safeUrl(url);
        if (validated) {
            const opts = options || {};
            const displayPx = Number(opts.displayPx) > 0 ? Number(opts.displayPx) : 80;
            const proxyWidth = Math.min(640, Math.max(120, displayPx * 3));
            const src = typeof optimizedImage === "function"
                ? optimizedImage(validated, { width: proxyWidth })
                : validated;
            const attrs = {
                src,
                alt: altText || "Товар без названия",
                loading: "lazy",
                decoding: "async",
                width: String(displayPx),
                height: String(displayPx),
            };
            // Modern browsers honour ``fetchpriority="low"`` on
            // off-screen lazy images so the network queue doesn't
            // starve the on-screen ones.
            if (opts.fetchPriority) {
                attrs.fetchpriority = opts.fetchPriority;
            }
            const img = domEl("img", { className: imageClass, attrs });
            // FE-M10: fall back to a placeholder when the proxied
            // Kufar thumbnail 404s or the network is misbehaving.
            // Without this the card renders a broken-image icon
            // (Kufar occasionally garbage-collects URLs while a
            // listing is still indexed). The replacement is a
            // <div class={placeholderClass}> matching the layout
            // box reserved by the <img>'s width/height attrs, so
            // the swap is invisible from a layout perspective.
            // ``once: true`` so a flaky network can't trigger an
            // infinite loop of swap → re-fetch → error.
            img.addEventListener("error", () => {
                const fallback = domEl("div", {
                    className: placeholderClass,
                    text: placeholderText,
                });
                if (img.parentNode) {
                    img.parentNode.replaceChild(fallback, img);
                }
            }, { once: true });
            return img;
        }
        return domEl("div", { className: placeholderClass, text: placeholderText });
    }

    function buildListingNode(item, verdictClassName) {
        const listing = domEl("article", {
            className: "listing",
            attrs: {
                "aria-label": item.subject || item.title || "Объявление",
                tabindex: "0",
                role: "button",
            },
        });
        const resolveVerdictClassName =
            typeof verdictClassName === "function" ? verdictClassName : function () { return "neutral"; };

        const badges = [];
        if (item.list_time) {
            const hours = (Date.now() - new Date(item.list_time).getTime()) / 3600000;
            if (hours <= 3) {
                badges.push(domEl("span", { className: "listing-badge fresh-hot", text: "Новое" }));
            } else if (hours <= 24) {
                badges.push(domEl("span", { className: "listing-badge fresh-warm", text: "Сегодня" }));
            }
        }
        if (item.deal_verdict) {
            badges.push(domEl("span", {
                className: `listing-badge verdict-${resolveVerdictClassName(item.deal_verdict)}`,
                text: item.deal_verdict,
            }));
        }

        let delta = item.price_vs_median;
        if (delta == null && item.price != null && state.misc.stats?.median && Number(state.misc.stats.median) > 0) {
            delta = Math.round(((Number(item.price) - Number(state.misc.stats.median)) / Number(state.misc.stats.median)) * 100 * 100) / 100;
        }
        if (delta != null) {
            const absDelta = Math.abs(delta);
            if (absDelta < 0.5) {
                badges.push(domEl("span", {
                    className: "listing-badge delta-approx",
                    text: "≈",
                    attrs: { title: item.price_reference_label
                        ? `По рынку (${item.price_reference_label})`
                        : "По рынку" },
                }));
            } else {
                const tooltipPrefix = delta > 0 ? "Выше" : "Ниже";
                const refLabel = item.price_reference_scope === "category"
                    ? (item.price_reference_label || "категории")
                    : "среднего по запросу";
                badges.push(domEl("span", {
                    className: `listing-badge ${deltaClass(delta)}`.trim(),
                    text: formatDelta(delta),
                    attrs: { title: `${tooltipPrefix} ${refLabel}` },
                }));
            }
        }

        const tags = domEl("div", { className: "listing-tags" });
        if (item.condition) tags.appendChild(domEl("span", { className: "tag", text: formatCondition(item.condition) }));
        if (item.seller_type) tags.appendChild(domEl("span", { className: "tag", text: formatSeller(item.seller_type) }));

        listing.appendChild(
            domFragment(
                domEl(
                    "div",
                    { className: "listing-top" },
                    buildMediaNode(
                        "listing-thumb",
                        "listing-thumb placeholder",
                        "Нет фото",
                        item.thumbnail,
                        item.title || item.subject || "",
                        { displayPx: 76 },
                    ),
                    domEl(
                        "div",
                        { className: "listing-body" },
                        domEl("span", { className: "listing-name", text: item.title }),
                        tags,
                        domEl("span", { className: "listing-price mono", text: formatPrice(item.price, item.price_type) }),
                    ),
                ),
                badges.length ? domEl("div", { className: "listing-badges" }, badges) : null,
                domEl(
                    "div",
                    { className: "listing-actions" },
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "lead" }, text: "В покупки", attrs: { "aria-label": `Добавить «${item.title || "товар"}» в покупки` } }),
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "watch" }, text: "В избранное", attrs: { "aria-label": `Добавить «${item.title || "товар"}» в избранное` } }),
                    domEl("a", {
                        className: "listing-btn listing-btn--kufar",
                        text: "Kufar",
                        attrs: { href: safeUrl(item.link), target: "_blank", rel: "noreferrer noopener" },
                    }),
                ),
            )
        );

        listing._item = item;

        if (typeof attachLongPress === "function") {
            attachLongPress(listing, () => [
                {
                    label: "В покупки",
                    tone: "accent",
                    icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-4"/><polyline points="9 11 12 8 15 11"/><line x1="12" y1="2" x2="12" y2="14"/></svg>',
                    onSelect: () => actions.addLeadFromListing(item),
                },
                {
                    label: "В избранное",
                    icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
                    onSelect: () => actions.addWatchlistFromListing(item),
                },
                {
                    label: "Открыть на Kufar",
                    icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
                    onSelect: () => {
                        const link = safeUrl(item.link);
                        if (!link) return;
                        if (window.Telegram?.WebApp?.openLink) {
                            window.Telegram.WebApp.openLink(link);
                        } else {
                            window.open(link, "_blank", "noopener,noreferrer");
                        }
                    },
                },
            ]);
        }
        return listing;
    }

    /* ─────────────────────────────────────────────────────────────────
     * Unified item card — watchlist (`mode='watching'`) and lead
     * (`mode='lead'`) share the same lead_items row after the
     * 20260427_0001 merge, so they share a single builder. The two
     * surfaces diverge in three places only:
     *   1. price source (lead.price_byn vs item.current_price_byn)
     *   2. middle "fields" section (buy/sold inputs vs note input)
     *   3. action row (confirm/cancel/close-deal vs detail/promote/delete)
     * Everything else — outer wrapper, media + title, missing banner,
     * profit/potential block, Kufar link — is shared via small helpers.
     * ─────────────────────────────────────────────────────────────────
     */

    /** Hash a numeric or null value into rounded int or null. */
    function _roundOrNull(value) {
        const n = value != null ? Number(value) : null;
        return n && n > 0 ? Math.round(n) : null;
    }

    /**
     * Human-readable status for a lead, used as the aria-label suffix
     * on the outer <article>. Keeps the four values the backend emits
     * in lead_items.status in sync with what screen readers announce.
     */
    function _leadStatusLabel(status) {
        switch (status) {
            case "new": return "ожидает подтверждения";
            case "confirmed": return "подтверждено";
            case "sold": return "продано";
            case "cancelled": return "отменено";
            default: return status || "без статуса";
        }
    }

    /** Build a shared "missing" banner with a mode-appropriate message. */
    function _buildMissingBanner(mode) {
        const text =
            mode === "watching"
                ? "Объявление снято с продажи. Будет удалено автоматически через несколько дней."
                : "Объявление снято с продажи";
        return domEl("div", { className: "watchlist-missing-banner", text });
    }

    /** Compute and render the price-delta pill ("📉 -120 BYN (-8%)"). */
    function _buildPriceDeltaNode(item) {
        if (item.price_delta_byn == null || Math.abs(item.price_delta_byn) <= 0.5) {
            return null;
        }
        const deltaNum = Math.round(item.price_delta_byn);
        const className = deltaNum < 0 ? "down" : deltaNum > 0 ? "up" : "neutral";
        const sign = deltaNum > 0 ? "+" : "";
        const arrow = deltaNum < 0 ? "📉" : deltaNum > 0 ? "📈" : "≈";
        const percent =
            item.price_delta_percent != null ? ` (${sign}${item.price_delta_percent}%)` : "";
        return domEl("span", {
            className: `watchlist-price-delta ${className}`,
            text: `${arrow} ${sign}${deltaNum} BYN${percent}`,
        });
    }

    /** Build an inline SVG sparkline from a watchlist item's
     *  price_history. Returns null when the series is too short to be
     *  meaningful (≤1 data point). The line is coloured by the
     *  net direction of the trend so a green line = price went down
     *  (good for a buyer) and a red line = price went up.
     *
     *  Pure-SVG, no dependency on Chart.js — keeps the watchlist
     *  render path off the lazy chart library entirely. */
    function _buildPriceSparkline(item) {
        const history = Array.isArray(item.price_history) ? item.price_history : [];
        if (history.length < 2) return null;

        const prices = history
            .map((p) => Number(p.price_byn))
            .filter((n) => Number.isFinite(n) && n > 0);
        if (prices.length < 2) return null;

        const min = Math.min(...prices);
        const max = Math.max(...prices);
        const span = Math.max(1, max - min);
        const width = 88;
        const height = 28;
        const padX = 1;
        const padY = 2;

        const points = prices.map((p, i) => {
            const x = padX + (i / (prices.length - 1)) * (width - padX * 2);
            // Invert Y so higher prices sit higher in the SVG (origin
            // top-left, but visually we want UP = more expensive).
            const y = padY + (1 - (p - min) / span) * (height - padY * 2);
            return `${x.toFixed(2)},${y.toFixed(2)}`;
        });

        // Direction: net change from first to last. Buyer-friendly:
        // down = green ("price dropped, deal warming up"), up = red.
        const first = prices[0];
        const last = prices[prices.length - 1];
        const direction =
            last < first - 0.5 ? "down" : last > first + 0.5 ? "up" : "flat";

        // Build a polyline + area-fill underneath.
        const linePath = `M ${points.join(" L ")}`;
        const areaPath =
            `M ${padX},${height - padY} ` +
            `L ${points.join(" L ")} ` +
            `L ${width - padX},${height - padY} Z`;

        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", `wl-sparkline wl-sparkline--${direction}`);
        svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
        svg.setAttribute("width", String(width));
        svg.setAttribute("height", String(height));
        svg.setAttribute("aria-hidden", "true");
        svg.setAttribute("role", "presentation");

        const area = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "path",
        );
        area.setAttribute("class", "wl-sparkline-area");
        area.setAttribute("d", areaPath);
        svg.appendChild(area);

        const line = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "path",
        );
        line.setAttribute("class", "wl-sparkline-line");
        line.setAttribute("d", linePath);
        line.setAttribute("fill", "none");
        svg.appendChild(line);

        // Last-point dot for emphasis ("here's where you are now").
        const lastPoint = points[points.length - 1].split(",");
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("class", "wl-sparkline-dot");
        dot.setAttribute("cx", lastPoint[0]);
        dot.setAttribute("cy", lastPoint[1]);
        dot.setAttribute("r", "2");
        svg.appendChild(dot);

        const wrap = domEl("div", {
            className: "wl-sparkline-wrap",
            attrs: {
                title:
                    direction === "down"
                        ? `Цена снижается (${prices.length} точек)`
                        : direction === "up"
                          ? `Цена растёт (${prices.length} точек)`
                          : `Цена стабильна (${prices.length} точек)`,
            },
        });
        wrap.appendChild(svg);
        return wrap;
    }

    /** Watchlist potential profit (median - current) — null in lead mode. */
    function _buildPotentialProfitNode(item) {
        const current = item.current_price_byn || item.initial_price_byn;
        if (!item.market_median_byn || !current || Number(item.market_median_byn) <= 0) {
            return null;
        }
        const profitByn = Number(item.market_median_byn) - Number(current);
        const profitPercent = current > 0 ? Math.round((profitByn / Number(current)) * 100) : 0;
        const className = profitByn >= 0 ? "profit-positive" : "profit-negative";
        const sign = profitByn >= 0 ? "+" : "";
        return domEl("span", {
            className: `watchlist-profit ${className}`,
            text: `Потенциал: ${sign}${Math.round(profitByn)} BYN (${sign}${profitPercent}%)`,
        });
    }

    /** Lead profit/potential ("Результат" if sold, "Потенциал" if active). */
    function _buildLeadProfitNode(lead) {
        const buy = _roundOrNull(lead.buy_price_byn);
        const sold = _roundOrNull(lead.sold_price_byn);
        if (!buy || !sold) return null;
        const profitRaw = sold - buy;
        const percent = buy > 0 ? ((profitRaw / buy) * 100).toFixed(0) : "0";
        const sign = profitRaw >= 0 ? "+" : "";
        const cls = profitRaw >= 0 ? "profit-positive" : "profit-negative";
        const isSold = lead.status === "sold";
        const showPotential = !isSold && (lead.status === "new" || lead.status === "bought");
        if (!isSold && !showPotential) return null;
        const label = isSold ? "Результат" : "Потенциал";
        return domEl("div", {
            className: `lead-financial-item ${cls}`,
            text: `${label}: ${sign}${Math.round(profitRaw)} BYN (${sign}${percent}%)`,
        });
    }

    /** Market badge ("Падение цены", "Пропало (3д)") — watchlist only. */
    function _buildMarketBadge(item, marketLabel) {
        if (!item.market_status || !["price_drop", "missing"].includes(item.market_status)) {
            return null;
        }
        let missingAgeText = "";
        if (item.market_status === "missing" && item.missing_since_at) {
            const missingDate = new Date(item.missing_since_at);
            const diffMs = Date.now() - missingDate.getTime();
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
            if (diffDays >= 1) missingAgeText = ` (${diffDays}д)`;
        }
        return domEl("span", {
            className: `market-badge ${item.market_status}`,
            text: `${marketLabel(item.market_status)}${missingAgeText}`,
        });
    }

    /** Lead-specific buy/sold price input fields. */
    function _buildLeadFields(lead, { signal } = {}) {
        const priceByn = _roundOrNull(lead.price_byn);
        const buyInput = domEl("input", {
            type: "text",
            attrs: { min: "0", placeholder: "цена покупки" },
            dataset: { role: "buy-price" },
        });
        buyInput.value = lead.buy_price_byn ?? "";
        buyInput.addEventListener("input", (e) => {
            const next = e.target.value.replace(/[^\d]/g, "");
            if (next !== e.target.value) e.target.value = next;
        }, { signal });

        const soldInput = domEl("input", {
            type: "text",
            attrs: { min: "0", placeholder: "цена продажи" },
            dataset: { role: "sold-price" },
        });
        soldInput.value = lead.sold_price_byn ?? "";
        soldInput.addEventListener("input", (e) => {
            const next = e.target.value.replace(/[^\d]/g, "");
            if (next !== e.target.value) e.target.value = next;
        }, { signal });

        const fillBuy = domEl("button", {
            className: "lead-field-chip",
            type: "button",
            dataset: { role: "fill-buy-price" },
            text: priceByn ? `${priceByn}` : "Договорная",
            attrs: !priceByn ? { disabled: true } : {},
        });
        fillBuy.addEventListener("click", () => {
            if (priceByn) {
                buyInput.value = String(priceByn);
                buyInput.focus();
            }
        }, { signal });

        return domEl(
            "div",
            { className: "lead-card-fields" },
            domEl(
                "label",
                { className: "lead-field" },
                domEl("span", { className: "lead-field-label", text: "Купил за" }),
                domEl(
                    "div",
                    { className: "lead-field-wrap" },
                    buyInput,
                    fillBuy,
                    domEl("span", { className: "unit", text: "BYN" }),
                ),
            ),
            domEl(
                "label",
                { className: "lead-field" },
                domEl("span", { className: "lead-field-label", text: "Продал за" }),
                domEl(
                    "div",
                    { className: "lead-field-wrap" },
                    soldInput,
                    domEl("span", { className: "unit", text: "BYN" }),
                ),
            ),
        );
    }

    /** Watchlist-specific note input. */
    function _buildWatchlistFields(item, { signal } = {}) {
        const notesInput = domEl("input", {
            type: "text",
            attrs: { placeholder: "заметка к лоту…" },
            dataset: { role: "notes" },
        });
        notesInput.value = item.notes || "";
        notesInput.addEventListener("change", () => {
            if (context._hooks?.showToast) context._hooks.showToast("Заметка сохранена");
            void actions.updateWatchlistMeta(item.id, {
                notes: notesInput.value.trim() || null,
            });
        }, { signal });
        return domEl(
            "div",
            { className: "watchlist-card-fields" },
            domEl(
                "label",
                { className: "wl-field wl-field-wide" },
                domEl("span", { className: "wl-field-label", text: "Заметка" }),
                domEl("div", { className: "wl-field-input-wrap" }, notesInput),
            ),
        );
    }

    /** Lead action row — depends on `status`. */
    function _buildLeadActions(lead) {
        const isSold = lead.status === "sold";
        const leadTitle = lead.title || "лот";
        return domEl(
            "div",
            { className: "lead-card-actions" },
            !isSold
                ? domEl(
                    "div",
                    { className: "lead-btn-row" },
                    domEl("a", {
                        className: "lead-btn lead-btn--kufar",
                        text: "Kufar ↗",
                        attrs: {
                            href: safeUrl(lead.link),
                            target: "_blank",
                            rel: "noreferrer noopener",
                            "aria-label": `Открыть «${leadTitle}» на Kufar`,
                        },
                    }),
                )
                : null,
            !isSold
                ? domEl(
                    "div",
                    { className: "lead-btn-row" },
                    domEl("button", {
                        className: "lead-btn lead-btn--confirm",
                        type: "button",
                        dataset: { role: "confirm" },
                        text: "✓",
                        attrs: { "aria-label": `Подтвердить «${leadTitle}»` },
                    }),
                    domEl("button", {
                        className: "lead-btn lead-btn--delete",
                        type: "button",
                        dataset: { role: "cancel" },
                        text: "✕",
                        attrs: { "aria-label": `Удалить «${leadTitle}»` },
                    }),
                )
                : null,
            isSold
                ? domEl(
                    "div",
                    { className: "lead-btn-row" },
                    domEl("button", {
                        className: "lead-btn lead-btn--success",
                        type: "button",
                        dataset: { role: "close-deal" },
                        text: "✓ Готово",
                        attrs: { "aria-label": `Завершить сделку «${leadTitle}»` },
                    }),
                    domEl("button", {
                        className: "lead-btn lead-btn--revert",
                        type: "button",
                        dataset: { role: "revert" },
                        text: "↩ Назад",
                        attrs: { "aria-label": `Вернуть «${leadTitle}» в работу` },
                    }),
                )
                : null,
        );
    }

    /** Watchlist action row — Kufar link is hidden when the listing is missing. */
    function _buildWatchlistActions(item, isMissing) {
        const itemTitle = item.title || "товар";
        return domEl(
            "div",
            { className: "watchlist-card-actions" },
            domEl("button", {
                className: "wl-btn wl-btn--detail",
                type: "button",
                dataset: { role: "detail" },
                text: "Подробнее",
                attrs: { "aria-label": `Подробнее о «${itemTitle}»` },
            }),
            !isMissing
                ? domEl("button", {
                    className: "wl-btn wl-btn--accent",
                    type: "button",
                    dataset: { role: "lead" },
                    text: "В покупки",
                    attrs: { "aria-label": `Добавить «${itemTitle}» в покупки` },
                })
                : null,
            !isMissing
                ? domEl("a", {
                    className: "wl-btn",
                    text: "Kufar ↗",
                    attrs: {
                        href: safeUrl(item.link),
                        target: "_blank",
                        rel: "noreferrer noopener",
                        "aria-label": `Открыть «${itemTitle}» на Kufar`,
                    },
                })
                : null,
            domEl("button", {
                className: "wl-btn wl-btn--danger",
                type: "button",
                dataset: { role: "delete" },
                text: "Удалить",
                attrs: { "aria-label": `Удалить «${itemTitle}» из избранного` },
            }),
        );
    }

    /** Wire DOM-event handlers based on the card's mode. */
    function _wireCardHandlers(card, item, mode, signal) {
        if (mode === "lead") {
            card.querySelector('[data-role="confirm"]')?.addEventListener("click", () => {
                void actions.confirmLead(item, card);
            }, { signal });
            card.querySelector('[data-role="cancel"]')?.addEventListener("click", () => {
                void actions.cancelLead(item.id);
            }, { signal });
            card.querySelector('[data-role="close-deal"]')?.addEventListener("click", () => {
                void actions.closeDeal(item.id);
            }, { signal });
            card.querySelector('[data-role="revert"]')?.addEventListener("click", () => {
                void actions.revertLeadStage(item.id, item.status);
            }, { signal });
        } else if (mode === "watching") {
            card.querySelector('[data-role="detail"]')?.addEventListener("click", () => {
                void actions.openWatchlistDetail(item);
            }, { signal });
            card.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
                void actions.promoteWatchlistToLead(item);
            }, { signal });
            card.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteWatchlistItem(item.id);
            }, { signal });
        }
    }

    /** Attach swipe-to-reveal for watchlist cards. */
    function _attachSwipeReveal(card, item, mode) {
        if (mode !== "watching") return;

        const bg = domEl("div", { className: "swipe-bg" });
        const deleteBtn = domEl("button", {
            className: "swipe-btn swipe-btn--delete",
            type: "button",
            text: "Удалить",
        });
        const promoteBtn = domEl("button", {
            className: "swipe-btn swipe-btn--promote",
            type: "button",
            text: "В покупки",
        });

        deleteBtn.addEventListener("click", () => actions.deleteWatchlistItem(item.id));
        promoteBtn.addEventListener("click", () => actions.promoteWatchlistToLead(item));

        bg.appendChild(promoteBtn);
        bg.appendChild(deleteBtn);
        card.insertBefore(bg, card.firstChild);

        let startX = 0, currentX = 0, isDragging = false;
        const threshold = 80;
        const slop = 10;

        card.addEventListener("touchstart", (e) => {
            startX = e.touches[0].clientX;
            currentX = startX;
            isDragging = true;
        }, { passive: true });

        card.addEventListener("touchmove", (e) => {
            if (!isDragging) return;
            currentX = e.touches[0].clientX;
            const diff = currentX - startX;
            if (Math.abs(diff) > slop) {
                // Horizontal movement past slop cancels any pending long-press
                card._swipeMoved = true;
                card.style.transform = `translateX(${Math.max(-threshold, Math.min(threshold, diff))}px)`;
            }
        }, { passive: true });

        card.addEventListener("touchend", () => {
            isDragging = false;
            const diff = currentX - startX;
            if (diff < -threshold / 2) {
                card.style.transform = `translateX(-${threshold}px)`;
            } else if (diff > threshold / 2) {
                card.style.transform = `translateX(${threshold}px)`;
            } else {
                card.style.transform = "";
            }
            card._swipeMoved = false;
        });

        card.addEventListener("touchcancel", () => {
            isDragging = false;
            card.style.transform = "";
            card._swipeMoved = false;
        });
    }

    /**
     * Single source of truth for both "Покупки" (lead) and "Избранное"
     * (watching) cards. Pass `mode='lead'` or `mode='watching'`.
     *
     * @param {object} item LeadItem-shaped row from /api/v1/{leads,watchlist}.
     * @param {object} options { mode, marketLabel }
     */
    function buildItemCard(item, options = {}) {
        const { mode = "lead", marketLabel, signal } = options;
        const isWatching = mode === "watching";
        const isLead = mode === "lead";
        const isMissing = item.market_status === "missing";

        const outerClass = isLead
            ? `lead-card status-${item.status}`
            : "watchlist-card";
        // FE-H5/UX-H2: ARIA roles on the card itself.
        //   * watching card: role="button" — Enter/Space opens the
        //     detail sheet (primary action, handled below).
        //   * lead card: no role=button because there are multiple
        //     equivalent actions (confirm/cancel/close-deal/…) and no
        //     single "primary" one. We still keep tabindex=0 so the
        //     card is reachable for screen readers as a landmark that
        //     contains the inline price inputs and action buttons.
        //     aria-label summarises the lot and status for assistive
        //     tech that lands on the article.
        const card = domEl("article", {
            className: outerClass,
            attrs: isLead
                ? {
                    "data-lead-id": item.id,
                    tabindex: "0",
                    "aria-label": `${item.title || "лот"} — ${_leadStatusLabel(item.status)}`,
                }
                : { "data-watchlist-id": item.id, tabindex: "0", role: "button" },
        });
        card.style.overflow = "hidden";

        // Price for the header. Lead reads price_byn directly; watchlist
        // prefers the live current_price_byn, falling back to the price
        // captured at watchlist-add time.
        const priceSource = isLead
            ? item.price_byn
            : item.current_price_byn || item.initial_price_byn;
        const priceDisplay = _roundOrNull(priceSource);

        const titleNode = isLead
            ? domEl(
                "div",
                { className: "lead-card-title-row" },
                domEl("strong", { className: "lead-card-title", text: item.title }),
            )
            : domEl("strong", { className: "watchlist-card-title", text: item.title });

        const priceRow = isLead
            ? domEl("span", {
                className: "lead-card-price mono",
                text: priceDisplay ? `${priceDisplay} BYN` : "без цены",
            })
            : domEl(
                "div",
                { className: "watchlist-card-price-row" },
                domEl("span", {
                    className: "watchlist-card-price mono",
                    text: priceDisplay ? `${priceDisplay} BYN` : "Договорная",
                }),
                _buildPriceDeltaNode(item),
                // 30-day price-trend sparkline next to the current price.
                // Returns null until the row has ≥2 history points so a
                // freshly-added watchlist item shows the price + delta
                // alone for the first day or two.
                _buildPriceSparkline(item),
            );

        // Market badge + missing pill (lead shows a single pill, watching
        // shows the full marketLabel(...) text).
        const missingMiniBadge = isLead && isMissing
            ? domEl("span", { className: "market-badge missing", text: "Пропало" })
            : null;
        const watchingMarketBadge = isWatching
            ? domEl("div", { className: "watchlist-card-meta" }, _buildMarketBadge(item, marketLabel))
            : null;

        const profitNode = isLead ? _buildLeadProfitNode(item) : _buildPotentialProfitNode(item);

        const bodyClass = isLead ? "lead-card-body" : "watchlist-card-body";
        const topClass = isLead ? "lead-card-top" : "watchlist-card-top";

        const fields = isLead ? _buildLeadFields(item, { signal }) : _buildWatchlistFields(item, { signal });
        const actionsRow = isLead
            ? _buildLeadActions(item)
            : _buildWatchlistActions(item, isMissing);

        card.appendChild(
            domFragment(
                isMissing ? _buildMissingBanner(mode) : null,
                domEl(
                    "div",
                    { className: `${topClass}${isMissing ? " is-missing" : ""}` },
                    buildMediaNode(
                        "watchlist-thumb",
                        "watchlist-thumb-placeholder",
                        "Нет фото",
                        item.thumbnail,
                        item.title || "",
                        { displayPx: 72 },
                    ),
                    domEl(
                        "div",
                        { className: bodyClass },
                        titleNode,
                        priceRow,
                        missingMiniBadge,
                        profitNode,
                        watchingMarketBadge,
                    ),
                ),
                fields,
                actionsRow,
            )
        );

        _wireCardHandlers(card, item, mode, signal);
        _attachSwipeReveal(card, item, mode);

        // FE-H5/UX-H2: keyboard activation for watching cards. Only
        // the outer <article> has role=button in that mode; lead cards
        // are a focusable group (see role assignment above) and rely
        // on the nested native <button>s for activation, so we skip
        // the handler for them to avoid phantom "Enter does nothing"
        // feedback. Guard on event.target===card so pressing Space in
        // the inline "Заметка" input still types a space.
        if (mode === "watching") {
            card.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                if (event.target !== card) return;
                event.preventDefault();
                void actions.openWatchlistDetail(item);
            }, { signal });
        }

        return card;
    }

    // Backwards-compat thin wrappers — callers in render_cards.js still
    // import these names. Keeping them lets us land the unification
    // without touching every render path.
    const buildLeadNode = (lead, signal) => buildItemCard(lead, { mode: "lead", signal });
    const buildWatchlistNode = (item, marketLabel, signal) =>
        buildItemCard(item, { mode: "watching", marketLabel, signal });

    return {
        buildListingNode,
        buildItemCard,
        buildLeadNode,
        buildWatchlistNode,
    };
}


;
/**
 * virtual_list.js — Lightweight virtual scrolling for large lists (50+ items).
 *
 * Usage:
 *   const vl = createVirtualList(container, {
 *       itemHeight: 160,
 *       bufferSize: 5,
 *       renderFn: (item, index) => buildListingNode(item),
 *   });
 *   vl.setItems(items);
 *
 * Features:
 *   - Renders only visible items + small buffer above/below viewport
 *   - Uses sticky viewport + spacer technique (no absolute positioning hacks)
 *   - requestAnimationFrame-throttled scroll handler
 *   - DOM recycling: only entering/exiting items are created/removed;
 *     items that remain visible are left untouched, reducing GC pressure
 *   - Automatic cleanup via destroy()
 */

function createVirtualList(container, options) {
    let {
        itemHeight = 160,
        fixedHeight = null,
        estimateHeight = null,
        bufferSize = 5,
        maxHeight = "70vh",
        renderFn,
    } = options;

    // When fixedHeight is set, use it directly instead of measuring
    // When estimateHeight is set, use it as initial guess before measurement
    let effectiveItemHeight = fixedHeight ?? estimateHeight ?? itemHeight;

    let items = [];
    let scrollTop = 0;
    let visibleStart = 0;
    let visibleEnd = 0;
    let rafId = null;
    let destroyed = false;

    // DOM recycling map: index → DOM element for currently rendered items
    let renderedMap = new Map();

    let _sortedKeysCache = null;
    let _sortedKeysDirty = true;

    function _invalidateSortedKeys() {
        _sortedKeysDirty = true;
    }

    function _getSortedKeys() {
        if (_sortedKeysDirty) {
            _sortedKeysCache = [...renderedMap.keys()].sort((a, b) => a - b);
            _sortedKeysDirty = false;
        }
        return _sortedKeysCache;
    }

    // Validate required params
    if (!container || typeof renderFn !== "function") {
        return {
            setItems: function () {},
            getItems: function () { return []; },
            refresh: function () {},
            destroy: function () {},
        };
    }

    // Create spacer element (provides scrollable height)
    const spacer = document.createElement("div");
    spacer.setAttribute("aria-hidden", "true");
    spacer.style.position = "relative";
    container.appendChild(spacer);

    // Create viewport (sticky container for visible items)
    const viewport = document.createElement("div");
    viewport.setAttribute("role", "list");
    viewport.style.cssText =
        "position:sticky;top:0;overflow:hidden;";
    container.appendChild(viewport);

    // Bottom spacer (always the last child of viewport)
    const bottomSpacer = document.createElement("div");
    bottomSpacer.setAttribute("data-vl-bottom-spacer", "true");
    bottomSpacer.setAttribute("aria-hidden", "true");
    viewport.appendChild(bottomSpacer);

    // Configure container scrolling
    container.style.overflowY = "auto";
    container.style.maxHeight = maxHeight;
    // Preserve existing role if set to "list", otherwise add it
    if (!container.getAttribute("role")) {
        container.setAttribute("role", "list");
    }

    function renderVisibleItems() {
        if (destroyed) return;

        const containerHeight = container.clientHeight;
        if (containerHeight === 0) return; // Container not visible yet

        const start = Math.max(
            0,
            Math.floor(scrollTop / effectiveItemHeight) - bufferSize
        );
        const end = Math.min(
            items.length,
            Math.ceil((scrollTop + containerHeight) / effectiveItemHeight) + bufferSize
        );

        // Skip if visible range hasn't changed
        if (start === visibleStart && end === visibleEnd) return;
        visibleStart = start;
        visibleEnd = end;

        // Calculate spacer heights
        const topPadding = start * effectiveItemHeight;
        const bottomPadding = (items.length - end) * effectiveItemHeight;
        spacer.style.height = `${topPadding}px`;
        bottomSpacer.style.height = `${bottomPadding}px`;

        // Determine which indices are now visible
        const newIndices = new Set();
        for (let i = start; i < end; i++) {
            newIndices.add(i);
        }

        // Remove items that scrolled out of the visible range
        for (const [idx, el] of renderedMap) {
            if (!newIndices.has(idx)) {
                el.remove();
                renderedMap.delete(idx);
                _invalidateSortedKeys();
            }
        }

        // Add items that scrolled into the visible range
        // Build a fragment for all new items, then insert at the right position
        const fragment = document.createDocumentFragment();
        const toInsert = [];

        for (let i = start; i < end; i++) {
            if (renderedMap.has(i)) continue; // Already rendered, leave untouched

            const el = renderFn(items[i], i);
            if (el) {
                if (effectiveItemHeight) {
                    el.style.minHeight = `${effectiveItemHeight}px`;
                }
                el.setAttribute("data-vl-index", String(i));
                fragment.appendChild(el);
                toInsert.push([i, el]);
            }
        }

        if (toInsert.length === 0) return; // Nothing new to add

        // Find the insertion point: before the first already-rendered
        // item whose index is greater than the smallest new index, or
        // before the bottom spacer if no such item exists.
        const minNewIdx = toInsert[0][0];
        let insertBefore = bottomSpacer; // default: before bottom spacer

        // Binary search over sorted keys for O(log n) instead of O(n) scan
        const sortedKeys = _getSortedKeys();
        let lo = 0, hi = sortedKeys.length;
        while (lo < hi) {
            const mid = (lo + hi) >>> 1;
            if (sortedKeys[mid] > minNewIdx) hi = mid;
            else lo = mid + 1;
        }
        if (lo < sortedKeys.length) {
            const closestHigherIdx = sortedKeys[lo];
            insertBefore = renderedMap.get(closestHigherIdx);
        }

        viewport.insertBefore(fragment, insertBefore);

        // Register new items in the recycling map
        for (const [idx, el] of toInsert) {
            renderedMap.set(idx, el);
            _invalidateSortedKeys();
        }
    }

    function onScroll() {
        scrollTop = container.scrollTop;
        if (rafId !== null) return; // Already scheduled
        rafId = requestAnimationFrame(function () {
            rafId = null;
            renderVisibleItems();
        });
    }

    container.addEventListener("scroll", onScroll, { passive: true });

    // Handle window resize (container height may change)
    function onResize() {
        renderVisibleItems();
    }
    window.addEventListener("resize", onResize, { passive: true });

    return {
        /**
         * Update the item list and re-render.
         * @param {Array} newItems
         */
        setItems: function (newItems) {
            if (destroyed) return;
            items = newItems || [];
            // Clear recycled DOM nodes — data changed, old nodes are stale
            renderedMap.clear();
            _invalidateSortedKeys();
            domClear(viewport);
            viewport.appendChild(bottomSpacer);
            // Reset scroll position when data changes
            container.scrollTop = 0;
            scrollTop = 0;
            visibleStart = 0;
            visibleEnd = 0;
            renderVisibleItems();
        },

        /**
         * Get current items.
         * @returns {Array}
         */
        getItems: function () {
            return items;
        },

        /**
         * Force re-render of visible items (e.g., after data mutation).
         */
        refresh: function () {
            if (destroyed) return;
            // Clear recycled DOM nodes — data changed, old nodes are stale
            renderedMap.clear();
            _invalidateSortedKeys();
            domClear(viewport);
            viewport.appendChild(bottomSpacer);
            visibleStart = -1; // Force re-render
            visibleEnd = -1;
            renderVisibleItems();
        },

        /**
         * Update the fixed item height and re-render.
         * @param {number} height
         */
        setItemHeight: function (height) {
            if (destroyed) return;
            effectiveItemHeight = height;
            visibleStart = -1;
            visibleEnd = -1;
            renderVisibleItems();
        },

        /**
         * Get the current item height.
         * @returns {number}
         */
        getItemHeight: function () {
            return effectiveItemHeight;
        },

        /**
         * Clean up event listeners and DOM.
         */
        destroy: function () {
            destroyed = true;
            renderedMap.clear();
            _invalidateSortedKeys();
            if (rafId !== null) {
                cancelAnimationFrame(rafId);
                rafId = null;
            }
            container.removeEventListener("scroll", onScroll);
            window.removeEventListener("resize", onResize);
            if (spacer.parentNode) spacer.parentNode.removeChild(spacer);
            if (viewport.parentNode) viewport.parentNode.removeChild(viewport);
            // Reset container styles that were set by virtual list
            container.style.overflowY = "";
            container.style.maxHeight = "";
        },
    };
}




;
/**
 * render_cards.js — buildListingNode, renderListings,
 * lead cards, watchlist cards.
 */

function createRenderCards(context) {
    const {
        state,
        elements,
        actions,
        hasTelegramInitData,
        safeRender: safeRender,
    } = context;
    const builders = createRenderCardBuilders(context);
    const {
        buildListingNode,
        buildLeadNode,
        buildWatchlistNode,
    } = builders;

    /* ===== Shared helpers (from original file) ===== */

    function _resetContainer(container) {
        if (!container) return;
        if (container._abortController) {
            container._abortController.abort();
            container._abortController = null;
        }
        container.style.overflowY = "";
        container.style.maxHeight = "";
        domClear(container);
    }

    function _getSignal(container) {
        if (!container._abortController) {
            container._abortController = new AbortController();
        }
        return container._abortController.signal;
    }

    function _buildSkeletonCard() {
        const skel = domEl("div", {
            className: "skeleton-card",
            attrs: { "aria-hidden": "true" },
        });
        skel.appendChild(domEl("div", { className: "skel-bar", attrs: { style: "width:60%" } }));
        skel.appendChild(domEl("div", { className: "skel-bar", attrs: { style: "width:40%" } }));
        skel.appendChild(domEl("div", { className: "skel-bar", attrs: { style: "width:30%" } }));
        return skel;
    }

    function verdictClassName(verdict) {
        if (!verdict) return "neutral";
        if (verdict.includes("Хорошая")) return "zabirat";
        if (verdict.includes("Ниже")) return "smotret";
        if (verdict.includes("Средняя")) return "norm";
        if (verdict.includes("Выше")) return "mimo";
        return "neutral";
    }

    function matchesFilters(item) {
        // Price range filter
        const itemPrice = item.price != null ? Number(item.price) : null;
        const hasPriceRange = state.filters.minPrice != null || state.filters.maxPrice != null;
        // Exclude "Договорная" (price null) when a price range is set
        if (hasPriceRange && itemPrice == null) return false;
        if (state.filters.minPrice != null && itemPrice != null && itemPrice < state.filters.minPrice) return false;
        if (state.filters.maxPrice != null && itemPrice != null && itemPrice > state.filters.maxPrice) return false;

        // Condition filter - handle various formats from API
        if (state.filters.condition) {
            const itemCondition = item.condition || "";
            // Normalize condition values for comparison
            const normalizedItemCondition = itemCondition.toLowerCase();
            const normalizedStateCondition = state.filters.condition.toLowerCase();
            
            // Map state condition to possible API values
            const conditionMap = {
                "new": ["new", "новый", "2"],
                "used": ["used", "б/у", "1"],
            };
            
            const validValues = conditionMap[normalizedStateCondition] || [normalizedStateCondition];
            if (!validValues.includes(normalizedItemCondition)) return false;
        }
        
        // Seller type filter
        if (state.filters.sellerType) {
            const isShop = item.company_ad || item.seller_type === "Магазин" || item.seller_type === "shop" || item.seller_type?.toLowerCase() === "shop";
            if (state.filters.sellerType === "private" && isShop) return false;
            if (state.filters.sellerType === "shop" && !isShop) return false;
        }
        
        // Region filter — matches region_name or area_name exactly
        if (state.filters.regionName) {
            const filterRegion = state.filters.regionName.toLowerCase().trim();
            if (!filterRegion) return true;
            const itemRegion = (item.region_name || "").toLowerCase().trim();
            const itemArea = (item.area_name || "").toLowerCase().trim();
            if (!itemRegion && !itemArea) return false;
            if (itemRegion === filterRegion || itemArea === filterRegion) return true;
            return false;
        }
        
        return true;
    }

    function applyFilters(items) {
        if (!state.filters.condition && !state.filters.sellerType && state.filters.minPrice == null && state.filters.maxPrice == null && !state.filters.regionName) return items;
        return items.filter(matchesFilters);
    }

    /* ===== Collections ===== */

    function _delegateListingClick(container) {
        if (container._listingDelegated) return;
        container._listingDelegated = true;
        container.addEventListener("click", (event) => {
            const card = event.target.closest(".listing");
            if (!card || !card._item) return;
            const item = card._item;
            const actionBtn = event.target.closest('[data-role="lead"], [data-role="watch"]');
            if (actionBtn) {
                event.stopPropagation();
                if (actionBtn.dataset.role === "lead") {
                    void actions.addLeadFromListing(item);
                } else {
                    void actions.addWatchlistFromListing(item);
                }
                return;
            }
            if (event.target.closest(".listing-top")) {
                void actions.openListingDetail(item);
            }
        });
        container.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            const card = event.target.closest(".listing");
            if (!card || !card._item) return;
            if (event.target !== card) return;
            event.preventDefault();
            void actions.openListingDetail(card._item);
        });
    }

    /**
     * Renders a collection of items into a container.
     *
     * @param {Array} items - The data items to render
     * @param {HTMLElement} container - The DOM container to render into
     * @param {HTMLElement} badge - Optional badge element for item count
     * @param {string} emptyText - Text to show when no items
     * @param {number|null} totalOverride - Override for total count display
     * @returns {boolean} True if content was rendered, false if empty
     */
    function renderListingsCollection(items, container, badge, emptyText, totalOverride = null) {
        return safeRender('renderListingsCollection', () => {
            if (!container) return false;
            _delegateListingClick(container);
            _resetContainer(container);

            const filtered = applyFilters(items);
            if (!filtered.length) {
                const buildEmpty = context.buildEmptyState;
                if (typeof buildEmpty === "function") {
                    container.appendChild(
                        buildEmpty({
                            icon: "listings",
                            title: "Ничего не найдено",
                            hint: emptyText || "Попробуйте изменить запрос или снять фильтры.",
                        })
                    );
                } else {
                    const note = document.createElement("p");
                    note.className = "tracker-empty";
                    note.textContent = emptyText;
                    container.appendChild(note);
                }
                if (badge) {
                    badge.textContent = "0";
                }
                return false;
            }

            const fragment = document.createDocumentFragment();
            for (const item of filtered) {
                fragment.appendChild(buildListingNode(item, verdictClassName));
            }
            container.appendChild(fragment);

            if (badge) {
                // Match kufar.by header behavior: the pill shows the
                // total number of matching ads on Kufar, not how many
                // we've actually rendered on the page (we cap at 200
                // for performance). Falls back to the rendered count
                // only if no API total is available.
                const apiTotal = totalOverride != null ? totalOverride : filtered.length;
                badge.textContent = `${apiTotal} объявлений`;
            }
            return true;
        });
    }

    /**
     * Append a pagination sentinel to the bottom of a list container.
     *
     * Two roles: the IntersectionObserver-watched element that
     * triggers ``onLoadMore`` when scrolled into view, AND the
     * visible "Загрузить ещё / Показано N из M" UI so users can
     * tap to fetch the next page if scroll-driven loading misses.
     */
    function _appendPaginationSentinel(container, options) {
        if (!container) return;
        // Disconnect any previous observer on this container to prevent
        // orphaned IntersectionObserver callbacks from stale renders.
        const oldSentinels = container.querySelectorAll(".list-pagination-sentinel");
        for (const old of oldSentinels) {
            if (old._paginationObserver) {
                old._paginationObserver.disconnect();
            }
        }
        const {
            renderedCount,
            totalCount,
            hasMore,
            isLoadingMore,
            onLoadMore,
            allLoadedText = "Все объявления загружены.",
        } = options;
        const sentinel = document.createElement("div");
        sentinel.className = "list-pagination-sentinel";
        if (hasMore) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "list-pagination-button";
            button.disabled = Boolean(isLoadingMore);
            button.textContent = isLoadingMore
                ? "Загружаю..."
                : `Загрузить ещё (${renderedCount} из ${totalCount || "—"})`;
            button.addEventListener("click", () => {
                if (typeof onLoadMore === "function") {
                    onLoadMore();
                }
            });
            sentinel.appendChild(button);
            // Auto-trigger on visibility — once the user scrolls
            // close enough to the sentinel, kick off the next page
            // without waiting for a tap. IntersectionObserver isn't
            // supported in really old WebViews; the button stays as
            // a manual fallback.
            if (typeof IntersectionObserver !== "undefined") {
                const observer = new IntersectionObserver(
                    (entries) => {
                        for (const entry of entries) {
                            if (entry.isIntersecting && typeof onLoadMore === "function") {
                                observer.disconnect();
                                onLoadMore();
                                break;
                            }
                        }
                    },
                    // Trigger the next page well before the user
                    // hits the bottom — 1200px ≈ 6-7 cards of
                    // headroom on phone screens, so by the time the
                    // sentinel actually scrolls into view the next
                    // batch is usually already rendered.
                    { rootMargin: "1200px 0px" },
                );
                observer.observe(sentinel);
                sentinel._paginationObserver = observer;
            }
        } else if (totalCount && renderedCount > 0) {
            const note = document.createElement("p");
            note.className = "list-pagination-note";
            note.textContent = allLoadedText;
            sentinel.appendChild(note);
        }
        if (sentinel.childNodes.length > 0) {
            container.appendChild(sentinel);
        }
    }

    function renderListings() {
        return safeRender('renderListings', () => {
            if (state.ui.loading) return;
            // Keep skeletons while listings request is in flight
            if (state.listings._pending) return;

            // Show skeleton cards while listings are loading (e.g. sort change)
            if (state.listings.loading && !state.listings.items.length) {
                if (elements.listingsList) {
                    _resetContainer(elements.listingsList);
                    for (let i = 0; i < 3; i++) {
                        elements.listingsList.appendChild(_buildSkeletonCard());
                    }
                    elements.listingsSection.hidden = false;
                }
                if (elements.listingsTotalBadge) {
                    elements.listingsTotalBadge.textContent = "";
                }
                return;
            }

            const hasData = state.listings.items.length > 0 || state.listings.total > 0;
            const hasContent = renderListingsCollection(
                state.listings.items,
                elements.listingsList,
                elements.listingsTotalBadge,
                "По этому запросу пока нечего показать.",
                state.listings.total || null
            );
            if (elements.listingsFallbackBadge) {
                elements.listingsFallbackBadge.hidden = !state.listings.fallbackUsed;
            }
            if (hasContent && elements.listingsList) {
                _appendPaginationSentinel(elements.listingsList, {
                    renderedCount: state.listings.items.length,
                    totalCount: state.listings.total,
                    hasMore: state.listings.hasMore,
                    isLoadingMore: state.listings.loadingMore,
                    onLoadMore: () => {
                        if (typeof actions.loadMoreListings === "function") {
                            void actions.loadMoreListings();
                        }
                    },
                });
            }
            // Keep section visible if data exists but was filtered out —
            // the empty message inside the container tells the user why.
            elements.listingsSection.hidden = !hasContent && !hasData;
        });
    }

    /* ===== Leads (Deals) ===== */

    function workflowLabel(value) {
        const labels = {
            new: "Новый",
            reviewing: "Смотреть",
            in_progress: "В работе",
            negotiating: "Торг",
            bought: "Купил",
            reselling: "В продаже",
            sold: "Продано",
            closed: "Закрыто",
            skipped: "Пропустить",
            deferred: "Позже",
            watching: "Слежу",
            default: "Обычное",
            important: "Важное",
            very_important: "Очень важное",
        };
        return labels[value] || value || "Обычное";
    }

    function leadSortValue(item) {
        const order = {
            in_progress: 0,
            negotiating: 1,
            reviewing: 2,
            new: 3,
            deferred: 4,
            bought: 5,
            reselling: 6,
            sold: 7,
            skipped: 8,
            closed: 9,
        };
        return order[item.status] ?? 99;
    }

    // closed/skipped leads are "done" — they live in the collapsible
    // "История сделок" block below the list. Everything else is an
    // active purchase visible in the "Покупки" tab.
    function isActiveLead(lead) {
        return lead.status !== "closed" && lead.status !== "skipped";
    }

    function emptyMessageForFilter(filter) {
        switch (filter) {
            case "watching":
                return "Здесь будут объявления, за которыми ты следишь. Добавь лот через поиск → «В избранное».";
            case "purchases":
            default:
                return "Нет сделок в работе. Добавь лот через поиск → «В покупки».";
        }
    }

    function buildItemsEmpty(filter) {
        const buildEmpty = context.buildEmptyState;
        if (typeof buildEmpty !== "function") {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = emptyMessageForFilter(filter);
            return note;
        }
        if (filter === "watching") {
            return buildEmpty({
                icon: "watchlist",
                title: "Здесь будут отслеживаемые лоты",
                hint: "Сохрани объявление через «В избранное», и сюда придут уведомления о смене цены и снятии с продажи.",
            });
        }
        return buildEmpty({
            icon: "leads",
            title: "Нет сделок в работе",
            hint: "Найди лот через поиск и нажми «В покупки», чтобы вести его до продажи и считать прибыль.",
        });
    }

    function renderLeads() {
        return safeRender('renderLeads', () => {
        const container = elements.leadInboxList;
        if (!container) return;

        // Destroy existing virtual list before reset
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        _resetContainer(container);

        // Call hero stats and profit dashboard via hooks
        if (context._hooks?.renderDealsHeroStats) context._hooks.renderDealsHeroStats();
        if (context._hooks?.renderProfitDashboard) context._hooks.renderProfitDashboard();
        if (context._hooks?.renderHistoryDeals) context._hooks.renderHistoryDeals();

        // Update tab counts and visibility of the "Очистить" buttons.
        const activeLeads = (state.leads.items || []).filter(isActiveLead);
        const counts = {
            watching: (state.watchlist.items || []).length,            purchases: activeLeads.length,
        };

        if (elements.itemsCountBadges) {
            for (const [key, badge] of Object.entries(elements.itemsCountBadges)) {
                if (!badge) continue;
                const value = counts[key] || 0;
                badge.textContent = String(value);
                badge.hidden = value === 0;
            }
        }
        const filter = state.leads.itemsFilter === "watching" ? "watching" : "purchases";
        for (const button of elements.itemsFilterButtons || []) {
            const isActive = button.dataset.itemsFilter === filter;
            button.classList.toggle("is-active", isActive);
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-selected", String(isActive));
        }

        if (elements.clearAllLeadsButton) {
            elements.clearAllLeadsButton.hidden = filter === "watching";
        }
        if (elements.deleteAllWatchlistButton) {
            elements.deleteAllWatchlistButton.hidden = filter !== "watching";
        }

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Раздел «Мои объявления» доступен внутри Telegram Mini App.";
            container.appendChild(note);
            return;
        }

        // Pick the right collection for the active tab. closed/skipped
        // leads are intentionally not rendered here — they're available
        // in "История сделок" below the list.
        let entries;
        if (filter === "watching") {
            entries = (state.watchlist.items || []).map((item) => ({
                kind: "watch",
                data: item,
                updatedAt: item.updated_at || item.last_seen_at || item.created_at || "",
            }));
            entries.sort((a, b) =>
                String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""))
            );
        } else {
            entries = activeLeads
                .map((lead) => ({
                    kind: "lead",
                    data: lead,
                    updatedAt: lead.updated_at || lead.created_at || "",
                }))
                .sort((a, b) => {
                    const statusDelta = leadSortValue(a.data) - leadSortValue(b.data);
                    if (statusDelta !== 0) return statusDelta;
                    return String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""));
                });
        }

        if (!entries.length) {
            container.appendChild(buildItemsEmpty(filter));
            return;
        }

        const watchlistMarketLabel = (val) => marketLabel(val);
        const WATCHLIST_ITEM_HEIGHT = 220;
        const VIRTUAL_LIST_THRESHOLD = 30;

        if (filter === "watching" && entries.length > VIRTUAL_LIST_THRESHOLD) {
            container._virtualList = createVirtualList(container, {
                itemHeight: WATCHLIST_ITEM_HEIGHT,
                fixedHeight: WATCHLIST_ITEM_HEIGHT,
                bufferSize: 5,
                renderFn: (entry, index) => buildWatchlistNode(entry.data, watchlistMarketLabel),
            });
            container._virtualList.setItems(entries);
        } else {
            const signal = _getSignal(container);
            for (const entry of entries) {
                if (entry.kind === "lead") {
                    container.appendChild(buildLeadNode(entry.data, signal));
                } else {
                    container.appendChild(buildWatchlistNode(entry.data, watchlistMarketLabel, signal));
                }
            }
        }
        });
    }

    /* ===== Watchlist ===== */

    function marketLabel(value) {
        const labels = {
            active: "На рынке",
            price_drop: "Падение цены",
            missing: "Пропало",
        };
        return labels[value] || value || "Без сигнала";
    }

    function watchlistMatchesFilter(item) {
        if (state.watchlist.filter === "all") {
            return true;
        }
        return item.workflow_status === state.watchlist.filter;
    }

    function watchlistSortValue(item) {
        const marketOrder = {
            price_drop: 0,
            missing: 1,
            active: 2,
        };
        const workflowOrder = {
            in_progress: 0,
            reviewing: 1,
            watching: 2,
            skipped: 3,
        };
        return `${marketOrder[item.market_status] ?? 9}:${workflowOrder[item.workflow_status] ?? 9}`;
    }

    function renderWatchlist() {
        return safeRender('renderWatchlist', () => {
        const container = elements.watchlistList;
        if (!container) return;

        // Destroy existing virtual list before reset
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        _resetContainer(container);

        if (context._hooks?.renderWatchlistFilters) context._hooks.renderWatchlistFilters();

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Отслеживание лотов доступно внутри Telegram Mini App.";
            container.appendChild(note);
            return;
        }

        // Show skeleton cards while loading
        if (state.watchlist._loading) {
                for (let i = 0; i < 3; i++) container.appendChild(_buildSkeletonCard());
                return;
            }

        const filteredWatchlist = [...state.watchlist.items]
            .filter(watchlistMatchesFilter)
            .sort((left, right) => {
                const rankDelta = watchlistSortValue(left).localeCompare(watchlistSortValue(right));
                if (rankDelta !== 0) {
                    return rankDelta;
                }
                return String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
            });

        if (!filteredWatchlist.length) {
            const buildEmpty = context.buildEmptyState;
            if (typeof buildEmpty === "function") {
                if (state.watchlist.items.length) {
                    container.appendChild(
                        buildEmpty({
                            icon: "watchlist",
                            title: "По этому фильтру ничего нет",
                            hint: "Попробуйте переключиться на «Все», чтобы увидеть весь список.",
                        })
                    );
                } else {
                    container.appendChild(
                        buildEmpty({
                            icon: "watchlist",
                            title: "Здесь будут ваши избранные лоты",
                            hint: "Нажмите «В избранное» в карточке объявления, чтобы следить за ценой и снятием с продажи.",
                            actionLabel: "Найти объявления",
                            onAction: () => {
                                if (typeof context.setActiveView === "function") {
                                    context.setActiveView("overview");
                                }
                                const searchInput = document.querySelector(".search-input");
                                if (searchInput) searchInput.focus();
                            },
                        })
                    );
                }
            } else {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = state.watchlist.items.length
                    ? "По текущему фильтру ничего нет. Попробуйте «Все»."
                    : "Сохранённых лотов пока нет. Нажмите «В избранное» в карточке объявления.";
                container.appendChild(note);
            }
            return;
        }

        // Re-enable virtual scrolling for watchlist — cards have consistent layout
        const WATCHLIST_ITEM_HEIGHT = 220;
        const VIRTUAL_LIST_THRESHOLD = 30;
        if (filteredWatchlist.length > VIRTUAL_LIST_THRESHOLD) {
            container._virtualList = createVirtualList(container, {
                itemHeight: WATCHLIST_ITEM_HEIGHT,
                fixedHeight: WATCHLIST_ITEM_HEIGHT,
                bufferSize: 5,
                renderFn: (item, index) => buildWatchlistNode(item, marketLabel),
            });
            container._virtualList.setItems(filteredWatchlist);
        } else {
            const signal = _getSignal(container);
            for (const item of filteredWatchlist) {
                container.appendChild(buildWatchlistNode(item, marketLabel, signal));
            }
        }
        });
    }

    return {
        buildListingNode,
        renderListingsCollection,
        renderListings,
        renderLeads,
        renderWatchlist,
    };
}


;
/**
 * render_views.js — Hero stats, Stats, segments, geography,
 * sort/discount buttons, tracker event filters, history range, deal/tracker inputs.
 */

function createRenderViews(context) {
    const {
        state,
        elements,
        formatPrice,
        escapeHtml: escapeHtml,
        safeRender: safeRender,
    } = context;

    function appendHeroStat(container, value, label) {
        const stat = document.createElement("span");
        stat.className = "hero-stat";
        const valueEl = document.createElement("span");
        valueEl.className = "hero-stat-val mono";
        valueEl.textContent = value;
        stat.appendChild(valueEl);
        stat.append(` ${label}`);
        container.appendChild(stat);
    }

    /* ===== Hero Stats ===== */

    function renderTrackingHeroStats() {
        if (!elements.trackingHeroStats) return;
        const trackerCount = state.trackers.items.length;
        const eventCount = state.trackers.events.length;
        domClear(elements.trackingHeroStats);
        appendHeroStat(elements.trackingHeroStats, String(trackerCount), "трекеров");
        appendHeroStat(elements.trackingHeroStats, String(eventCount), "событий");
    }

    function renderDealsHeroStats() {
        if (!elements.dealsHeroStats) return;
        const activeLeads = state.leads.items.filter((l) => l.status !== "closed");
        domClear(elements.dealsHeroStats);
        appendHeroStat(elements.dealsHeroStats, String(activeLeads.length), "сделок");

        // Pipeline progress indicator — show status distribution
        if (activeLeads.length > 0) {
            const statusOrder = ["new", "researching", "bought", "sold"];
            const statusLabels = { new: "Новые", researching: "В работе", bought: "Куплено", sold: "Продано" };
            const pipeline = document.createElement("div");
            pipeline.className = "pipeline-bar";
            pipeline.setAttribute("role", "meter");
            pipeline.setAttribute("aria-label", "Прогресс сделок");
            for (const status of statusOrder) {
                const count = activeLeads.filter((l) => l.status === status).length;
                if (count > 0) {
                    const seg = document.createElement("span");
                    seg.className = `pipeline-seg pipeline-seg--${status}`;
                    seg.style.width = `${(count / activeLeads.length) * 100}%`;
                    seg.textContent = `${statusLabels[status] || status} ${count}`;
                    pipeline.appendChild(seg);
                }
            }
            elements.dealsHeroStats.appendChild(pipeline);
        }
    }

    /* ===== Sort / Discount / Filter buttons ===== */

    function renderFilterDropdown() {
        return safeRender('renderFilterDropdown', () => {
            if (!elements.filterDropdown || !elements.filterCategories) return;

            // Toggle visibility
            elements.filterDropdown.hidden = !state.filters.filterDropdownOpen;

            // Render category chips
            domClear(elements.filterCategories);
            if (!state.filters.categories.length || state.filters.categories.length <= 1) {
                const empty = document.createElement("span");
                empty.className = "filter-empty";
                empty.textContent = "Нет категорий";
                elements.filterCategories.appendChild(empty);
            } else {
                // Use total_results from stats instead of sum of categories
                // because not all ads have category data
                const totalResults = Number(state.misc.stats?.total_results || 0);
                const totalCount = state.filters.categories.reduce((sum, cat) => sum + cat.count, 0);
                // Show the larger of: total results from Kufar, or sum of categories
                const displayTotal = Math.max(totalResults, totalCount);
                
                // Use pendingCategory for display, fall back to applied category
                const displayCategory = state.filters.pendingCategory !== undefined ? state.filters.pendingCategory : state.filters.category;
                
                const allButton = document.createElement("button");
                const allActive = displayCategory == null;
                allButton.className = `filter-chip ${allActive ? 'active' : ''}`;
                allButton.dataset.category = "";
                allButton.type = "button";
                allButton.setAttribute("aria-pressed", String(allActive));
                allButton.textContent = `Все (${displayTotal})`;
                elements.filterCategories.appendChild(allButton);
                for (const cat of state.filters.categories) {
                    const button = document.createElement("button");
                    const active = displayCategory === cat.id;
                    button.className = `filter-chip ${active ? 'active' : ''}`;
                    button.dataset.category = String(cat.id);
                    button.type = "button";
                    button.setAttribute("aria-pressed", String(active));
                    button.textContent = `${cat.label} (${cat.count})`;
                    elements.filterCategories.appendChild(button);
                }
            }

            // Update price range inputs with PENDING values
            if (elements.filterMinPrice) {
                elements.filterMinPrice.value = state.filters.pendingMinPrice != null ? state.filters.pendingMinPrice : "";
            }
            if (elements.filterMaxPrice) {
                elements.filterMaxPrice.value = state.filters.pendingMaxPrice != null ? state.filters.pendingMaxPrice : "";
            }

            // Update region dropdown with PENDING value
            if (elements.filterRegion) {
                elements.filterRegion.value = state.filters.pendingRegionName || "";
            }

            // Update condition/seller chip states with PENDING values
            if (elements.filterConditions) {
                for (const chip of elements.filterConditions.querySelectorAll("[data-condition]")) {
                    const active = chip.dataset.condition === state.filters.pendingCondition;
                    chip.classList.toggle("active", active);
                    chip.setAttribute("aria-pressed", String(active));
                }
            }
            if (elements.filterSellers) {
                for (const chip of elements.filterSellers.querySelectorAll("[data-seller]")) {
                    const active = chip.dataset.seller === state.filters.pendingSellerType;
                    chip.classList.toggle("active", active);
                    chip.setAttribute("aria-pressed", String(active));
                }
            }

            // Update filter count badge on the filter button
            const badge = document.getElementById("filter-active-badge");
            if (badge) {
                let count = 0;
                if (state.filters.category != null) count++;
                if (state.filters.condition) count++;
                if (state.filters.sellerType) count++;
                if (state.filters.minPrice != null) count++;
                if (state.filters.maxPrice != null) count++;
                if (state.filters.regionName) count++;
                badge.textContent = count;
                badge.hidden = count === 0;
            }
        });
    }

    function renderSortButtons() {
        for (const button of elements.sortButtons) {
            const active = button.dataset.sort === state.search.sort;
            button.classList.toggle("active", active);
            button.setAttribute("aria-pressed", String(active));
        }
    }

    function renderDiscountButtons() {
        for (const button of elements.discountButtons) {
            const from = Number(button.dataset.discountFrom);
            const to = Number(button.dataset.discountTo);
            const active = from === state.filters.discountFromPercent
                && to === state.filters.discountToPercent;
            button.classList.toggle("active", active);
            button.setAttribute("aria-pressed", String(active));
        }
    }

    function renderHistoryRangeButtons() {
        if (elements.historyBadge) {
            elements.historyBadge.textContent = `${state.misc.historyDays} дней`;
        }
        for (const button of elements.historyRangeButtons) {
            const active = Number(button.dataset.historyDays) === state.misc.historyDays;
            button.classList.toggle("active", active);
            button.setAttribute("aria-pressed", String(active));
        }
    }

    /* ===== Inputs ===== */

    function renderDealInputs() {
        if (elements.dealFromInput) {
            elements.dealFromInput.value = String(state.filters.discountFromPercent);
        }
        if (elements.dealToInput) {
            elements.dealToInput.value = String(state.filters.discountToPercent);
        }
    }

    function renderTrackerInputs() {
        if (elements.trackerMinDiscountInput) {
            elements.trackerMinDiscountInput.value = String(state.trackers.minDiscountPercent ?? 10);
        }
        if (elements.trackerMaxPriceInput) {
            elements.trackerMaxPriceInput.value = state.trackers.maxPriceByn ?? "";
        }
        if (elements.trackerExcludeDuplicatesToggle) {
            elements.trackerExcludeDuplicatesToggle.checked = Boolean(state.trackers.excludeDuplicates);
        }
        if (elements.trackerSellerSelect) {
            elements.trackerSellerSelect.value = state.trackers.sellerType || "";
        }
        if (elements.trackerConditionSelect) {
            elements.trackerConditionSelect.value = state.trackers.condition || "";
        }
        if (elements.trackerRegionSelect) {
            elements.trackerRegionSelect.value = state.trackers.regionName || "";
        }
        if (elements.trackerConfigInput) {
            elements.trackerConfigInput.value = state.trackers.configKeyword || "";
        }
    }

    /* ===== Stats ===== */

    function renderStats() {
        return safeRender('renderStats', () => {
            if (!state.misc.stats) {
            elements.statsSection.hidden = true;
            elements.chartSection.hidden = true;
            if (context._hooks?.destroyChart) context._hooks.destroyChart();
            return;
        }

        elements.stats.median.textContent = formatPrice(state.misc.stats.median);
        elements.stats.mean.textContent = formatPrice(state.misc.stats.mean);
        elements.stats.min.textContent = formatPrice(state.misc.stats.min);
        elements.stats.max.textContent = formatPrice(state.misc.stats.max);
        if (state.misc.stats.fair_price_from != null && state.misc.stats.fair_price_to != null) {
            elements.stats.fairRange.replaceChildren(
                domEl("span", { className: "stat-range-item", text: formatPrice(state.misc.stats.fair_price_from) }),
                domEl("span", { className: "stat-range-sep", text: "-" }),
                domEl("span", { className: "stat-range-item", text: formatPrice(state.misc.stats.fair_price_to) }),
            );
        } else {
            elements.stats.fairRange.textContent = "—";
        }
        const totalResults = Number(state.misc.stats.total_results || 0);
        const analyzedCount = Number(state.misc.stats.analyzed_count || state.misc.stats.count || 0);
        elements.stats.coverage.textContent =
            `${analyzedCount} с ценой / ${totalResults}`;
        // Show total with priced count in badge for better clarity
        elements.marketTotalBadge.textContent = `${totalResults} (${analyzedCount} с ценой)`;
        elements.statsSection.hidden = false;
        elements.chartSection.hidden = state.misc.stats.count <= 0;
        if (elements.chartSection.hidden || !state.panels.distribution) {
            if (context._hooks?.destroyChart) context._hooks.destroyChart();
        }
        });
    }

    /* ===== Segments ===== */

    function renderSegments() {
        return safeRender('renderSegments', () => {
        domClear(elements.segmentsGrid);
        if (!state.misc.segments) {
            elements.segmentsSection.hidden = true;
            return;
        }

        const segments = [
            {
                type: "new",
                typeLabel: "Новый",
                sellerLabel: "Частное лицо",
                data: state.misc.segments.new_private,
            },
            {
                type: "new",
                typeLabel: "Новый",
                sellerLabel: "Магазин",
                data: state.misc.segments.new_shop,
            },
            {
                type: "used",
                typeLabel: "Б/у",
                sellerLabel: "Частное лицо",
                data: state.misc.segments.used_private,
            },
            {
                type: "used",
                typeLabel: "Б/у",
                sellerLabel: "Магазин",
                data: state.misc.segments.used_shop,
            },
        ].filter((segment) => segment.data && segment.data.count > 0);

        if (segments.length === 0) {
            elements.segmentsSection.hidden = true;
            return;
        }

        for (const segment of segments) {
            const totalAnalyzed = Number(state.misc.stats?.analyzed_count || state.misc.stats?.count || 0);
            const share = totalAnalyzed
                ? Math.round((Number(segment.data.count || 0) / totalAnalyzed) * 100)
                : 0;
            const marketMedian = Number(state.misc.stats?.median || 0);
            const segmentMedian = Number(segment.data.median || 0);
            const delta = marketMedian && segmentMedian
                ? ((segmentMedian - marketMedian) / marketMedian) * 100
                : 0;
            const deltaClassName = Math.abs(delta) < 0.5 ? "neutral" : (delta >= 0 ? "over" : "under");
            const deltaText = Math.abs(delta) < 0.5
                ? "≈ рынок"
                : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}% к рынку`;
            const card = domEl(
                "div",
                { className: "seg-card" },
                domEl(
                    "div",
                    { className: "seg-head" },
                    domEl("span", { className: `seg-pill ${segment.type}`, text: segment.typeLabel }),
                    domEl("span", { className: "seg-seller", text: segment.sellerLabel }),
                ),
                domEl("span", { className: "seg-price mono", text: formatPrice(segment.data.median) }),
                domEl(
                    "div",
                    { className: "seg-meta-row" },
                    domEl("span", { className: "seg-count", text: `${segment.data.count} с ценой` }),
                    domEl("span", { className: "seg-share", text: `${share}% выборки` }),
                ),
                domEl("span", { className: `seg-delta ${deltaClassName}`, text: deltaText }),
            );
            elements.segmentsGrid.appendChild(card);
        }

        elements.segmentsSection.hidden = false;
        });
    }

    /* ===== Geography ===== */

    function renderGeography() {
        return safeRender('renderGeography', () => {
        domClear(elements.geographyGrid);
        if (!state.misc.geography.length) {
            elements.geographySection.hidden = true;
            return;
        }

        for (const region of state.misc.geography) {
            const card = domEl(
                "div",
                { className: "geo-card" },
                domEl(
                    "div",
                    { className: "geo-head" },
                    domEl("strong", { className: "geo-name", text: region.region_name }),
                    domEl("span", { className: "geo-share", text: `${region.share_percent}% выборки` }),
                ),
                domEl("span", { className: "geo-price mono", text: formatPrice(region.median) }),
                domEl(
                    "div",
                    { className: "geo-meta" },
                    domEl("span", { text: `${region.count} с ценой` }),
                    domEl("span", { text: `ср. ${formatPrice(region.mean)}` }),
                ),
            );
            elements.geographyGrid.appendChild(card);
        }

        elements.geographySection.hidden = false;
        });
    }

    /* ===== Recent Searches ===== */

    function renderRecentSearches() {
        return safeRender('renderRecentSearches', () => {
            if (!elements.recentSection || !elements.recentList) return;
            const searches = state.search.recentSearches || [];
            if (!searches.length) {
                elements.recentSection.hidden = true;
                domClear(elements.recentList);
                return;
            }
            elements.recentSection.hidden = false;
            domClear(elements.recentList);
            const fragment = document.createDocumentFragment();
            for (const query of searches) {
                const button = document.createElement("button");
                button.className = "recent-chip";
                button.type = "button";
                button.dataset.recentQuery = query;
                button.textContent = query;
                fragment.appendChild(button);
            }
            elements.recentList.appendChild(fragment);
        });
    }

    return {
        renderTrackingHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderHistoryRangeButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderFilterDropdown,
    };
}


;
/**
 * render_modals.js — Detail modal, expenses modal, edit tracker modal.
 */

function createRenderModals(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        formatDate,
        trapFocus,
        safeUrl: safeUrl,
        optimizedImage,
        safeRender: safeRender,
        escapeHtml,
    } = context;

    function buildDetailField(label, value) {
        const item = document.createElement("div");
        item.className = "detail-field";
        const labelEl = document.createElement("span");
        labelEl.className = "detail-field-label";
        labelEl.textContent = label;
        const valueEl = document.createElement("span");
        valueEl.className = "detail-field-value";
        valueEl.textContent = value;
        item.append(labelEl, valueEl);
        return item;
    }

    /* ===== Detail Modal ===== */

    function renderDetailModal() {
        return safeRender('renderDetailModal', () => {
            if (!state.detail.data) {
            // Don't toggle .hidden here — closeDetailModal animates it
            // out and a stray render call would otherwise abort that
            // transition. The modal starts hidden in HTML and is only
            // opened through an explicit openModalAnimated call below.
            return;
        }

        const detail = state.detail.data;
        const images = detail.images || [];
        const hasImages = images.length > 0;
        const currentImage = hasImages ? images[state.detail.imageIndex] || images[0] : null;

        elements.detailTitle.textContent = detail.title || "Объявление";
        elements.detailPrice.textContent = formatPrice(detail.price, detail.price_type);
        elements.detailLink.href = safeUrl(detail.link) || "#";

        const aiState = state.detail.ai || {};
        if (elements.detailAiBlock && elements.detailAiContent) {
            elements.detailAiBlock.hidden = true;
            domClear(elements.detailAiContent);
        }

        elements.detailDescription.textContent = detail.description || "";
        elements.detailDescription.hidden = !detail.description;

        // Flip estimates hidden — resale info now shown in AI analysis
        elements.detailProfitBlock.hidden = true;

        domClear(elements.detailLiquidity);
        if (detail.liquidity) {
            const item = buildDetailField(
                detail.liquidity.label,
                `${Math.round(detail.liquidity.score)} • ${(detail.liquidity.reasons || []).map(String).join(" · ")}`
            );
            elements.detailLiquidity.appendChild(item);
        }
        elements.detailLiquidityBlock.hidden = !detail.liquidity;

        // Build the price-vs-market badge with the reference label so the user
        // sees WHAT the deviation is measured against (per-category fallback
        // is computed on the backend when the category has ≥3 ads).
        let priceVsMarketLabel = detail.fair_price_label || "";
        if (priceVsMarketLabel && detail.price_reference_scope === "category"
            && detail.price_reference_label) {
            priceVsMarketLabel += ` · ${detail.price_reference_label}`;
        } else if (priceVsMarketLabel && detail.price_reference_scope === "query") {
            priceVsMarketLabel += " · по запросу";
        }

        const metaItems = [
            detail.category,
            detail.condition ? formatCondition(detail.condition) : "",
            detail.seller_type ? formatSeller(detail.seller_type) : "",
            detail.list_time ? formatDate(detail.list_time) : "",
            priceVsMarketLabel,
            formatDelta(detail.price_vs_median),
        ].filter(Boolean);
        domClear(elements.detailMeta);
        for (const item of metaItems) {
            const pill = document.createElement("span");
            pill.className = "detail-pill";
            pill.textContent = item;
            elements.detailMeta.appendChild(pill);
        }

        elements.detailMainImage.hidden = !hasImages;
        elements.detailNoImage.hidden = hasImages;

        // Update gallery aria-label with current image index
        if (elements.detailMedia) {
            const total = images.length || 1;
            const current = images.length ? (state.detail.imageIndex + 1) : 1;
            elements.detailMedia.setAttribute("aria-label", `Фото объявления ${current} из ${total}`);
        }

        const optimizeWith = (url, width) => {
            const validated = safeUrl(url);
            if (!validated) return "";
            return typeof optimizedImage === "function"
                ? optimizedImage(validated, { width })
                : validated;
        };

        if (currentImage) {
            const safeImage = optimizeWith(currentImage, 800);
            if (safeImage) {
                elements.detailMainImage.src = safeImage;
                elements.detailMainImage.alt = detail.title || "Фото объявления";
            } else {
                elements.detailMainImage.removeAttribute("src");
            }
        } else {
            elements.detailMainImage.removeAttribute("src");
        }

        domClear(elements.detailThumbs);
        for (const [index, image] of images.entries()) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `detail-thumb${state.detail.imageIndex === index ? " active" : ""}`;
            const img = document.createElement("img");
            img.src = optimizeWith(image, 120);
            img.alt = "";
            button.appendChild(img);
            button.addEventListener("click", () => {
                state.detail.imageIndex = index;
                renderDetailModal();
            });
            elements.detailThumbs.appendChild(button);
        }

        domClear(elements.detailParams);
        const params = detail.parameters || [];
        for (const field of params) {
            elements.detailParams.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailParamsBlock.hidden = params.length === 0;

        domClear(elements.detailSeller);
        const sellerFields = detail.seller_fields || [];
        for (const field of sellerFields) {
            elements.detailSeller.appendChild(buildDetailField(field.label, field.value));
        }
        elements.detailSellerBlock.hidden = sellerFields.length === 0;

        // Hide "Следить" button if item is already in watchlist
        if (elements.detailAddWatchlistButton) {
            elements.detailAddWatchlistButton.hidden = state.detail.fromWatchlist || false;
        }

        // Reset scroll position to top when modal opens
        const scrollContainer = elements.detailModal?.querySelector(".detail-sheet-content");
        if (scrollContainer) {
            scrollContainer.scrollTop = 0;
        }

        // Pinch-zoom: lazy-init once per session and stash the controller
        // on the image so closeDetailModal / navigateDetailImage can
        // reset the transform when the user moves on to a different lot
        // or photo. The helper itself is a no-op on desktop because the
        // touch events never fire — but it costs nothing to keep wired.
        if (
            elements.detailMainImage &&
            !elements.detailMainImage._pinchController &&
            typeof attachPinchZoom === "function"
        ) {
            elements.detailMainImage._pinchController = attachPinchZoom(
                elements.detailMainImage,
            );
        }
        if (elements.detailMainImage?._pinchController) {
            elements.detailMainImage._pinchController.reset(false);
        }

        if (elements.detailModal.hidden) {
            openModalAnimated(elements.detailModal);
        }
        });
    }

    function closeDetailModal() {
        // FE-C4: drop the in-flight detail/AI fetch (if any) before
        // we tear the modal down. Otherwise a slow /listing-detail
        // call can resolve into already-cleared state.detail and
        // either flash the modal back open or trip a "Cannot read
        // properties of null" in renderDetailModal.
        if (typeof actions.abortDetailRequest === "function") {
            actions.abortDetailRequest();
        }
        if (state.misc.modalCleanup) {
            state.misc.modalCleanup();
            state.misc.modalCleanup = null;
        }
        // Drop any pinch-zoom transform so the next lot opens at 1×
        // even if the previous viewer left the photo magnified.
        if (elements.detailMainImage?._pinchController) {
            elements.detailMainImage._pinchController.reset(false);
        }
        state.detail.data = null;
        state.detail.imageIndex = 0;
        state.detail.fromWatchlist = false;
        state.detail.ai = {
            adId: null,
            loading: false,
            result: null,
            error: "",
            source: "",
        };
        // Animate close, THEN renderDetailModal will see state.detail = null
        // and the modal will already be hidden by the helper.
        closeModalAnimated(elements.detailModal);
    }

    /* ===== Expenses Modal ===== */

    function renderExpensesModal() {
        return safeRender('renderExpensesModal', () => {
            if (!elements.expensesModal) return;
            domClear(elements.expensesList);

        // Show loading state
        if (state.expenses.loading) {
            const loader = document.createElement("div");
            loader.className = "expenses-loading";
            for (let i = 0; i < 3; i++) {
                const row = document.createElement("div");
                row.className = "skeleton-expense-row";
                loader.appendChild(row);
            }
            elements.expensesList.appendChild(loader);
            return;
        }

        if (!state.expenses.items.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Расходов пока нет.";
            elements.expensesList.appendChild(note);
            return;
        }

        let totalExpenses = 0;
        for (const expense of state.expenses.items) {
            const amount = Number(expense.amount_byn || 0);
            totalExpenses += amount;
            const row = document.createElement("div");
            row.className = "expense-row";
            const typeLabels = { delivery: "🚚 Доставка", repair: "🔧 Ремонт", other: "📦 Другое" };
            const main = document.createElement("div");
            main.className = "expense-main";
            const type = document.createElement("span");
            type.className = "expense-type";
            type.textContent = typeLabels[expense.expense_type] || expense.expense_type;
            const meta = document.createElement("span");
            meta.className = "expense-meta";
            meta.textContent = expense.notes || "";
            main.append(type, meta);
            const amountEl = document.createElement("span");
            amountEl.className = "expense-amount mono";
            amountEl.textContent = `-${Math.round(amount)} BYN`;
            const deleteBtn = document.createElement("button");
            deleteBtn.className = "expense-delete-btn";
            deleteBtn.type = "button";
            deleteBtn.setAttribute("aria-label", "Удалить расход");
            deleteBtn.textContent = "✕";
            deleteBtn.addEventListener("click", () => {
                void actions.deleteExpense(state.expenses.currentLeadId, expense.id);
            });
            row.append(main, amountEl, deleteBtn);
            elements.expensesList.appendChild(row);
        }

        // Show total
        const totalRow = document.createElement("div");
        totalRow.className = "expense-total";
        const totalLabel = document.createElement("span");
        totalLabel.className = "expense-total-label";
        totalLabel.textContent = "Итого расходов";
        const totalValue = document.createElement("span");
        totalValue.className = "expense-total-value mono";
        totalValue.textContent = `-${Math.round(totalExpenses)} BYN`;
        totalRow.append(totalLabel, totalValue);
        elements.expensesList.prepend(totalRow);
        });
    }

    function openExpensesModal(leadId, leadTitle) {
        state.expenses.currentLeadId = leadId;
        state.expenses.items = [];
        if (elements.expensesSubtitle) {
            elements.expensesSubtitle.textContent = leadTitle;
            elements.expensesSubtitle.hidden = false;
        }
        if (elements.expensesModal) {
            if (state.misc.modalCleanup) {
                state.misc.modalCleanup();
                state.misc.modalCleanup = null;
            }
            // FE-H4/UX-H1: openModalAnimated() already installs a
            // focus trap and stores the cleanup on the modal element
            // (_focusTrapCleanup), which closeModalAnimated() will run
            // on exit. Adding a second trap here caused two keydown
            // listeners to fight over Tab, and the outer cleanup never
            // ran because closeExpensesModal relies on closeModalAnimated.
            openModalAnimated(elements.expensesModal);
        }
        void actions.loadExpenses(leadId);
    }

    function closeExpensesModal() {
        if (state.misc.modalCleanup) {
            state.misc.modalCleanup();
            state.misc.modalCleanup = null;
        }
        if (elements.expensesModal) {
            closeModalAnimated(elements.expensesModal);
        }
        state.expenses.currentLeadId = null;
        state.expenses.items = [];
        if (elements.expenseTypeSelect) elements.expenseTypeSelect.value = "delivery";
        if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
        if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
    }

    return {
        renderDetailModal,
        closeDetailModal,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
    };
}


;
/**
 * render_charts.js — Price chart, history chart, profit dashboard, history deals.
 *
 * Chart.js is loaded lazily on first chart paint instead of being a
 * blocking <script> tag in the document head. That's ~80 KB of JS
 * that the watchlist / trackers / leads users never need.
 */
/* global Chart */

const CHART_JS_URL =
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js";
const CHART_JS_INTEGRITY =
    "sha384-vsrfeLOOY6KuIYKDlmVH5UiBmgIdB1oEf7p01YgWHuqmOHfZr374+odEv96n9tNC";

let _chartLibPromise = null;

function ensureChartLib() {
    if (typeof window.Chart === "function") return Promise.resolve();
    if (_chartLibPromise) return _chartLibPromise;
    _chartLibPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = CHART_JS_URL;
        script.integrity = CHART_JS_INTEGRITY;
        script.crossOrigin = "anonymous";
        script.async = true;
        script.onload = () => resolve();
        script.onerror = () => {
            // Reset so a later retry (e.g. user opens overview again
            // after a flaky network) can try again from scratch.
            _chartLibPromise = null;
            reject(new Error("Chart.js failed to load"));
        };
        document.head.appendChild(script);
    });
    return _chartLibPromise;
}

function createRenderCharts(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        escapeHtml: escapeHtml,
        safeUrl: safeUrl,
        optimizedImage,
        safeRender: safeRender,
    } = context;

    /* ===== Chart lifecycle ===== */

    function destroyChart() {
        if (state.charts.distribution) {
            state.charts.distribution.destroy();
            state.charts.distribution = null;
        }
    }

    function destroyHistoryChart() {
        if (state.charts.history) {
            state.charts.history.destroy();
            state.charts.history = null;
        }
    }

    /* ===== Price Distribution Chart ===== */

    function renderChart(stats) {
        if (stats) {
            state.misc.stats = stats;
        }
        if (
            !state.misc.stats ||
            state.misc.stats.count === 0 ||
            elements.chartSection.hidden ||
            !state.panels.distribution
        ) {
            destroyChart();
            return;
        }

        const canvas = elements.priceChartCanvas;
        if (!canvas) return;

        // Lazy-load Chart.js on demand. If the library hasn't arrived
        // yet, paint a quiet skeleton (the existing aria-busy state on
        // .chart-section is enough — we just bail and re-enter once
        // the lib resolves).
        if (typeof window.Chart !== "function") {
            ensureChartLib()
                .then(() => renderChart())
                .catch(() => {
                    /* Network failure surfaces via the regular error
                       toast on the next render attempt. */
                });
            return;
        }

        destroyChart();

        const isDark = document.documentElement.getAttribute("data-theme") !== "light";
        const muted = isDark ? "rgba(136,128,120,0.6)" : "rgba(114,105,94,0.6)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";
        const accentColor = isDark ? "#3B82F6" : "#2563EB";
        const values = [
            state.misc.stats.min,
            state.misc.stats.q1,
            state.misc.stats.median,
            state.misc.stats.q3,
            state.misc.stats.max,
        ];
        const alphas = [0.22, 0.4, 0.9, 0.4, 0.22];

        // Canvas is marked aria-hidden; the wrapper div carries the accessible label
        canvas.setAttribute("aria-hidden", "true");

        state.charts.distribution = new Chart(canvas, {
            type: "bar",
            data: {
                labels: ["Мин", "Q1", "Медиана", "Q3", "Макс"],
                datasets: [
                    {
                        data: values,
                        backgroundColor: alphas.map((alpha) => `rgba(59,146,246,${alpha})`),
                        borderColor: alphas.map((alpha) => `rgba(59,146,246,${Math.min(alpha + 0.3, 1)})`),
                        borderWidth: 1.5,
                        borderRadius: 5,
                        borderSkipped: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: tooltipBackground,
                        titleColor: tooltipText,
                        bodyColor: accentColor,
                        borderColor: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
                        borderWidth: 1,
                        padding: 10,
                        callbacks: {
                            label(context) {
                                return ` ${formatPrice(context.parsed.y)}`;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 10 },
                        },
                    },
                    y: {
                        grid: { color: grid },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 4,
                            callback(value) {
                                return formatPrice(value);
                            },
                        },
                    },
                },
            },
        });
    }

    /* ===== History ===== */

    function renderHistory() {
        return safeRender('renderHistory', () => {
            const hasHistory = state.charts.historyData.length > 0;
        elements.historySection.hidden = !state.search.query;
        if (context._hooks?.renderHistoryRangeButtons) context._hooks.renderHistoryRangeButtons();
        elements.historyEmpty.hidden = hasHistory;
        elements.historySummary.hidden = !hasHistory;
        domClear(elements.historySummary);
        if (!state.search.query) {
            destroyHistoryChart();
            return;
        }
        if (!hasHistory || !state.panels.history) {
            destroyHistoryChart();
            return;
        }
        renderHistoryChart();
        });
    }

    function renderHistoryChart() {
        return safeRender('renderHistoryChart', () => {
        const canvas = elements.historyChartCanvas;
        if (!canvas || !state.charts.historyData.length) {
            destroyHistoryChart();
            return;
        }

        // Lazy-load Chart.js — mirrors the guard in renderChart().
        if (typeof window.Chart !== "function") {
            ensureChartLib()
                .then(() => renderHistoryChart())
                .catch(() => {
                    /* error surfaced via toast on next attempt */
                });
            return;
        }

        const firstPoint = state.charts.historyData[0];
        const lastPoint = state.charts.historyData[state.charts.historyData.length - 1];
        const delta = firstPoint && lastPoint && firstPoint.median
            ? ((lastPoint.median - firstPoint.median) / firstPoint.median) * 100
            : 0;
        const summaryItems = [
            {
                label: "Сейчас",
                value: formatPrice(lastPoint?.median),
                meta: `${state.charts.historyData.length} точек`,
            },
            {
                label: "Тренд",
                value: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`,
                meta: `${state.misc.historyDays} дней`,
            },
            {
                label: "Диапазон",
                value: `${formatPrice(state.charts.historyData.reduce((min, p) => Math.min(min, p.median), Infinity))} - ${formatPrice(state.charts.historyData.reduce((max, p) => Math.max(max, p.median), -Infinity))}`,
                meta: "по медиане",
            },
        ];
        elements.historySummary.replaceChildren(
            domFragment(
                summaryItems.map((item) => domEl(
                    "div",
                    { className: "history-summary-card" },
                    domEl("span", { className: "history-summary-label", text: item.label }),
                    domEl("strong", { className: "history-summary-value mono", text: item.value }),
                    domEl("span", { className: "history-summary-meta", text: item.meta }),
                ))
            )
        );
        elements.historySummary.hidden = false;

        destroyHistoryChart();
        const isDark = document.documentElement.getAttribute("data-theme") !== "light";
        const lineColor = isDark ? "#3B82F6" : "#2563EB";
        const fillColor = isDark ? "rgba(59,130,246,0.12)" : "rgba(37,99,235,0.12)";
        const muted = isDark ? "rgba(136,128,120,0.75)" : "rgba(114,105,94,0.75)";
        const grid = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.05)";
        const tooltipBackground = isDark ? "#1A1A1D" : "#FFFFFF";
        const tooltipText = isDark ? "#F2EFE8" : "#1A1917";

        // Canvas is marked aria-hidden; the wrapper div carries the accessible label
        canvas.setAttribute("aria-hidden", "true");

        state.charts.history = new Chart(canvas, {
            type: "line",
            data: {
                labels: state.charts.historyData.map((point) => formatDate(point.snapshot_at) || ""),
                datasets: [
                    {
                        label: "Медиана",
                        data: state.charts.historyData.map((point) => point.median),
                        borderColor: lineColor,
                        backgroundColor: fillColor,
                        fill: true,
                        tension: 0.28,
                        pointRadius: 2.5,
                        pointHoverRadius: 4,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: tooltipBackground,
                        titleColor: tooltipText,
                        bodyColor: lineColor,
                        borderColor: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
                        borderWidth: 1,
                        padding: 10,
                        callbacks: {
                            label(context) {
                                return ` ${formatPrice(context.parsed.y)}`;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 6,
                        },
                    },
                    y: {
                        grid: { color: grid },
                        border: { display: false },
                        ticks: {
                            color: muted,
                            font: { family: "'JetBrains Mono'", size: 9 },
                            maxTicksLimit: 4,
                            callback(value) {
                                return formatPrice(value);
                            },
                        },
                    },
                },
            },
        });
        });
    }

    /* ===== Profit Dashboard ===== */

    function _renderDashboardCards(dashboard) {
        // Hero cards: revenue, profit, ROI, win-rate, days-to-close.
        // Each card is structurally identical (label/value/sub) so the
        // CSS scaling is consistent and the user can scan top-to-bottom.
        const profit = Number(dashboard.total_profit_byn || 0);
        const revenue = Number(dashboard.total_revenue_byn || 0);
        const roi = Number(dashboard.average_roi_percent || 0);
        const winRate = Number(dashboard.win_rate_percent || 0);
        const avgDays = Number(dashboard.average_days_to_close || 0);
        const sold = Number(dashboard.sold_leads || 0);
        const pursued = Number(dashboard.pursued_leads || 0);
        const period = Number(dashboard.period_days || 90);
        const expenses = Number(dashboard.total_expenses_byn || 0);

        return [
            {
                label: "Прибыль",
                value: `${profit >= 0 ? "+" : ""}${Math.round(profit)} BYN`,
                sub: `выручка ${Math.round(revenue)} · расходы ${Math.round(expenses)}`,
                className: profit >= 0 ? "is-accent" : "is-warning",
            },
            {
                label: "ROI",
                value: `${roi >= 0 ? "+" : ""}${roi.toFixed(1)}%`,
                sub: `средний по ${sold} продажам`,
                className: roi >= 0 ? "is-accent" : "is-warning",
            },
            {
                label: "Win rate",
                value: `${winRate.toFixed(1)}%`,
                sub: `${sold} из ${pursued} в работе`,
                className: winRate >= 50 ? "is-accent" : winRate >= 25 ? "" : "is-warning",
            },
            {
                label: "Цикл сделки",
                value: avgDays > 0 ? `${avgDays.toFixed(1)} дн` : "—",
                sub: `медиана ${(dashboard.median_days_to_close || 0).toFixed(1)} дн · ${period} дн период`,
                className: "",
            },
        ];
    }

    function renderProfitDashboard() {
        return safeRender('renderProfitDashboard', () => {
        if (!elements.profitCards) return;
        domClear(elements.profitCards);

        if (!hasTelegramInitData()) {
            elements.profitDashboardSection.hidden = true;
            return;
        }

        elements.profitDashboardSection.hidden = false;

        // Reflect the active period chip from state (in case the user
        // toggled it between renders without clicking).
        for (const button of elements.analyticsPeriodButtons || []) {
            const days = Number(button.dataset.analyticsPeriod || 0);
            button.classList.toggle("is-active", days === Number(state.analytics.periodDays));
        }

        const dashboard = state.analytics.dashboard;
        if (!dashboard) {
            // Loading or no data yet — render placeholder cards so the
            // layout doesn't jump when the first response lands.
            const placeholderCards = [
                { label: "Прибыль", value: "…", sub: state.analytics.loading ? "загружаю" : "нет данных", className: "" },
                { label: "ROI", value: "…", sub: state.analytics.loading ? "загружаю" : "нет данных", className: "" },
                { label: "Win rate", value: "…", sub: state.analytics.loading ? "загружаю" : "нет данных", className: "" },
                { label: "Цикл сделки", value: "…", sub: state.analytics.loading ? "загружаю" : "нет данных", className: "" },
            ];
            for (const card of placeholderCards) {
                elements.profitCards.appendChild(
                    domEl(
                        "div",
                        { className: `profit-card ${card.className}`.trim() },
                        domEl("span", { className: "profit-card-label", text: card.label }),
                        domEl("span", { className: "profit-card-value mono", text: card.value }),
                        domEl("span", { className: "profit-card-sub", text: card.sub }),
                    ),
                );
            }
            return;
        }

        const cards = _renderDashboardCards(dashboard);
        for (const card of cards) {
            elements.profitCards.appendChild(
                domEl(
                    "div",
                    { className: `profit-card ${card.className}`.trim() },
                    domEl("span", { className: "profit-card-label", text: card.label }),
                    domEl("span", { className: "profit-card-value mono", text: card.value }),
                    domEl("span", { className: "profit-card-sub", text: card.sub }),
                ),
            );
        }
        });
    }

    /* ===== History Deals ===== */

    function renderHistoryDeals() {
        return safeRender('renderHistoryDeals', () => {
        if (!elements.historyDealsList) return;
        domClear(elements.historyDealsList);

        const closedLeads = state.leads.items.filter((l) => l.status === "closed");

        if (elements.historyDealsCount) {
            elements.historyDealsCount.textContent = String(closedLeads.length);
        }

        if (!closedLeads.length) {
            const note = document.createElement("p");
            note.className = "tracker-empty";
            note.textContent = "Закрытых сделок пока нет. Завершите текущие сделки, чтобы они появились здесь.";
            elements.historyDealsList.appendChild(note);
            return;
        }

        const sortedLeads = [...closedLeads].sort((a, b) => {
            return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
        });

        for (const lead of sortedLeads) {
            const card = document.createElement("div");
            card.className = "history-deal-card";
            card.dataset.leadId = lead.id;

            const buyPriceBynRaw = lead.buy_price_byn ? Number(lead.buy_price_byn) : null;
            const soldPriceBynRaw = lead.sold_price_byn ? Number(lead.sold_price_byn) : null;

            const buyPrice = buyPriceBynRaw ? Math.round(buyPriceBynRaw) : "?";
            const soldPrice = soldPriceBynRaw ? Math.round(soldPriceBynRaw) : "?";
            const profit = soldPriceBynRaw && buyPriceBynRaw ? soldPriceBynRaw - buyPriceBynRaw : null;
            const profitSign = profit && profit >= 0 ? "+" : "";
            const profitClass = profit && profit >= 0 ? "history-profit-positive" : "history-profit-negative";

            const dateStr = lead.updated_at ? new Date(lead.updated_at).toLocaleDateString("ru-RU") : "";

            const thumbSrc = (() => {
                const validated = safeUrl(lead.thumbnail);
                if (!validated) return "";
                return typeof optimizedImage === "function"
                    ? optimizedImage(validated, { width: 160 })
                    : validated;
            })();
            const thumbNode = thumbSrc
                ? domEl("img", {
                    className: "history-deal-thumb",
                    attrs: { src: thumbSrc, alt: lead.title || "Сделка", loading: "lazy" },
                })
                : domEl("div", { className: "history-deal-thumb-placeholder", attrs: { "aria-hidden": "true" }, text: "📦" });

            card.appendChild(
                domFragment(
                    thumbNode,
                    domEl(
                        "div",
                        { className: "history-deal-info" },
                        domEl("strong", { className: "history-deal-title", text: lead.title }),
                        domEl(
                            "div",
                            { className: "history-deal-meta" },
                            domEl("span", { className: "history-deal-price", text: `${buyPrice} → ${soldPrice} BYN` }),
                            domEl("span", { className: "history-deal-date", text: dateStr }),
                        ),
                    ),
                    domEl(
                        "div",
                        { className: "history-deal-profit-wrap" },
                        domEl(
                            "div",
                            {
                                className: `history-deal-profit ${profitClass}`.trim(),
                                text: profit !== null ? `${profitSign}${Math.round(profit)}` : "—",
                            },
                        ),
                        domEl("span", { className: "history-deal-profit-currency", text: "BYN" }),
                    ),
                    domEl("button", {
                        className: "history-deal-delete",
                        type: "button",
                        text: "✕",
                        dataset: { role: "delete-history-deal" },
                        attrs: { "aria-label": "Удалить из истории" },
                    }),
                )
            );

            card.querySelector('[data-role="delete-history-deal"]')?.addEventListener("click", () => {
                void actions.deleteHistoryDeal(lead.id);
            });

            elements.historyDealsList.appendChild(card);
        }
        });
    }

    return {
        destroyChart,
        destroyHistoryChart,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderProfitDashboard,
        renderHistoryDeals,
    };
}


;
/**
 * render_trackers.js — Tracker status, tracker events, tracker filters,
 * tracker management UI, watchlist filters.
 */

function createRenderTrackers(context) {
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        escapeHtml: escapeHtml,
        safeUrl: safeUrl,
        optimizedImage,
        safeRender: safeRender,
    } = context;

    // FE-05 / Wave 29: emoji-prefixed labels need a tiny shim so screen
    // readers don't pronounce the emoji glyph (Unicode names like
    // "BACKHAND INDEX POINTING DOWN" are unreadable as price-drop
    // markers). Wrap the emoji in an aria-hidden span and let the
    // following text node carry the meaningful label.
    function emojiSpan(emoji) {
        const span = document.createElement("span");
        span.setAttribute("aria-hidden", "true");
        span.textContent = emoji;
        return span;
    }

    function emojiLabel(className, emoji, text) {
        // Returns a span whose visible content is "📍 Минск" but whose
        // accessible name reads as plain "Минск". Used for chip-style
        // labels in tracker event cards.
        return domEl("span", { className }, emojiSpan(emoji), ` ${text}`);
    }

    // FE-08 / Wave 29: build SVG icons via createElementNS instead of
    // ``innerHTML = "<svg...>"``. The previous code worked because
    // every SVG payload was a hardcoded source-code constant, but a
    // future refactor that interpolates ANY runtime value into one
    // of these strings would have re-introduced an XSS vector. The
    // descriptor form below is forced to go through DOM APIs that
    // can never execute injected script regardless of input.
    const SVG_NS = "http://www.w3.org/2000/svg";

    function buildSvgIcon(viewBox, attrs, children) {
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("viewBox", viewBox);
        svg.setAttribute("aria-hidden", "true");
        for (const [k, v] of Object.entries(attrs || {})) svg.setAttribute(k, String(v));
        for (const child of children) {
            const el = document.createElementNS(SVG_NS, child.tag);
            for (const [k, v] of Object.entries(child)) {
                if (k === "tag") continue;
                el.setAttribute(k, String(v));
            }
            svg.appendChild(el);
        }
        return svg;
    }

    // Common attribute presets for the action / search icons.
    const _STROKE_ATTRS = {
        fill: "none",
        stroke: "currentColor",
        "stroke-width": "2",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
    };

    function iconPlay() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, fill: "currentColor" },
            [{ tag: "path", d: "M8 5v14l11-7z" }]);
    }
    function iconPause() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, fill: "currentColor" },
            [{ tag: "path", d: "M6 4h4v16H6zM14 4h4v16h-4z" }]);
    }
    function iconEdit() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, ..._STROKE_ATTRS },
            [
                { tag: "path", d: "M12 20h9" },
                { tag: "path", d: "M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4z" },
            ]);
    }
    function iconDelete() {
        return buildSvgIcon("0 0 24 24",
            { width: 14, height: 14, ..._STROKE_ATTRS },
            [
                { tag: "polyline", points: "3 6 5 6 21 6" },
                { tag: "path", d: "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" },
                { tag: "path", d: "M10 11v6M14 11v6" },
                { tag: "path", d: "M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" },
            ]);
    }
    function iconSearch() {
        return buildSvgIcon("0 0 24 24",
            { width: 16, height: 16, ..._STROKE_ATTRS },
            [
                { tag: "circle", cx: "11", cy: "11", r: "7" },
                { tag: "line", x1: "21", y1: "21", x2: "16.65", y2: "16.65" },
            ]);
    }

    /* ===== Tracker Status ===== */

    function renderTrackerStatus() {
        return safeRender('renderTrackerStatus', () => {
            const message = typeof state.trackers.status === "string" ? state.trackers.status.trim() : "";
        if (!message) {
            elements.trackerStatus.hidden = true;
            elements.trackerStatus.textContent = "";
            elements.trackerStatus.className = "tracker-status";
            return;
        }

        elements.trackerStatus.textContent = message;
        elements.trackerStatus.className = `tracker-status is-visible ${state.trackers.statusKind}`;
        elements.trackerStatus.hidden = false;
        });
    }

    /* ===== Watchlist Filters ===== */

    function renderWatchlistFilters() {
        for (const button of elements.watchlistFilterButtons || []) {
            button.classList.toggle("active", button.dataset.watchFilter === state.watchlist.filter);
        }
    }

    /* ===== Tracker Cards ===== */

    /**
     * Format a relative time from an ISO date string for display.
     */
    function formatLastEventTime(isoDate) {
        if (!isoDate) return "нет";
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return "нет";
        const diffMs = Math.max(0, Date.now() - date.getTime());
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return "только что";
        if (diffMin < 60) return `${diffMin} мин`;
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) return `${diffHr} ч`;
        const diffDays = Math.floor(diffHr / 24);
        return `${diffDays} д`;
    }

    function renderTrackers() {
        return safeRender('renderTrackers', () => {
        domClear(elements.trackersList);

        const buildEmpty = context.buildEmptyState;
        if (!hasTelegramInitData()) {
            if (typeof buildEmpty === "function") {
                elements.trackersList.appendChild(
                    buildEmpty({
                        icon: "trackers",
                        title: "Открывайте Mini App в Telegram",
                        hint: "Автопоиск работает с Telegram-аккаунтом — только так трекер сможет прислать уведомление о новых лотах.",
                    })
                );
            } else {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = "Откройте Mini App внутри Telegram, чтобы управлять трекерами.";
                elements.trackersList.appendChild(note);
            }
            return;
        }

        // Show skeleton cards while loading
        if (state.trackers._loading) {
            for (let i = 0; i < 3; i++) {
                const skel = document.createElement("div");
                skel.className = "skeleton-card";
                skel.setAttribute("aria-hidden", "true");
                // FE-08: build children via DOM API instead of innerHTML.
                for (const width of ["60%", "40%", "30%"]) {
                    const bar = document.createElement("div");
                    bar.className = "skel-bar";
                    bar.style.width = width;
                    skel.appendChild(bar);
                }
                elements.trackersList.appendChild(skel);
            }
            return;
        }

        if (!state.trackers.items.length) {
            if (typeof buildEmpty === "function") {
                elements.trackersList.appendChild(
                    buildEmpty({
                        icon: "trackers",
                        title: "Создайте первый автопоиск",
                        hint: "Сохраните любой запрос как трекер — и Telegram пришлёт уведомление, когда появятся новые объявления или цена пойдёт вниз.",
                        actionLabel: "Создать автопоиск",
                        onAction: () => {
                            const input = document.querySelector(".tracker-input");
                            if (input) input.focus();
                        },
                    })
                );
            } else {
                const note = document.createElement("p");
                note.className = "tracker-empty";
                note.textContent = "Активных трекеров пока нет.";
                elements.trackersList.appendChild(note);
            }
            return;
        }

        for (const tracker of state.trackers.items) {
            let lastCheckedLabel = "";
            if (tracker.last_checked_at) {
                const checkedDate = new Date(tracker.last_checked_at);
                if (!isNaN(checkedDate.getTime())) {
                    const diffMs = Math.max(0, Date.now() - checkedDate.getTime());
                    const diffMin = Math.floor(diffMs / 60000);
                    if (diffMin < 1) {
                        lastCheckedLabel = "проверено только что";
                    } else if (diffMin < 60) {
                        lastCheckedLabel = `${diffMin} мин назад`;
                    } else {
                        const diffHr = Math.floor(diffMin / 60);
                        lastCheckedLabel = `${diffHr} ч назад`;
                    }
                }
            }

            const trackerFilters = domEl(
                "div",
                { className: "tracker-filters" },
                domEl("span", { className: "tracker-filter-tag", text: `каждые ${tracker.interval_min} мин` }),
            );
            if (tracker.strict_mode) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: "строгий" }));
            if (tracker.min_discount_percent) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: `от -${Math.round(tracker.min_discount_percent)}%` }));
            if (tracker.max_price_byn) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: `до ${Math.round(tracker.max_price_byn)} BYN` }));
            if (tracker.seller_type === "Частное лицо") trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: "частники" }));
            if (tracker.condition) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: tracker.condition }));
            if (tracker.region_name) trackerFilters.appendChild(domEl("span", { className: "tracker-filter-tag", text: tracker.region_name }));

            const buildStat = (label, value, className) => domEl(
                "div",
                { className: "tracker-stat" },
                domEl("span", { className: "tracker-stat-label", text: label }),
                domEl("span", { className: `tracker-stat-value${className ? ` ${className}` : ""}`.trim(), text: value }),
            );

            const actionRole = tracker.paused ? "resume" : "pause";
            const actionLabel = tracker.paused ? "Возобновить" : "Пауза";
            // FE-08: SVG icons constructed via createElementNS (see
            // helpers at top of module). Picked at render time so the
            // button gets a clean monochrome glyph instead of a
            // platform-specific emoji.
            const actionIconNode = tracker.paused ? iconPlay() : iconPause();

            const buildIconButton = (className, role, label, iconNode) => {
                const btn = domEl(
                    "button",
                    { className, type: "button", dataset: { role } },
                );
                const icon = document.createElement("span");
                icon.className = "tracker-action-icon";
                icon.appendChild(iconNode);
                btn.append(icon, document.createTextNode(label));
                return btn;
            };
            const card = domEl(
                "div",
                { className: `tracker-card-enhanced${tracker.paused ? " paused" : ""}` },
                domEl(
                    "div",
                    { className: "tracker-header" },
                    (() => {
                        const iconWrap = document.createElement("div");
                        iconWrap.className = "tracker-icon";
                        // FE-08: see iconSearch() at top of module.
                        iconWrap.appendChild(iconSearch());
                        return iconWrap;
                    })(),
                    domEl(
                        "div",
                        { className: "tracker-title-wrap" },
                        domEl("h4", { className: "tracker-query-title", text: tracker.query }),
                    ),
                ),
                trackerFilters,
                domEl(
                    "div",
                    { className: "tracker-stats" },
                    buildStat("События", tracker.event_count || 0, "highlight"),
                    buildStat("В среднем", `${tracker.avg_events_per_day || 0}/день`),
                    buildStat("Посл. событие", formatLastEventTime(tracker.last_event_at)),
                ),
                lastCheckedLabel ? emojiLabel("tracker-last-checked", "🕐", lastCheckedLabel) : null,
                domEl(
                    "div",
                    { className: "tracker-card-actions" },
                    buildIconButton("ghost-btn small tracker-action-btn", actionRole, actionLabel, actionIconNode),
                    buildIconButton("ghost-btn small tracker-action-btn", "edit", "Изменить", iconEdit()),
                    buildIconButton("ghost-btn small tracker-action-btn danger", "delete", "Удалить", iconDelete()),
                ),
            );

            card.querySelector('[data-role="pause"]')?.addEventListener("click", () => {
                void actions.pauseTracker(tracker.id);
            });
            card.querySelector('[data-role="resume"]')?.addEventListener("click", () => {
                void actions.resumeTracker(tracker.id);
            });
            card.querySelector('[data-role="edit"]')?.addEventListener("click", () => {
                actions.openEditTracker(tracker.id);
            });
            card.querySelector('[data-role="view-events"]')?.addEventListener("click", () => {
                state.trackers.events = state.trackers.events || [];
                state.trackers.eventFilterTrackerId = tracker.id;
                if (context._hooks?.renderTrackerEvents) context._hooks.renderTrackerEvents();
            });
            card.querySelector('[data-role="delete"]')?.addEventListener("click", () => {
                void actions.deleteTracker(tracker.id);
            });
            card.querySelector('[data-role="open"]')?.addEventListener("click", () => {
                if (context._hooks?.showToast) context._hooks.showToast("Загружаю...");
                elements.searchInput.value = tracker.query;
                state.search.query = tracker.query;
                state.search.strictSearch = Boolean(tracker.strict_mode);
                state.trackers.minDiscountPercent = Math.round(tracker.min_discount_percent || 10);
                state.trackers.maxPriceByn = tracker.max_price_byn ?? null;
                state.trackers.sellerType = tracker.seller_type || "";
                state.trackers.condition = tracker.condition || "";
                state.trackers.regionName = tracker.region_name || "";
                state.trackers.configKeyword = tracker.config_keyword || "";
                if (context._hooks?.renderStrictSearch) context._hooks.renderStrictSearch();
                if (context._hooks?.renderTrackerInputs) context._hooks.renderTrackerInputs();
                if (context._hooks?.renderLoading) context._hooks.renderLoading();
                void actions.search();
            });
            elements.trackersList.appendChild(card);
        }
        });
    }

    /* ===== Tracker Events ===== */

    function renderTrackerEvents() {
        return safeRender('renderTrackerEvents', () => {
        const container = elements.trackerEventsList;
        if (!container) return;

        // Destroy existing virtual list if present and reset container
        if (container._virtualList) {
            container._virtualList.destroy();
            container._virtualList = null;
        }
        container.style.overflowY = "";
        container.style.maxHeight = "";

        domClear(container);

        if (!hasTelegramInitData()) {
            const note = document.createElement("p");
            note.className = "tracker-event-empty";
            note.textContent = "Откройте Mini App внутри Telegram, чтобы видеть события.";
            container.appendChild(note);
            return;
        }

        const trackerScopedEvents = state.trackers.eventFilterTrackerId
            ? state.trackers.events.filter((event) => event.tracker_id === state.trackers.eventFilterTrackerId)
            : state.trackers.events.slice();

        let dropCount = 0, newCount = 0, thresholdCount = 0, discountAlertCount = 0;
        for (const e of trackerScopedEvents) {
            if (e.event_type === "price_drop") dropCount++;
            else if (e.event_type === "new_listing") newCount++;
            else if (e.event_type === "price_threshold_alert") thresholdCount++;
            else if (e.event_type === "discount_alert") discountAlertCount++;
        }
        const totalCount = trackerScopedEvents.length;

        if (elements.trackerEventsBadge) {
            elements.trackerEventsBadge.textContent = totalCount > 0 ? `${totalCount} событий` : "чат + Mini App";
        }

        const FILTER_LABELS = {
            all: "Все",
            price_drop: "Упали в цене",
            new_listing: "Новые лоты",
            price_threshold_alert: "Порог цены",
            discount_alert: "Скидка",
        };
        const FILTER_COUNTS = {
            all: totalCount,
            price_drop: dropCount,
            new_listing: newCount,
            price_threshold_alert: thresholdCount,
            discount_alert: discountAlertCount,
        };
        for (const button of elements.trackerEventFilterButtons) {
            const filter = button.dataset.eventFilter;
            const count = FILTER_COUNTS[filter] ?? 0;
            const label = FILTER_LABELS[filter] ?? filter;
            button.textContent = count > 0 ? `${label} (${count})` : label;
            button.classList.toggle("active", filter === state.trackers.eventFilter);
        }

        // Populate tracker dropdown filter
        if (elements.trackerEventTrackerSelect) {
            const select = elements.trackerEventTrackerSelect;
            const prevValue = select.value;
            const uniqueTrackers = new Map();
            for (const evt of state.trackers.events) {
                if (!uniqueTrackers.has(evt.tracker_id)) {
                    uniqueTrackers.set(evt.tracker_id, evt.query);
                }
            }
            const options = [domEl("option", { value: "", text: "Все трекеры" })];
            for (const [id, query] of uniqueTrackers) {
                const option = domEl("option", { value: id, text: query });
                if (String(id) === prevValue) option.selected = true;
                options.push(option);
            }
            select.replaceChildren(...options);
            // Restore selection from state
            select.value = state.trackers.eventFilterTrackerId || "";
        }

        // Filter events by selected tracker first, then by event type
        let filteredEvents = trackerScopedEvents.filter((event) => {
            if (state.trackers.eventFilter === "all") {
                return true;
            }
            return event.event_type === state.trackers.eventFilter;
        });

        if (!filteredEvents.length) {
            const buildEmpty = context.buildEmptyState;
            let title;
            let hint;
            if (state.trackers.eventFilterTrackerId) {
                const tracker = state.trackers.items.find((t) => t.id === state.trackers.eventFilterTrackerId);
                title = tracker ? `Тихо по запросу "${tracker.query}"` : "Тихо по этому трекеру";
                hint = "Дайте трекеру несколько часов — Kufar обновляется неравномерно.";
            } else if (state.trackers.eventFilter === "all") {
                title = "Событий пока нет";
                hint = "Они появятся после первой проверки планировщика. Свежие лоты и падения цен прилетят в этот раздел и в чат бота.";
            } else {
                title = "По этому фильтру пусто";
                hint = "Переключитесь на «Все», чтобы увидеть остальные сигналы.";
            }
            if (typeof buildEmpty === "function") {
                container.appendChild(buildEmpty({ icon: "events", title, hint }));
            } else {
                const note = document.createElement("p");
                note.className = "tracker-event-empty";
                note.textContent = `${title}. ${hint}`;
                container.appendChild(note);
            }
            return;
        }

        // Build event card element — extracted for virtual scrolling
        function buildEventNode(event) {
            const isPriceDrop = event.event_type === "price_drop";
            const thumbSrc = (() => {
                const validated = safeUrl(event.thumbnail);
                if (!validated) return "";
                return typeof optimizedImage === "function"
                    ? optimizedImage(validated, { width: 200 })
                    : validated;
            })();
            const thumbnailNode = thumbSrc
                ? domEl("img", {
                    className: "event-thumbnail",
                    attrs: { src: thumbSrc, alt: event.title || "Объявление", loading: "lazy" },
                })
                // FE-05: aria-label exposes "Нет фото" instead of the
                // SR pronouncing the bare 📱 glyph as "MOBILE PHONE".
                : domEl("div", {
                    className: "event-thumbnail-placeholder",
                    text: "📱",
                    attrs: { "aria-label": "Нет фото", role: "img" },
                });
            const eventMeta = domEl("div", { className: "event-meta" });
            if (event.region_name) eventMeta.appendChild(emojiLabel("event-meta-item", "📍", event.region_name));
            if (event.seller_type) eventMeta.appendChild(emojiLabel("event-meta-item", "👤", event.seller_type));
            const priceRow = domEl(
                "div",
                { className: "event-price-row" },
                domEl("span", { className: "event-price mono", text: event.price_byn ? `${Math.round(event.price_byn)} р.` : "без цены" }),
            );
            if (event.delta_byn && event.event_type === "price_drop") {
                priceRow.appendChild(domEl("span", { className: "event-delta", text: `-${Math.round(event.delta_byn)} р.` }));
            }
            if (event.event_type === "price_threshold_alert" && event.parameters?.threshold) {
                priceRow.appendChild(domEl("span", { className: "event-delta event-delta--alert", text: `порог: ${Math.round(event.parameters.threshold)} р.` }));
            }
            if (event.event_type === "discount_alert" && event.parameters?.discount_percent) {
                priceRow.appendChild(domEl("span", { className: "event-delta event-delta--alert", text: `-${Math.round(event.parameters.discount_percent)}% от медианы` }));
            }

            // FE-05: badges carry both an emoji and a text label; the
            // emoji is decorative duplication of the label, so it goes
            // into an aria-hidden span and screen readers announce
            // only the meaningful suffix (e.g. "Новый лот").
            let badgeClass = "new";
            let badgeEmoji = "🆕";
            let badgeLabel = "Новый лот";
            let cardModifier = "";
            if (isPriceDrop) {
                badgeClass = "drop";
                badgeEmoji = "🔽";
                badgeLabel = "Падение цены";
                cardModifier = " price-drop";
            }
            if (event.event_type === "price_threshold_alert") {
                badgeClass = "alert";
                badgeEmoji = "🎯";
                badgeLabel = "Порог цены";
                cardModifier = " threshold-alert";
            }
            if (event.event_type === "discount_alert") {
                badgeClass = "alert";
                badgeEmoji = "📉";
                badgeLabel = "Скидка от медианы";
                cardModifier = " discount-alert";
            }

            const card = domEl(
                "article",
                { className: `tracker-event-card${cardModifier}` },
                domEl(
                    "div",
                    { className: "event-header" },
                    thumbnailNode,
                    domEl(
                        "div",
                        { className: "event-body" },
                        domEl(
                            "div",
                            { className: "event-top-row" },
                            emojiLabel(`event-type-badge ${badgeClass}`, badgeEmoji, badgeLabel),
                            domEl("span", { className: "event-time", text: formatDate(event.created_at) }),
                        ),
                        domEl("strong", { className: "event-title", text: event.title }),
                        priceRow,
                        eventMeta,
                        domEl("span", { className: "event-tracker-source" },
                            domEl("span", { attrs: { "aria-hidden": "true" }, text: "🔍 " }),
                            event.query,
                        ),
                    ),
                ),
                domEl(
                    "div",
                    { className: "event-actions" },
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "open-query" }, text: "Открыть" }),
                    domEl("button", { className: "listing-btn", type: "button", dataset: { role: "lead" }, text: "В покупки" }),
                    domEl("a", {
                        className: "listing-btn listing-btn--kufar",
                        text: "Kufar →",
                        attrs: { href: safeUrl(event.link), target: "_blank", rel: "noreferrer noopener" },
                    }),
                ),
            );

            card.querySelector('[data-role="open-query"]')?.addEventListener("click", () => {
                if (event.query) {
                    elements.searchInput.value = event.query;
                    state.search.query = event.query;
                }
                state.search.strictSearch = Boolean(event.strict_mode);
                if (context._hooks?.renderStrictSearch) context._hooks.renderStrictSearch();
                void actions.openListingDetail({
                    ad_id: event.ad_id,
                    title: event.title,
                    link: event.link,
                    price_byn: event.price_byn,
                });
            });
            card.querySelector('[data-role="lead"]')?.addEventListener("click", () => {
                void actions.addLeadFromListing(
                    {
                        ad_id: event.ad_id,
                        title: event.title,
                        link: event.link,
                        price_byn: event.price_byn,
                        flip_estimates: [],
                    },
                    "tracker_event",
                    event.query
                );
            });
            return card;
        }

        const VIRTUAL_LIST_THRESHOLD = 50;
        if (filteredEvents.length > VIRTUAL_LIST_THRESHOLD) {
            container._virtualList = createVirtualList(container, {
                itemHeight: 160,
                bufferSize: 5,
                renderFn: (event, index) => buildEventNode(event),
            });
            container._virtualList.setItems(filteredEvents);
        } else {
            for (const event of filteredEvents) {
                container.appendChild(buildEventNode(event));
            }
        }
        });
    }

    /* ===== Tracker Event Filter Buttons (standalone call) ===== */

    function renderTrackerEventFilters() {
        // Tally events by event_type so each filter chip can show how
        // many alerts it represents — gives the user a sense of where
        // the action is before they tap. "all" mirrors the total.
        const events = state.trackers.events || [];
        const total = events.length;
        let priceDrops = 0;
        let newListings = 0;
        let thresholdAlerts = 0;
        let discountAlerts = 0;
        for (const event of events) {
            if (event?.event_type === "price_drop") priceDrops += 1;
            else if (event?.event_type === "new_listing") newListings += 1;
            else if (event?.event_type === "price_threshold_alert") thresholdAlerts += 1;
            else if (event?.event_type === "discount_alert") discountAlerts += 1;
        }
        const counts = {
            all: total,
            price_drop: priceDrops,
            new_listing: newListings,
            price_threshold_alert: thresholdAlerts,
            discount_alert: discountAlerts,
        };

        for (const button of elements.trackerEventFilterButtons) {
            button.classList.toggle("active", button.dataset.eventFilter === state.trackers.eventFilter);
        }
        const badges = elements.trackerEventFilterCounts || {};
        for (const [key, badge] of Object.entries(badges)) {
            if (!badge) continue;
            const value = counts[key] ?? 0;
            badge.textContent = String(value);
            badge.hidden = value === 0;
        }
    }

    return {
        renderTrackerStatus,
        renderWatchlistFilters,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
    };
}


;
/* global Chart */

/**
 * app_renderers.js — Composition entry point.
 * Delegates to focused modules: core, cards, views, modals, charts, trackers.
 * Every function that the original file exported is still available on the
 * returned object so app_actions.js continues to work without changes.
 */

function createAppRenderers(baseContext) {
    const context = { ...baseContext };
    const {
        state,
        elements,
        actions,
        formatPrice,
        formatCondition,
        formatSeller,
        formatDelta,
        deltaClass,
        formatDate,
        trapFocus,
        hasTelegramInitData,
        isDirty: isDirtyFn,
        clearDirty: clearDirtyFn,
    } = context;

    // ── Instantiate sub-modules ──────────────────────────────────────────
    const core = createRenderCore(context);

    // Share escapeHtml and safeRender across sub-modules via context
    context.escapeHtml = core.escapeHtml;
    context.safeUrl = core.safeUrl;
    context.optimizedImage = core.optimizedImage;
    context.safeRender = safeRender;
    // Empty state factory is invoked by every collection renderer
    // (watchlist, leads, tracker events) to swap the previous plain
    // ".tracker-empty" paragraph for a richer iconified block.
    context.buildEmptyState = core.buildEmptyState;

    const cards = createRenderCards(context);
    const views = createRenderViews(context);
    const modals = createRenderModals(context);
    const charts = createRenderCharts(context);
    const trackers = createRenderTrackers(context);

    // ── Cross-module hooks ───────────────────────────────────────────────
    // Modules call these when they need functionality from another module.
    context._hooks = {
        showToast: core.showToast,
        renderChart: charts.renderChart,
        destroyChart: charts.destroyChart,
        renderHistory: charts.renderHistory,
        destroyHistoryChart: charts.destroyHistoryChart,
        renderHistoryRangeButtons: views.renderHistoryRangeButtons,
        renderDealsHeroStats: views.renderDealsHeroStats,
        renderProfitDashboard: charts.renderProfitDashboard,
        renderHistoryDeals: charts.renderHistoryDeals,
        renderWatchlistFilters: trackers.renderWatchlistFilters,
        renderTrackerEvents: trackers.renderTrackerEvents,
        renderStrictSearch: core.renderStrictSearch,
        renderTrackerInputs: views.renderTrackerInputs,
        renderLoading: core.renderLoading,
        // renderRecentSearches will be added after declarations below
    };

    // ── Error boundary pattern ──────────────────────────────────────────
    /**
     * Wraps a render function in a try/catch to prevent a single render
     * error from crashing the entire app. Logs the error and shows a
     * fallback toast notification instead.
     *
     * @param {string} name - Human-readable render function name
     * @param {Function} fn - The render function to execute
     * @returns {*} The return value of fn, or null on error
     */
    function safeRender(name, fn) {
        try {
            return fn();
        } catch (err) {
            console.error('safeRender error:', name, err);
            if (typeof core.showToast === 'function') {
                core.showToast("Ошибка отображения", 'error');
            }
            return null;
        }
    }

    // ── Forwarded functions (all names that app_actions.js destructures) ─
    const {
        escapeHtml,
        safeUrl,
        showToast,
        dismissToast,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
    } = core;

    const {
        buildListingNode,
        renderListingsCollection,
        renderListings,
        renderLeads,
        renderWatchlist,
    } = cards;

    const {
        renderTrackingHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderHistoryRangeButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderFilterDropdown,
    } = views;

    const {
        renderDetailModal,
        closeDetailModal,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
    } = modals;

    const {
        destroyChart,
        destroyHistoryChart,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderProfitDashboard,
        renderHistoryDeals,
    } = charts;

    const {
        renderTrackerStatus,
        renderWatchlistFilters,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
    } = trackers;

    // Add renderRecentSearches to hooks now that it's declared
    context._hooks.renderRecentSearches = renderRecentSearches;

    // ── renderAll ────────────────────────────────────────────────────────

    /**
     * Map of render functions keyed by dirty-flag name.
     * If no dirty flags are set, all renderers run (first-call / full-refresh).
     */
    const _renderMap = {
        error: renderError,
        loading: renderLoading,
        strict: renderStrictSearch,
        tabs: renderViewTabs,
        panels: renderPanels,
        summary: renderSummary,
        helper: renderHelper,
        views: renderViews,
        trackingHero: renderTrackingHeroStats,
        dealsHero: renderDealsHeroStats,
        sort: renderSortButtons,
        discount: renderDiscountButtons,
        eventFilters: renderTrackerEventFilters,
        dealInputs: renderDealInputs,
        trackerInputs: renderTrackerInputs,
        stats: renderStats,
        history: renderHistory,
        segments: renderSegments,
        geography: renderGeography,
        recent: renderRecentSearches,
        categories: renderFilterDropdown,
        listings: renderListings,
        trackerStatus: renderTrackerStatus,
        trackers: renderTrackers,
        trackerEvents: renderTrackerEvents,
        leads: renderLeads,
        watchlist: renderWatchlist,
        profit: renderProfitDashboard,
    };

    function renderAll() {
        const hasSelectiveFlags = state.ui.dirtyViews.size > 0 && !state.ui._allDirty;
        try {
            if (hasSelectiveFlags) {
                // Selective render — only flagged views
                for (const [key, fn] of Object.entries(_renderMap)) {
                    if (state.ui.dirtyViews.has(key)) fn();
                }
            } else {
                // Full render — no specific flags or _allDirty is set
                for (const fn of Object.values(_renderMap)) fn();
            }
        } catch (err) {
            console.error('renderAll error:', err);
            if (typeof core.showToast === 'function') {
                core.showToast('Ошибка отображения', 'error');
            }
        }
        state.ui._allDirty = false;
        state.ui.dirtyViews.clear();
    }

    // Batch multiple renderAll calls into a single requestAnimationFrame.
    // This prevents render cascades when parallel API calls each trigger renderAll.
    let _renderScheduled = false;
    function scheduleRender() {
        if (!_renderScheduled) {
            _renderScheduled = true;
            requestAnimationFrame(() => {
                _renderScheduled = false;
                renderAll();
            });
        }
    }

    // ── Public API (every name the original file exported) ───────────────

    return {
        showToast,
        dismissToast,
        escapeHtml,
        safeUrl,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderPanels,
        setPanelOpen,
        renderSummary,
        renderHelper,
        renderViews,
        renderTrackingHeroStats,
        renderDealsHeroStats,
        renderSortButtons,
        renderDiscountButtons,
        renderTrackerEventFilters,
        renderDealInputs,
        renderTrackerInputs,
        renderStats,
        renderSegments,
        renderGeography,
        renderRecentSearches,
        renderListingsCollection,
        renderListings,
        renderTrackerStatus,
        renderTrackers,
        renderTrackerEvents,
        renderWatchlistFilters,
        renderLeads,
        renderWatchlist,
        renderProfitDashboard,
        renderHistoryDeals,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
        destroyChart,
        destroyHistoryChart,
        renderDetailModal,
        closeDetailModal,
        renderChart,
        renderHistory,
        renderHistoryChart,
        renderAll,
        scheduleRender,
    };
}


;
/**
 * api_core.js — Shared HTTP primitives, currency helpers, and rate loading.
 *
 * Provides the foundational request wrappers that every other API module builds on.
 */

function createApiCore(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        renderLoading,
        renderError,
        renderStrictSearch,
        renderDealInputs,
        renderTrackerInputs,
        setActiveView,
        clearSearchData,
        search,
    } = context;

    const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);
    const RETRY_BASE_DELAY_MS = 300;
    const RETRY_MAX_ATTEMPTS = 3;

    function _retryDelayMs(attempt, response) {
        const retryAfter = response?.headers?.get?.("retry-after");
        if (retryAfter) {
            const seconds = Number(retryAfter);
            if (Number.isFinite(seconds) && seconds >= 0) {
                return Math.min(5000, seconds * 1000);
            }
        }
        return Math.min(4000, RETRY_BASE_DELAY_MS * (2 ** (attempt - 1)))
            + Math.floor(Math.random() * 180);
    }

    function _sleep(ms, signal) {
        return new Promise((resolve, reject) => {
            if (signal?.aborted) {
                reject(new DOMException("Aborted", "AbortError"));
                return;
            }
            const timer = setTimeout(resolve, ms);
            signal?.addEventListener("abort", () => {
                clearTimeout(timer);
                reject(new DOMException("Aborted", "AbortError"));
            }, { once: true });
        });
    }

    function _friendlyErrorMessage(message) {
        if (/Lead version is required for updates|Lead was updated elsewhere/i.test(message)) {
            return "Данные устарели. Обновите список и попробуйте ещё раз.";
        }
        return message;
    }

    // ── Telegram headers ─────────────────────────────────────────────────
    function telegramHeaders() {
        const initData = window.Telegram?.WebApp?.initData;
        return initData ? { "X-Telegram-Init-Data": initData } : {};
    }

    // ── Generic request wrapper ──────────────────────────────────────────
    async function requestJson(url, options = {}) {
        const method = (options.method || "GET").toUpperCase();
        // FE-H7: X-Requested-With header on every state-changing call.
        // HTML forms and <img>/<link> tags can't set custom headers, so
        // requiring "XMLHttpRequest" here means a CSRF attacker has to
        // also beat the browser's CORS preflight — on top of the
        // existing Origin check on the server. It costs nothing for
        // legitimate traffic (we already send X-Telegram-Init-Data)
        // and gives us one more layer of defence for the endpoints
        // that mutate state.
        const isStateChanging = method !== "GET" && method !== "HEAD";
        const headers = {
            ...telegramHeaders(),
            ...(isStateChanging ? { "X-Requested-With": "XMLHttpRequest" } : {}),
            ...(options.headers || {}),
        };

        const canRetry = method === "GET" || method === "HEAD";
        const maxAttempts = canRetry && options.retry !== false
            ? (options.retryAttempts || RETRY_MAX_ATTEMPTS)
            : 1;
        const timeoutMs = options.timeout || 90000;

        let timedOut = false;
        let response;
        for (let attempt = 1; attempt <= maxAttempts; attempt++) {
            const controller = new AbortController();
            const timer = setTimeout(() => {
                timedOut = true;
                controller.abort();
            }, timeoutMs);
            const onExternalAbort = () => controller.abort();
            if (options.signal) {
                options.signal.addEventListener("abort", onExternalAbort, { once: true });
            }
            try {
                response = await fetch(url, {
                    ...options,
                    headers,
                    signal: controller.signal,
                });
                if (
                    attempt < maxAttempts
                    && RETRYABLE_STATUSES.has(response.status)
                ) {
                    await _sleep(_retryDelayMs(attempt, response), options.signal);
                    continue;
                }
                break;
            } catch (fetchErr) {
                if (fetchErr.name === "AbortError") {
                    if (options.signal?.aborted) throw fetchErr;
                    throw new Error("Превышено время ожидания. Попробуйте ещё раз.");
                }
                if (attempt >= maxAttempts) throw fetchErr;
                await _sleep(_retryDelayMs(attempt), options.signal);
            } finally {
                clearTimeout(timer);
                if (options.signal) {
                    options.signal.removeEventListener("abort", onExternalAbort);
                }
            }
        }

        if (!response || !response.ok) {
            let message = "Не удалось выполнить запрос.";
            try {
                const payload = await response.json();
                if (typeof payload.detail === "string" && payload.detail.trim()) {
                    message = _friendlyErrorMessage(payload.detail.trim());
                }
            } catch (_) {
                if (response.status >= 500) {
                    message = "Сервер временно недоступен. Попробуйте позже.";
                }
            }
            throw new Error(message);
        }

        if (response.status === 204) {
            return null;
        }

        return response.json();
    }

    // ── Convenience wrappers ─────────────────────────────────────────────
    function getJson(url, options) {
        return requestJson(url, options);
    }

    function postJson(url, payload, extraOptions) {
        return requestJson(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            ...(extraOptions || {}),
        });
    }

    function deleteJson(url, payload) {
        // BE-M3: DELETE may carry a JSON body (e.g. account-deletion
        // confirmation). When ``payload`` is omitted we keep the
        // historical no-body behaviour so existing callers don't need
        // to change.
        const opts = { method: "DELETE" };
        if (payload !== undefined) {
            opts.headers = { "Content-Type": "application/json" };
            opts.body = JSON.stringify(payload);
        }
        return requestJson(url, opts);
    }

    // ── Query builder ────────────────────────────────────────────────────
    function buildCommonQuery(params = {}) {
        const query = new URLSearchParams({
            query: state.search.query,
            currency: state.misc.currency,
            strict_search: String(state.search.strictSearch),
        });
        if (state.filters.category != null) {
            query.set("category", String(state.filters.category));
        }

        for (const [key, value] of Object.entries(params)) {
            if (value == null || value === "") {
                continue;
            }
            query.set(key, String(value));
        }

        return query.toString();
    }

    return {
        telegramHeaders,
        requestJson,
        getJson,
        postJson,
        deleteJson,
        buildCommonQuery,
    };
}


;
/**
 * api_listings.js — Search, listings, deals, and detail loading.
 *
 * All functions that fetch and manage listing data and search
 * orchestration belong here.
 */

function createApiListings(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        scheduleRender,
        markDirty,
        renderLoading,
        renderError,
        renderHistory,
        setPanelOpen,
        renderDetailModal,
        closeDetailModal,
        showToast,
        dismissToast,
        buildCommonQuery,
        getJson,
        deleteJson,
    } = context;

    // ── Stale-request guard ──────────────────────────────────────────────
    function isActiveRequest(requestId) {
        return requestId === state.search.searchRequestId;
    }

    // Cache TTL: skip reload if data was fetched within this window (ms)
    const CACHE_TTL = 2 * 60 * 1000; // 2 minutes

    // Page size — must match the backend's `_DEFAULT_LISTINGS_PAGE`.
    // Hoisted to module scope so `loadSearchDependencies` can use it.
    const PAGE_SIZE = 50;

    let _detailAbortController = null;
    let _historyAbortController = null;

    // FE-C4: explicit abort hooks so callers in the modal/view
    // lifecycle (closeDetailModal, view-tab change, etc.) can drop
    // in-flight requests instead of letting them resolve into stale
    // state. Each hook is a no-op when nothing is pending.
    function abortDetailRequest() {
        if (_detailAbortController) {
            _detailAbortController.abort();
            _detailAbortController = null;
        }
    }
    function abortHistoryRequest() {
        if (_historyAbortController) {
            _historyAbortController.abort();
            _historyAbortController = null;
        }
    }
    function abortSearchRequests() {
        if (state.search.searchAbortController) {
            state.search.searchAbortController.abort();
            state.search.searchAbortController = null;
        }
    }

    function buildListingsQuery(params = {}) {
        const query = new URLSearchParams(buildCommonQuery(params));
        if (state.filters.category != null) {
            query.set("reference_context", "base_query");
        }
        return query.toString();
    }

    // ── Clear search-dependent state ─────────────────────────────────────
    function clearSearchData() {
        state.misc.stats = null;
        state.charts.historyData = [];
        state.misc.segments = null;
        state.misc.geography = [];
        state.listings.items = [];
        state.listings._loadedAt = 0;
        state.listings.total = 0;
        state.listings.fallbackUsed = false;
        state.detail.data = null;
        state.detail.imageIndex = 0;
        state.listings._pending = true;
    }

    function resetCategoryFilter() {
        state.filters.category = null;
        state.filters.categories = [];
        // Reset all filter state when query changes
        state.filters.condition = "";
        state.filters.sellerType = "";
        state.filters.minPrice = null;
        state.filters.maxPrice = null;
        state.filters.regionName = "";
        // Also reset pending values
        state.filters.pendingCategory = null;
        state.filters.pendingCondition = "";
        state.filters.pendingSellerType = "";
        state.filters.pendingMinPrice = null;
        state.filters.pendingMaxPrice = null;
        state.filters.pendingRegionName = "";
    }

    // ── Load price history (standalone) ──────────────────────────────────
    async function loadHistory() {
        if (!state.search.query) {
            state.charts.historyData = [];
            renderHistory();
            return;
        }
        // Abort previous in-flight history request
        if (_historyAbortController) {
            _historyAbortController.abort();
        }
        _historyAbortController = new AbortController();
        const signal = _historyAbortController.signal;

        // Stale-response guard — switching the history range button or
        // changing the query mid-fetch shouldn't let the older response
        // overwrite the freshly-requested points.
        const requestId = (state.charts._historyRequestId =
            (state.charts._historyRequestId + 1) % 1_000_000);
        let nextHistory;
        try {
            const payload = await getJson(
                `/api/v1/price-history?${buildCommonQuery({ days: state.misc.historyDays })}`,
                { signal }
            );
            nextHistory = payload.points || [];
        } catch (err) {
            if (err.name === "AbortError") return;
            nextHistory = [];
        }
        if (requestId !== state.charts._historyRequestId) return;
        state.charts.historyData = nextHistory;
        renderHistory();
    }

    // ── Load parallel search dependencies ────────────────────────────────
    function loadSearchDependencies(requestId) {
        // Abort previous in-flight search dependencies
        if (state.search.searchAbortController) {
            state.search.searchAbortController.abort();
        }
        state.search.searchAbortController = new AbortController();
        const signal = state.search.searchAbortController.signal;

        const dependencies = [
            {
                request: getJson(`/api/v1/price-history?${buildCommonQuery({ days: state.misc.historyDays })}`, { signal }),
                apply(payload) {
                    state.charts.historyData = payload.points || [];
                },
            },
            {
                request: getJson(`/api/v1/segments?${buildCommonQuery()}`, { signal }),
                apply(payload) {
                    state.misc.segments = payload;
                },
            },
            {
                request: getJson(`/api/v1/geography?${buildCommonQuery()}`, { signal }),
                apply(payload) {
                    state.misc.geography = payload.regions || [];
                },
            },
            {
                request: getJson(
                    `/api/v1/listings?${buildListingsQuery({
                        sort: state.search.sort,
                        limit: PAGE_SIZE,
                        offset: 0,
                    })}`,
                    { signal },
                ),
                apply(payload) {
                    state.listings.items = payload.listings || [];
                    state.listings.total = payload.total || 0;
                    state.listings.hasMore = Boolean(payload.has_more);
                    state.listings.fallbackUsed = Boolean(payload.fallback_used);
                    state.listings._loadedAt = Date.now();
                    state.listings._loadedSort = state.search.sort;
                    state.listings._pending = false;
                },
            },
        ];

        // FE-03: each dependency still applies its slice as it lands
        // (so the user sees progressive paint of history → segments →
        // geography → listings), but we ONLY clear ``listings._pending``
        // — i.e. drop the skeletons — once EVERY dependency has
        // settled. The previous code flipped _pending = false the
        // moment any single dependency rejected, which made the
        // listings skeleton vanish while the other three were still
        // in flight, replacing it with a half-empty page.
        const settled = dependencies.map((dependency) =>
            dependency.request
                .then((payload) => {
                    if (!isActiveRequest(requestId)) return;
                    dependency.apply(payload);
                    markDirty('stats', 'history', 'segments', 'geography', 'listings');
                    scheduleRender();
                })
                .catch((err) => {
                    if (err.name === "AbortError" || !isActiveRequest(requestId)) {
                        return;
                    }
                    // Don't clear _pending here — wait for the whole
                    // fan-out to settle so other still-loading slots
                    // keep their skeletons.
                    markDirty('stats', 'history', 'segments', 'geography', 'listings');
                    scheduleRender();
                })
        );
        Promise.allSettled(settled).then(() => {
            if (!isActiveRequest(requestId)) return;
            state.listings._pending = false;
            markDirty('listings');
            scheduleRender();
        });
    }

    // ── Load listings (ads view) ─────────────────────────────────────────
    function _listingsQueryParams(extra) {
        // Cheap sort folds in the discount-range filters that used to
        // belong to the standalone "Выгодно" view. Other sorts ignore
        // the discount params; the backend only honours them when
        // sort=cheap.
        const params = { sort: state.search.sort, ...extra };
        if (state.search.sort === "cheap") {
            params.discount_from_percent = state.filters.discountFromPercent;
            params.discount_to_percent = state.filters.discountToPercent;
        }
        return params;
    }

    function _sameDiscountRangeAsLast() {
        return (
            state._listingsLoadedDiscountFrom === state.filters.discountFromPercent &&
            state._listingsLoadedDiscountTo === state.filters.discountToPercent
        );
    }

    async function loadListings(force) {
        if (!state.search.query) {
            state.listings.items = [];
            state.listings.hasMore = false;
            markDirty('listings');
            renderAll();
            return;
        }
        // Skip reload if data is fresh AND sort + discount range hasn't
        // changed. Discount range only matters when sort=cheap.
        const cacheStillValid =
            state.listings.items.length &&
            state.listings._loadedSort === state.search.sort &&
            (state.search.sort !== "cheap" || _sameDiscountRangeAsLast()) &&
            Date.now() - state.listings._loadedAt < CACHE_TTL;
        if (!force && cacheStillValid) {
            return;
        }

        // First page — reset pagination cursor.
        state.listings.items = [];
        state.listings.hasMore = false;
        state.listings.loading = true;
        markDirty('listings');
        renderAll();

        const requestId = (state.listings._requestId =
            ((state.listings._requestId || 0) + 1) % 1_000_000);

        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery(_listingsQueryParams({
                    limit: PAGE_SIZE,
                    offset: 0,
                }))}`
            );
            if (requestId !== state.listings._requestId) return;
            state.listings.items = payload.listings || [];
            state.listings.total = payload.total || 0;
            state.listings.hasMore = Boolean(payload.has_more);
            state.listings.fallbackUsed = Boolean(payload.fallback_used);
            state.listings._loadedAt = Date.now();
            state.listings._loadedSort = state.search.sort;
            state._listingsLoadedDiscountFrom = state.filters.discountFromPercent;
            state._listingsLoadedDiscountTo = state.filters.discountToPercent;
            state.ui.error = null;
        } catch (error) {
            if (requestId !== state.listings._requestId) return;
            state.listings.items = [];
            state.listings.hasMore = false;
            state.ui.error = error.message || "Не удалось загрузить объявления";
            renderError();
        } finally {
            if (requestId === state.listings._requestId) {
                state.listings.loading = false;
                markDirty('listings', 'error');
                renderAll();
            }
        }
    }

    async function loadMoreListings() {
        // Append the next page onto state.listings — trigger from the
        // IntersectionObserver attached to the bottom sentinel card.
        // Multiple observer fires while a request is in-flight should
        // be coalesced via state.listings.loadingMore.
        if (!state.search.query) return;
        if (!state.listings.hasMore) return;
        if (state.listings.loadingMore) return;
        state.listings.loadingMore = true;
        markDirty('listings');
        scheduleRender();

        const offset = state.listings.items.length;
        const requestId = state.listings._requestId;
        try {
            const payload = await getJson(
                `/api/v1/listings?${buildListingsQuery(_listingsQueryParams({
                    limit: PAGE_SIZE,
                    offset,
                }))}`
            );
            // Drop the response if the user re-searched in the
            // meantime — the new query ditched the old cursor.
            if (requestId !== state.listings._requestId) return;
            const fresh = payload.listings || [];
            state.listings.items = state.listings.items.concat(fresh);
            state.listings.total = payload.total || state.listings.total;
            state.listings.hasMore = Boolean(payload.has_more);
        } catch (_) {
            // On error, surface the chip-style "load more" button by
            // keeping has_more true. The user can tap to retry.
        } finally {
            state.listings.loadingMore = false;
            markDirty('listings');
            renderAll();
        }
    }

    // ── Open listing detail modal ────────────────────────────────────────
    async function openListingDetail(item) {
        const queryToUse = (item?.query || state.search.query || "").trim();
        if (!item?.ad_id || !queryToUse) {
            return;
        }

        // Abort previous in-flight detail request
        if (_detailAbortController) {
            _detailAbortController.abort();
        }
        _detailAbortController = new AbortController();
        const signal = _detailAbortController.signal;

        // Stale-response guard — if the user taps a different listing
        // before the first detail fetch resolves, the older response
        // would otherwise overwrite state.detail and pop the wrong
        // modal contents on screen.
        const requestId = (state.detail._requestId =
            (state.detail._requestId + 1) % 1_000_000);

        const loadingToast = showToast("Загружаю...", "info", 1400);
        state.ui.error = null;
        renderError();
        try {
            const params = new URLSearchParams({
                query: queryToUse,
                currency: state.misc.currency,
                strict_search: String(state.search.strictSearch),
                ad_id: String(item.ad_id),
            });
            if (state.filters.category != null) {
                params.set("category", String(state.filters.category));
                params.set("reference_context", "base_query");
            }
            const fullDetail = await getJson(
                `/api/v1/listing-detail?${params.toString()}`,
                { signal }
            );
            if (requestId !== state.detail._requestId) return;
            state.detail.data = fullDetail;
            state.detail.imageIndex = 0;
            state.detail.fromWatchlist = false;
            state.detail.ai = {
                adId: fullDetail.ad_id || item.ad_id,
                loading: false,
                result: null,
                error: "",
                source: "",
            };
            renderDetailModal();
        } catch (error) {
            if (error.name === "AbortError") {
                return;
            }
            if (requestId !== state.detail._requestId) return;
            state.ui.error = error.message || "Не удалось загрузить детали";
            renderError();
        } finally {
            if (loadingToast) dismissToast(loadingToast);
        }
    }

    // ── Search orchestration ─────────────────────────────────────────────
    /**
     * Run a search and (re)load all dependent panels.
     *
     * @param {string}  target           Active view to focus after search.
     * @param {object}  [options]
     * @param {boolean} [options.keepFilters=false]
     *        If true, do NOT reset the category/condition/price/region
     *        filters. The filter dropdown's "Применить" button passes
     *        true so the user-picked category is honored. Every other
     *        entry point (text input, Enter, search button, recent
     *        searches, programmatic) uses the default false: every new
     *        query starts clean — no leftover category from the last
     *        search bleeding through.
     */
    async function search(target = "overview", { keepFilters = false } = {}) {
        const query = elements.searchInput.value.trim();
        state.search.query = query;
        state.ui.error = null;

        if (!query) {
            clearSearchData();
            resetCategoryFilter();
            state.ui.loading = false;
            context.focusTarget("overview");
            renderAll();
            return;
        }

        if (!keepFilters) {
            resetCategoryFilter();
        }

        context.focusTarget(target);
        clearSearchData();
        state.ui.loading = true;
        state.search.searchRequestId = (state.search.searchRequestId + 1) % 1_000_000;
        const requestId = state.search.searchRequestId;
        markDirty('loading', 'error', 'summary', 'helper', 'stats');
        renderAll();

        // FE-C2: cancel any in-flight /price-stats from a previous
        // search before kicking off a new one. Without this, fast
        // typers race two stats requests against each other and the
        // older one's response can clobber the fresh state — even
        // with isActiveRequest() guarding the .then() branch, the
        // socket and DB query keep running on the server. We reuse
        // the same searchAbortController that loadSearchDependencies
        // wires up, so abort() drops both groups in one call.
        if (state.search.searchAbortController) {
            state.search.searchAbortController.abort();
        }
        state.search.searchAbortController = new AbortController();
        const searchSignal = state.search.searchAbortController.signal;

        try {
            const stats = await getJson(
                `/api/v1/price-stats?${buildCommonQuery()}`,
                { signal: searchSignal },
            );
            if (!isActiveRequest(requestId)) {
                return;
            }

            state.misc.stats = stats;
            state.ui.loading = false;
            // Categories cache rules:
            //  - Broad search (state.filters.category == null) ⇒ ALWAYS replace
            //    the chips with whatever the new query returned, even
            //    when the new distribution is empty/1-element. This
            //    prevents stale chips ("Легковые авто (11)") from
            //    bleeding into a refined query that no longer has
            //    that category.
            //  - Narrowed search (a category chip is active) ⇒ keep
            //    the previous distribution because /price-stats with
            //    a `category=` filter only returns that one bucket
            //    and we'd lose the other chips.
            if (state.filters.category == null) {
                state.filters.categories = stats.categories || [];
            } else if (stats.categories && stats.categories.length > 1) {
                state.filters.categories = stats.categories;
            }
            markDirty('loading', 'stats', 'summary', 'helper', 'categories');
            renderAll();
            loadSearchDependencies(requestId);

            // Save to recent searches
            if (context.addRecentSearch) {
                context.addRecentSearch(query);
            }
            if (context._hooks?.renderRecentSearches) {
                context._hooks.renderRecentSearches();
            }
        } catch (error) {
            if (!isActiveRequest(requestId)) {
                return;
            }
            // FE-C2: AbortError is the expected outcome when a
            // newer search() supersedes us; don't surface it as a
            // user-facing failure. Only "real" errors (network/HTTP)
            // get the toast and the retry button.
            if (error?.name === "AbortError") {
                return;
            }
            clearSearchData();
            state.ui.error = error.message || "Не удалось загрузить аналитику.";
            state.ui.loading = false;
            markDirty('loading', 'error', 'summary', 'helper');
            renderAll();
        }
    }

    return {
        search,
        loadListings,
        loadMoreListings,
        openListingDetail,
        loadSearchDependencies,
        clearSearchData,
        resetCategoryFilter,
        loadHistory,
        // FE-C4: abort hooks exposed for callers (closeDetailModal,
        // view changes) to drop pending requests on unmount.
        abortDetailRequest,
        abortHistoryRequest,
        abortSearchRequests,
    };
}


;
/**
 * api_trackers.js — Tracker CRUD, event feed, and auto-refresh.
 *
 * Manages tracker lifecycle: create, pause, resume, delete, edit,
 * plus the periodic background refresh of the tracker event feed.
 */

function createApiTrackers(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        trapFocus,
        renderAll,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
        renderTrackerStatus,
        showToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
    } = context;

    // ── Auto-refresh state ───────────────────────────────────────────────
    let trackerRefreshTimer = null;
    const TRACKER_REFRESH_MS = 30_000;

    function startTrackerRefresh() {
        stopTrackerRefresh();
        trackerRefreshTimer = setInterval(() => {
            void refreshTrackerEvents();
        }, TRACKER_REFRESH_MS);
    }

    function stopTrackerRefresh() {
        if (trackerRefreshTimer) {
            clearInterval(trackerRefreshTimer);
            trackerRefreshTimer = null;
        }
    }

    async function refreshTrackerEvents() {
        if (!hasTelegramInitData()) return;
        if (state.ui.activeView !== "tracking") return;
        try {
            const [trackersResult, eventsResult] = await Promise.allSettled([
                getJson("/api/v1/trackers"),
                getJson("/api/v1/tracker-events"),
            ]);
            if (trackersResult.status === "fulfilled") {
                state.trackers.items = trackersResult.value;
                renderTrackers();
            }
            if (eventsResult.status === "fulfilled") {
                state.trackers.events = eventsResult.value;
                renderTrackerEvents();
                renderTrackerEventFilters();
            }
        } catch (err) {
            // Background refresh failed — will retry on next interval
        }
    }

    // ── Load trackers + events ───────────────────────────────────────────
    async function loadTrackers() {
        if (!hasTelegramInitData()) {
            state.trackers.items = [];
            state.trackers.events = [];
            state.trackers.status = "";
            renderTrackers();
            renderTrackerEvents();
            renderTrackerStatus();
            return;
        }

        // Show skeleton cards while loading
        state.trackers.items = [];
        state.trackers._loading = true;
        renderTrackers();

        const [trackersResult, eventsResult] = await Promise.allSettled([
            getJson("/api/v1/trackers"),
            getJson("/api/v1/tracker-events"),
        ]);

        state.trackers._loading = false;
        state.trackers.items = trackersResult.status === "fulfilled" ? trackersResult.value : [];
        state.trackers.events = eventsResult.status === "fulfilled" ? eventsResult.value : [];

        const failures = [trackersResult, eventsResult].filter((r) => r.status === "rejected");
        if (failures.length > 0 && context.showToast) {
            context.showToast("Не удалось загрузить некоторые данные", "error", 3000);
        }

        if (trackersResult.status === "rejected") {
            state.trackers.status = trackersResult.reason?.message || "Не удалось загрузить трекеры.";
            state.trackers.statusKind = "error";
        } else {
            state.trackers.status = "";
            state.trackers.statusKind = "info";
        }

        renderTrackers();
        renderTrackerEvents();
        renderTrackerStatus();
    }

    // ── Create tracker ───────────────────────────────────────────────────
    async function createTracker() {
        if (!hasTelegramInitData()) {
            showToast("Доступно только в Telegram");
            return;
        }

        const query = state.search.query.trim();
        if (!query) {
            showToast("Сначала введите запрос");
            return;
        }

        const normalizedQuery = query.toLocaleLowerCase("ru-RU");
        const duplicate = state.trackers.items.find(
            (t) => t.query.trim().toLocaleLowerCase("ru-RU") === normalizedQuery
        );
        if (duplicate) {
            showToast("Такой трекер уже существует");
            return;
        }

        try {
            state.trackers.creating = true;
            if (context.renderAll) context.renderAll();
            await postJson("/api/v1/trackers", {
                query,
                strict_mode: state.search.strictSearch,
                interval_min: 15,
                min_discount_percent: state.trackers.minDiscountPercent,
                max_price_byn: state.trackers.maxPriceByn,
                seller_type: state.trackers.sellerType || null,
                condition: state.trackers.condition || null,
                region_name: state.trackers.regionName || null,
                config_keyword: state.trackers.configKeyword || null,
            });
            showToast("Трекер добавлен", "success");
            await loadTrackers();
            renderAll();
        } catch (error) {
            state.trackers.status = error.message || "Не удалось создать трекер.";
            state.trackers.statusKind = "error";
            renderTrackerStatus();
        } finally {
            state.trackers.creating = false;
            if (context.renderAll) context.renderAll();
        }
    }

    // ── Delete tracker ───────────────────────────────────────────────────
    async function deleteTracker(trackerId) {
        if (!trackerId) {
            return;
        }

        try {
            await deleteJson(`/api/v1/trackers/${trackerId}`);
            showToast("Трекер удалён");
            await loadTrackers();
            renderAll();
        } catch (error) {
            state.trackers.status = error.message || "Не удалось удалить трекер.";
            state.trackers.statusKind = "error";
            renderTrackerStatus();
        }
    }

    // ── Pause / Resume tracker ───────────────────────────────────────────
    async function pauseTracker(trackerId) {
        if (!hasTelegramInitData()) {
            showToast("Доступно только в Telegram");
            return;
        }
        try {
            await postJson(`/api/v1/trackers/${trackerId}/pause`);
            showToast("Трекер приостановлен");
            await loadTrackers();
            renderAll();
        } catch (error) {
            showToast(error.message || "Не удалось приостановить трекер");
        }
    }

    async function resumeTracker(trackerId) {
        if (!hasTelegramInitData()) {
            showToast("Доступно только в Telegram");
            return;
        }
        try {
            await postJson(`/api/v1/trackers/${trackerId}/resume`);
            showToast("Трекер возобновлен");
            await loadTrackers();
            renderAll();
        } catch (error) {
            showToast(error.message || "Не удалось возобновить трекер");
        }
    }

    // ── Edit tracker modal ───────────────────────────────────────────────
    function openEditTracker(trackerId) {
        const tracker = state.trackers.items.find((t) => t.id === trackerId);
        if (!tracker) {
            showToast("Трекер не найден");
            return;
        }

        state.trackers.editingId = trackerId;

        if (elements.editTrackerQuery) elements.editTrackerQuery.value = tracker.query;
        if (elements.editStrictModeToggle) elements.editStrictModeToggle.checked = Boolean(tracker.strict_mode);
        if (elements.editMinDiscountInput) elements.editMinDiscountInput.value = tracker.min_discount_percent ?? 10;
        if (elements.editMaxPriceInput) elements.editMaxPriceInput.value = tracker.max_price_byn ?? "";
        if (elements.editSellerSelect) elements.editSellerSelect.value = tracker.seller_type || "";
        if (elements.editConditionSelect) elements.editConditionSelect.value = tracker.condition || "";
        if (elements.editRegionSelect) elements.editRegionSelect.value = tracker.region_name || "";
        if (elements.editConfigInput) elements.editConfigInput.value = tracker.config_keyword || "";

        if (elements.editTrackerModal) {
            if (state.misc.modalCleanup) {
                state.misc.modalCleanup();
                state.misc.modalCleanup = null;
            }
            // FE-H4/UX-H1: openModalAnimated() already installs a
            // focus trap (see dom_helpers.js). The extra trapFocus()
            // call we used to make here registered a second keydown
            // listener that both handled Tab, leading to focus fights
            // and a leaked listener once closeModalAnimated cleaned up
            // only _focusTrapCleanup.
            openModalAnimated(elements.editTrackerModal);
        }
    }

    function closeEditTracker() {
        if (state.misc.modalCleanup) {
            state.misc.modalCleanup();
            state.misc.modalCleanup = null;
        }
        state.trackers.editingId = null;
        if (elements.editTrackerModal) closeModalAnimated(elements.editTrackerModal);
    }

    async function saveTracker() {
        if (!hasTelegramInitData() || !state.trackers.editingId) {
            showToast("Ошибка");
            return;
        }

        try {
            await requestJson(`/api/v1/trackers/${state.trackers.editingId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    strict_mode: elements.editStrictModeToggle?.checked,
                    min_discount_percent: Number(elements.editMinDiscountInput?.value) || null,
                    max_price_byn: elements.editMaxPriceInput?.value ? Number(elements.editMaxPriceInput.value) : null,
                    seller_type: elements.editSellerSelect?.value || null,
                    condition: elements.editConditionSelect?.value || null,
                    region_name: elements.editRegionSelect?.value || null,
                    config_keyword: elements.editConfigInput?.value || null,
                }),
            });

            showToast("Трекер обновлен");
            closeEditTracker();
            await loadTrackers();
            renderAll();
        } catch (error) {
            showToast(error.message || "Не удалось обновить трекер");
        }
    }

    return {
        loadTrackers,
        createTracker,
        pauseTracker,
        resumeTracker,
        deleteTracker,
        openEditTracker,
        closeEditTracker,
        saveTracker,
        refreshTrackerEvents,
        startTrackerRefresh,
        stopTrackerRefresh,
    };
}


;
/**
 * api_leads.js — Leads (purchases) CRUD, lifecycle, and detail.
 *
 * Handles loading, confirming, cancelling, closing, reverting, deleting leads,
 * plus updating lead metadata and opening the detail modal for a lead.
 */

function createApiLeads(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        renderError,
        renderLeads,
        renderDealsHeroStats,
        renderProfitDashboard,
        renderDetailModal,
        showToast,
        dismissToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
    } = context;

    let _leadDetailAbortController = null;
    let _analyticsAbortController = null;

    function _validVersion(value) {
        const numeric = Number(value);
        return Number.isInteger(numeric) && numeric >= 1 ? numeric : null;
    }

    function _findLeadSnapshot(leadOrId) {
        const leadId = typeof leadOrId === "object" ? leadOrId?.id : leadOrId;
        return state.leads.items.find((l) => l.id === leadId) || null;
    }

    async function _resolveLeadVersion(leadOrId) {
        const snapshot = _findLeadSnapshot(leadOrId) || (typeof leadOrId === "object" ? leadOrId : null);
        const version = _validVersion(snapshot?.version);
        if (version) return version;
        await loadLeads();
        return _validVersion(_findLeadSnapshot(leadOrId)?.version);
    }

    function _showStaleLeadToast() {
        showToast("Данные устарели. Обновите список и попробуйте ещё раз.", "error");
    }

    // ── Load leads ───────────────────────────────────────────────────────
    async function loadLeads() {
        if (!hasTelegramInitData()) {
            state.leads.items = [];
            renderLeads();
            renderDealsHeroStats();
            renderProfitDashboard();
            return;
        }
        // Stale-response guard: rapid tab toggling or mutations followed
        // by reloads can issue multiple in-flight loadLeads() calls. The
        // older response can otherwise resolve last and overwrite a
        // fresher state with outdated rows.
        const requestId = (state.leads._requestId = (state.leads._requestId + 1) % 1_000_000);
        let nextLeads;
        try {
            nextLeads = await getJson("/api/v1/leads");
        } catch (_) {
            nextLeads = [];
        }
        // Drop the response if a newer loadLeads() has started since.
        if (requestId !== state.leads._requestId) return;
        state.leads.items = nextLeads;
        renderLeads();
        renderDealsHeroStats();
        renderProfitDashboard();
        // Refresh server-side analytics in parallel with the lead list —
        // the dashboard depends on lead-mutating endpoints (sale, expense)
        // so any reload of leads should also refresh aggregates.
        if (!state.analytics.loading) {
            void loadAnalytics();
        }
    }

    // ── Load lead analytics dashboard ────────────────────────────────────
    async function loadAnalytics() {
        if (!hasTelegramInitData()) {
            state.analytics.dashboard = null;
            renderProfitDashboard();
            return;
        }

        // Abort previous in-flight analytics request
        if (_analyticsAbortController) {
            _analyticsAbortController.abort();
        }
        _analyticsAbortController = new AbortController();
        const signal = _analyticsAbortController.signal;

        const requestId = (state._analyticsRequestId =
            ((state._analyticsRequestId || 0) + 1) % 1_000_000);
        state.analytics.loading = true;
        renderProfitDashboard();
        try {
            const days = Number(state.analytics.periodDays || 90);
            const response = await getJson(
                `/api/v1/analytics/leads?days=${encodeURIComponent(days)}`,
                { signal },
            );
            if (requestId !== state._analyticsRequestId) return;
            state.analytics.dashboard = response;
        } catch (err) {
            if (err.name === "AbortError") return;
            if (requestId !== state._analyticsRequestId) return;
            state.analytics.dashboard = null;
        } finally {
            if (requestId === state._analyticsRequestId) {
                state.analytics.loading = false;
                renderProfitDashboard();
            }
        }
    }

    // ── Clear all leads (active only) ────────────────────────────────────
    async function clearAllLeads() {
        const activeLeads = state.leads.items.filter((l) => l.status !== "closed");
        if (!activeLeads.length) {
            showToast("Нет активных сделок для удаления");
            return;
        }
        try {
            await deleteJson("/api/v1/leads/all");
            const count = activeLeads.length;
            state.leads.items = state.leads.items.filter((l) => l.status === "closed");
            renderLeads();
            showToast(`Удалено ${count} сделок`);
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось очистить список");
            await loadLeads();
        }
    }

    // ── Confirm lead (sold stage with prices) ────────────────────────────
    async function confirmLead(lead, cardElement) {
        const buyPriceInput = cardElement.querySelector('[data-role="buy-price"]');
        const soldPriceInput = cardElement.querySelector('[data-role="sold-price"]');

        const buyPriceRaw = buyPriceInput?.value?.trim();
        const soldPriceRaw = soldPriceInput?.value?.trim();

        if (!buyPriceRaw || !soldPriceRaw) {
            showToast("Заполните оба поля: цена покупки и цена продажи");
            if (!buyPriceRaw) {
                buyPriceInput?.focus();
            } else {
                soldPriceInput?.focus();
            }
            return;
        }

        const buyPriceNum = parseInt(buyPriceRaw, 10);
        const soldPriceNum = parseInt(soldPriceRaw, 10);

        if (!Number.isInteger(buyPriceNum) || buyPriceNum <= 0) {
            showToast("Введите целую цену покупки больше 0");
            buyPriceInput?.focus();
            return;
        }
        if (buyPriceNum > 10_000_000) {
            showToast("Цена покупки слишком большая (макс. 10 000 000 BYN)");
            buyPriceInput?.focus();
            return;
        }

        if (!Number.isInteger(soldPriceNum) || soldPriceNum <= 0) {
            showToast("Введите целую цену продажи больше 0");
            soldPriceInput?.focus();
            return;
        }
        if (soldPriceNum > 10_000_000) {
            showToast("Цена продажи слишком большая (макс. 10 000 000 BYN)");
            soldPriceInput?.focus();
            return;
        }

        const pendingToast = showToast("Сохраняю…", "info", 8000);
        const version = await _resolveLeadVersion(lead);
        if (!version) {
            if (pendingToast) dismissToast(pendingToast);
            _showStaleLeadToast();
            return;
        }

        try {
            const payload = {
                buy_price_byn: buyPriceNum,
                sold_price_byn: soldPriceNum,
                status: "sold",
                version,
            };

            const updatedLead = await requestJson(`/api/v1/leads/${lead.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            const leadInState = state.leads.items.find((l) => l.id === lead.id);
            if (leadInState) {
                Object.assign(leadInState, updatedLead);
            }

            renderLeads();

            const profit = soldPriceNum - buyPriceNum;
            const profitSign = profit >= 0 ? "+" : "";
            if (pendingToast) dismissToast(pendingToast);
            showToast(`Сделка подтверждена: ${profitSign}${Math.round(profit)} BYN`, "success");

            // Full reload to refresh analytics/profit dashboard data
            await loadLeads();

            setTimeout(() => {
                const updatedCard = elements.leadInboxList?.querySelector(
                    `[data-lead-id="${lead.id}"]`
                );
                if (updatedCard) {
                    updatedCard.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }, 100);
        } catch (error) {
            // Revert optimistic state by reloading from server
            if (pendingToast) dismissToast(pendingToast);
            showToast(error.message || "Не удалось подтвердить сделку", "error");
            await loadLeads();
        }
    }

    // ── Cancel lead (delete) ─────────────────────────────────────────────
    async function cancelLead(leadId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}`);
            state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
            renderLeads();
            showToast("Сделка отменена", "info");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось отменить сделку");
        }
    }

    // ── Close deal (mark closed, show profit) ────────────────────────────
    async function closeDeal(leadId) {
        try {
            const lead = state.leads.items.find((l) => l.id === leadId);
            const version = await _resolveLeadVersion(lead || leadId);
            if (!version) {
                _showStaleLeadToast();
                return;
            }
            const buyPriceByn = lead?.buy_price_byn || 0;
            const soldPriceByn = lead?.sold_price_byn || 0;

            const profit = soldPriceByn - buyPriceByn;

            const updatedLead = await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "closed",
                    version,
                }),
            });

            if (lead) {
                Object.assign(lead, updatedLead);
            }
            renderLeads();

            const profitSign = profit >= 0 ? "+" : "";
            if (profit >= 0) {
                showToast(`Сделка закрыта: ${profitSign}${Math.round(profit)} BYN`);
            } else {
                showToast(`Сделка закрыта: ${Math.round(profit)} BYN`);
            }
        } catch (error) {
            showToast(error.message || "Не удалось закрыть сделку");
        }
    }

    // ── Revert lead stage (back to new) ──────────────────────────────────
    async function revertLeadStage(leadId, currentStatus) {
        try {
            const leadInState = state.leads.items.find((l) => l.id === leadId);
            const version = await _resolveLeadVersion(leadInState || leadId);
            if (!version) {
                _showStaleLeadToast();
                return;
            }
            const updatedLead = await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "new",
                    buy_price_byn: null,
                    sold_price_byn: null,
                    version,
                }),
            });

            if (leadInState) {
                Object.assign(leadInState, updatedLead);
            }
            renderLeads();

            showToast("Сделка возвращена");

            setTimeout(() => {
                const updatedCard = elements.leadInboxList?.querySelector(
                    `[data-lead-id="${leadId}"]`
                );
                if (updatedCard) {
                    updatedCard.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }, 100);
        } catch (error) {
            showToast(error.message || "Не удалось вернуть сделку");
        }
    }

    // ── Delete single lead ───────────────────────────────────────────────
    async function deleteLead(leadId) {
        try {
            await deleteJson(`/api/v1/leads/${leadId}`);
            state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
            renderLeads();
            showToast("Сделка удалена");
            await loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось удалить");
        }
    }

    // ── Update lead metadata ─────────────────────────────────────────────
    async function updateLeadMeta(leadId, payload) {
        try {
            const lead = state.leads.items.find((l) => l.id === leadId);
            const version = payload.version ?? await _resolveLeadVersion(lead || leadId);
            if (!version) {
                _showStaleLeadToast();
                return false;
            }
            await requestJson(`/api/v1/leads/${leadId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    ...payload,
                    version,
                }),
            });
            await loadLeads();
            return true;
        } catch (error) {
            showToast(error.message || "Не удалось обновить сделку");
            await loadLeads();
            return false;
        }
    }

    async function updateLeadStatus(leadId, nextStatus) {
        await updateLeadMeta(leadId, { status: nextStatus });
    }

    // ── Mark lead as sold ────────────────────────────────────────────────
    async function markLeadAsSold(lead, priceNum) {
        if (!priceNum || !Number.isFinite(priceNum) || priceNum <= 0) {
            showToast("Введите корректную цену");
            return;
        }

        try {
            const version = await _resolveLeadVersion(lead);
            if (!version) {
                _showStaleLeadToast();
                return;
            }

            const updatedLead = await requestJson(`/api/v1/leads/${lead.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "sold",
                    sold_price_byn: priceNum,
                    version,
                }),
            });

            const leadInState = state.leads.items.find((l) => l.id === lead.id);
            if (leadInState) {
                Object.assign(leadInState, updatedLead);
            }
            renderLeads();

            const priceBynRaw = lead.price_byn || 0;
            const profit = priceNum - priceBynRaw;
            const profitSign = profit >= 0 ? "+" : "";
            showToast(`Сделка продана: ${profitSign}${Math.round(profit)} BYN`);
        } catch (err) {
            showToast(err.message || "Не удалось отметить сделку как проданную");
            await loadLeads();
        }
    }

    // ── Open lead detail modal ───────────────────────────────────────────
    async function openLeadDetail(lead) {
        if (!lead?.ad_id) {
            showToast("Не удалось открыть: нет ID объявления");
            return;
        }
        const queryToUse = lead.query || state.search.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        // Abort previous in-flight lead detail request
        if (_leadDetailAbortController) {
            _leadDetailAbortController.abort();
        }
        _leadDetailAbortController = new AbortController();
        const signal = _leadDetailAbortController.signal;

        // Shared stale-response guard with openListingDetail and
        // openWatchlistDetail — only the latest tap wins, older
        // listing-detail responses are dropped.
        const requestId = (state.detail._requestId =
            (state.detail._requestId + 1) % 1_000_000);

        const loadingToast = showToast("Загружаю...", "info", 1400);
        state.ui.error = null;
        renderError();
        try {
            const catParam = state.filters.category != null ? `&category=${state.filters.category}` : "";
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.misc.currency}&strict_search=${state.search.strictSearch}&ad_id=${lead.ad_id}${catParam}`,
                { signal }
            );
            if (requestId !== state.detail._requestId) return;
            state.detail.data = fullDetail;
            state.detail.imageIndex = 0;
            state.detail.fromWatchlist = false;
            state.detail.ai = {
                adId: fullDetail.ad_id || lead.ad_id,
                loading: false,
                result: null,
                error: "",
                source: "",
            };
            renderDetailModal();
        } catch (error) {
            if (error.name === "AbortError") {
                return;
            }
            if (requestId !== state.detail._requestId) return;
            state.ui.error = error.message || "Не удалось загрузить детали";
            renderError();
        } finally {
            if (loadingToast) dismissToast(loadingToast);
        }
    }

    return {
        loadLeads,
        loadAnalytics,
        clearAllLeads,
        confirmLead,
        cancelLead,
        closeDeal,
        revertLeadStage,
        deleteLead,
        updateLeadMeta,
        updateLeadStatus,
        markLeadAsSold,
        openLeadDetail,
    };
}


;
/**
 * api_watchlist.js — Watchlist (favorites) CRUD, promotion to leads, and detail.
 *
 * Manages the watchlist: load, add items, update meta/status, promote to leads,
 * open detail, delete items (single or all), and manual refresh.
 */

function createApiWatchlist(context) {
    const {
        state,
        elements,
        hasTelegramInitData,
        renderAll,
        renderError,
        renderWatchlist,
        renderLeads,
        renderDetailModal,
        showToast,
        dismissToast,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        buildCommonQuery,
    } = context;

    let _watchlistDetailAbortController = null;

    // After watchlist mutations we need to refresh BOTH renders. The
    // legacy "Избранное" view still binds renderWatchlist, while the
    // unified "Мои объявления" tab binds renderLeads which itself reads
    // from state.watchlist for the "Избранное" tab. Without this, a
    // delete/promote leaves the card visible until the user switches tabs.
    function refreshAfterWatchlistChange() {
        if (typeof renderWatchlist === "function") renderWatchlist();
        if (typeof renderLeads === "function") renderLeads();
    }

    // Tracks ad_ids and watchlist row ids with an in-flight mutation so
    // a rapid double-click doesn't fire two POST/PATCH/DELETE for the
    // same row. The backend is race-safe (savepoint + 409), but the
    // user otherwise sees doubled toasts and one round-trip is wasted.
    const _inflightAd = new Set();
    const _inflightWatchId = new Set();

    // FE-M14: ``INFLIGHT_GUARD_MS`` lives in dom_helpers.js so the
    // dedupe window matches the cross-pipeline guard in app_actions —
    // a "Добавить" click and a "Удалить" on the same row can't race
    // past each other regardless of which surface fired first.
    function _guardInflightAd(adId) {
        _inflightAd.add(adId);
        setTimeout(() => _inflightAd.delete(adId), INFLIGHT_GUARD_MS);
    }
    function _guardInflightWatchId(watchId) {
        _inflightWatchId.add(watchId);
        setTimeout(() => _inflightWatchId.delete(watchId), INFLIGHT_GUARD_MS);
    }

    function _validVersion(value) {
        const numeric = Number(value);
        return Number.isInteger(numeric) && numeric >= 1 ? numeric : null;
    }

    function _findWatchlistSnapshot(itemOrId) {
        const itemId = typeof itemOrId === "object" ? itemOrId?.id : itemOrId;
        return state.watchlist.items.find((w) => w.id === itemId) || null;
    }

    async function _resolveWatchlistVersion(itemOrId) {
        const snapshot = _findWatchlistSnapshot(itemOrId)
            || (typeof itemOrId === "object" ? itemOrId : null);
        const version = _validVersion(snapshot?.version);
        if (version) return version;
        await loadWatchlist();
        return _validVersion(_findWatchlistSnapshot(itemOrId)?.version);
    }

    function _showStaleWatchlistToast() {
        showToast("Данные устарели. Обновите список и попробуйте ещё раз.", "error");
    }

    // ── Load watchlist ───────────────────────────────────────────────────
    async function loadWatchlist() {
        if (!hasTelegramInitData()) {
            state.watchlist.items = [];
            refreshAfterWatchlistChange();
            return;
        }
        // Show skeleton cards while loading
        state.watchlist.items = [];
        state.watchlist._loading = true;
        refreshAfterWatchlistChange();

        // Stale-response guard — same pattern as loadLeads(). Watchlist
        // and leads share the same lead_items table, so a stale
        // watchlist GET arriving after a promote/delete can resurrect
        // a row that no longer belongs there.
        const requestId = (state.watchlist._requestId =
            (state.watchlist._requestId + 1) % 1_000_000);
        let nextWatchlist;
        try {
            nextWatchlist = await getJson("/api/v1/watchlist");
        } catch (_) {
            nextWatchlist = [];
        }
        if (requestId !== state.watchlist._requestId) return;
        state.watchlist._loading = false;
        state.watchlist.items = nextWatchlist;
        refreshAfterWatchlistChange();
    }

    // ── Clear entire watchlist ───────────────────────────────────────────
    async function clearAllWatchlist() {
        if (!state.watchlist.items.length) {
            showToast("Список уже пуст");
            return;
        }
        try {
            await deleteJson("/api/v1/watchlist/all");
            const count = state.watchlist.items.length;
            state.watchlist.items = [];
            await loadWatchlist();
            showToast(`Удалено ${count} лотов`);
        } catch (error) {
            showToast(error.message || "Не удалось очистить список");
            await loadWatchlist();
        }
    }

    // ── Add item to watchlist from a listing ─────────────────────────────
    async function addWatchlistFromListing(item, queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }
        if (_inflightAd.has(item.ad_id)) {
            return;
        }

        // Check both surfaces — after the watchlist→leads merge a single
        // ad_id can only be in ONE state at a time.
        const ACTIVE_LEAD_STATUSES = new Set([
            "new",
            "in_progress",
            "researching",
            "bought",
            "sold",
        ]);
        const alreadyInWatchlist = state.watchlist.items.some((w) => w.ad_id === item.ad_id);
        if (alreadyInWatchlist) {
            showToast("Уже в избранном");
            return;
        }
        const alreadyInLeads = state.leads.items.some(
            (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
        );
        if (alreadyInLeads) {
            showToast("Уже в покупках");
            return;
        }

        _guardInflightAd(item.ad_id);
        const pendingToast = showToast("Добавляю…", "info", 8000);
        try {
            await postJson("/api/v1/watchlist", {
                query: queryOverride || state.search.query || "",
                ad_id: item.ad_id,
                title: item.title,
                link: item.link,
                price_byn: item.price_byn,
                thumbnail: item.thumbnail || null,
                market_median_byn: state.misc.stats?.median ? Number(state.misc.stats.median) : null,
            });
            if (pendingToast) dismissToast(pendingToast);
            showToast("В избранном", "success", 1600);
            await loadWatchlist();
        } catch (error) {
            if (pendingToast) dismissToast(pendingToast);
            // Server returns 409 with detail "Этот лот уже в покупках"
            // when the ad already has a non-watching lead. Surface a
            // friendly toast instead of the generic "internal error".
            const message = error?.message || "";
            if (/уже\s+в\s+покупках/i.test(message)) {
                showToast("Уже в покупках");
                // Make sure UI reflects reality.
                if (typeof context.loadLeads === "function") {
                    await context.loadLeads();
                }
                return;
            }
            showToast(message || "Не удалось добавить в избранное", "error");
        } finally {
            _inflightAd.delete(item.ad_id);
        }
    }

    // ── Update watchlist item metadata ───────────────────────────────────
    async function updateWatchlistMeta(watchlistId, payload) {
        try {
            const item = state.watchlist.items.find((w) => w.id === watchlistId);
            const version = payload.version ?? await _resolveWatchlistVersion(item || watchlistId);
            if (!version) {
                _showStaleWatchlistToast();
                return;
            }
            await requestJson(`/api/v1/watchlist/${watchlistId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    ...payload,
                    version,
                }),
            });
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось обновить");
        }
    }

    async function updateWatchlistStatus(watchlistId, workflowStatus) {
        await updateWatchlistMeta(watchlistId, { workflow_status: workflowStatus });
    }

    // ── Promote watchlist item to lead ───────────────────────────────────
    async function promoteWatchlistToLead(item) {
        if (!item?.id || !item?.ad_id) {
            return;
        }
        if (_inflightWatchId.has(item.id)) {
            return;
        }

        // Watchlist items live in the same lead_items table after the
        // 20260427_0001 merge — promotion is a pure status transition,
        // no INSERT + DELETE dance needed.
        const ACTIVE_LEAD_STATUSES = new Set([
            "new",
            "in_progress",
            "researching",
            "bought",
            "sold",
        ]);
        const alreadyInLeads = state.leads.items.some(
            (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
        );
        if (alreadyInLeads) {
            showToast("Уже в покупках");
            return;
        }

        _guardInflightWatchId(item.id);
        const pendingToast = showToast("Добавляю…", "info", 8000);
        try {
            const version = await _resolveWatchlistVersion(item);
            if (!version) {
                if (pendingToast) dismissToast(pendingToast);
                _showStaleWatchlistToast();
                return;
            }
            await requestJson(`/api/v1/leads/${item.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: "new",
                    version,
                }),
            });
            if (pendingToast) dismissToast(pendingToast);
            showToast("В покупках", "success", 1600);
            // Optimistic local state cleanup so the UI reflects the move
            // immediately, even before the parallel reloads finish.
            state.watchlist.items = state.watchlist.items.filter((w) => w.id !== item.id);
            refreshAfterWatchlistChange();
            await Promise.all([
                loadWatchlist(),
                typeof context.loadLeads === "function" ? context.loadLeads() : null,
            ]);
        } catch (error) {
            if (pendingToast) dismissToast(pendingToast);
            showToast(error?.message || "Не удалось перевести в покупки", "error");
        } finally {
            _inflightWatchId.delete(item.id);
        }
    }

    // ── Open watchlist item detail modal ─────────────────────────────────
    async function openWatchlistDetail(item) {
        if (!item?.ad_id) {
            return;
        }
        const queryToUse = item.query || state.search.query || "";
        if (!queryToUse) {
            showToast("Не удалось открыть: нет привязки к запросу");
            return;
        }

        // Abort previous in-flight watchlist detail request
        if (_watchlistDetailAbortController) {
            _watchlistDetailAbortController.abort();
        }
        _watchlistDetailAbortController = new AbortController();
        const signal = _watchlistDetailAbortController.signal;

        // Shared stale-response guard with openListingDetail and
        // openLeadDetail — older detail responses are dropped.
        const requestId = (state.detail._requestId =
            (state.detail._requestId + 1) % 1_000_000);

        const loadingToast = showToast("Загружаю...", "info", 1400);
        state.ui.error = null;
        renderError();
        try {
            const catParam = state.filters.category != null ? `&category=${state.filters.category}` : "";
            const fullDetail = await getJson(
                `/api/v1/listing-detail?query=${encodeURIComponent(queryToUse)}&currency=${state.misc.currency}&strict_search=${state.search.strictSearch}&ad_id=${item.ad_id}${catParam}`,
                { signal }
            );
            if (requestId !== state.detail._requestId) return;
            state.detail.data = fullDetail;
            state.detail.imageIndex = 0;
            state.detail.fromWatchlist = true;
            state.detail.ai = {
                adId: fullDetail.ad_id || item.ad_id,
                loading: false,
                result: null,
                error: "",
                source: "",
            };
            renderDetailModal();
        } catch (error) {
            if (error.name === "AbortError") {
                return;
            }
            if (requestId !== state.detail._requestId) return;
            state.ui.error = error.message || "Не удалось загрузить детали";
            renderError();
        } finally {
            if (loadingToast) dismissToast(loadingToast);
        }
    }

    // ── Delete single watchlist item ─────────────────────────────────────
    async function deleteWatchlistItem(watchlistId) {
        if (_inflightWatchId.has(watchlistId)) {
            return;
        }
        _guardInflightWatchId(watchlistId);
        // Optimistic remove so the card disappears immediately even
        // when the network is slow. The server-side DELETE is
        // idempotent (always 204), so a duplicate click later — even
        // after this guard's TTL — is still safe.
        const previousWatchlist = state.watchlist.items;
        state.watchlist.items = state.watchlist.items.filter((w) => w.id !== watchlistId);
        refreshAfterWatchlistChange();
        try {
            await deleteJson(`/api/v1/watchlist/${watchlistId}`);
            // Confirmation toast was missing — users couldn't tell
            // delete actually fired vs the card just animating out.
            showToast("Удалено из избранного", "info");
            await loadWatchlist();
        } catch (error) {
            // Rollback the optimistic removal so the user can see the
            // item didn't actually delete and retry.
            state.watchlist.items = previousWatchlist;
            refreshAfterWatchlistChange();
            showToast(error.message || "Не удалось удалить", "error");
        } finally {
            _inflightWatchId.delete(watchlistId);
        }
    }

    // ── Delete all watchlist ─────────────────────────────────────────────
    async function deleteAllWatchlist() {
        if (!state.watchlist.items.length) {
            showToast("Список уже пуст");
            return;
        }
        const count = state.watchlist.items.length;
        try {
            await deleteJson("/api/v1/watchlist/all");
            state.watchlist.items = [];
            await loadWatchlist();
            showToast(`Удалено ${count} лотов`);
        } catch (error) {
            showToast(error.message || "Не удалось очистить");
            await loadWatchlist();
        }
    }

    // ── Refresh watchlist (server-side price check) ──────────────────────
    async function refreshWatchlist() {
        const button = elements.watchlistSection?.querySelector('[data-role="refresh-watchlist"]');
        let originalText = "";
        if (button) {
            originalText = button.textContent;
            button.disabled = true;
            button.classList.add('is-loading');
            button.textContent = 'Обновляю...';
        }
        try {
            const payload = await postJson("/api/v1/watchlist/refresh", {});
            const parts = [`${payload.updated} проверено`, `${payload.price_drops} падений цены`];
            if (payload.missing > 0) {
                parts.push(`${payload.missing} пропало`);
            }
            if (payload.auto_removed > 0) {
                parts.push(`${payload.auto_removed} удалено (устарело)`);
            }
            showToast(`Обновлено: ${parts.join(", ")}`);
            await loadWatchlist();
        } catch (error) {
            showToast(error.message || "Не удалось обновить цены");
        } finally {
            if (button) {
                button.disabled = false;
                button.classList.remove('is-loading');
                button.textContent = originalText;
            }
        }
    }

    return {
        loadWatchlist,
        clearAllWatchlist,
        addWatchlistFromListing,
        updateWatchlistMeta,
        updateWatchlistStatus,
        promoteWatchlistToLead,
        openWatchlistDetail,
        deleteWatchlistItem,
        deleteAllWatchlist,
        refreshWatchlist,
    };
}


;
/**
 * api_events.js — All DOM event binding for the application.
 *
 * This module wires up every interactive element: search inputs, buttons,
 * chips, tabs, toggles, modals, filters, swipes, keyboard shortcuts, and
 * cross-app lifecycle triggers. It delegates all business logic to the action
 * functions passed through context.
 */

function _haptic(type = "light") {
    try { _tgHaptic()?.impactOccurred?.(type); } catch (_) {}
}

function createApiEvents(context) {
    const {
        state,
        elements,
        trapFocus,
        renderAll,
        renderLoading,
        renderStrictSearch,
        renderSortButtons,
        renderDiscountButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderHistory,
        renderProfitDashboard,
        renderTrackerEventFilters,
        renderTrackerEvents,
        renderTrackingHeroStats,
        renderLeads,
        renderWatchlist,
        setActiveView,
        setPanelOpen,
        closeDetailModal,
        closeEditTracker,
        closeExpensesModal,
        showToast,
        buildCommonQuery,
        getJson,
        postJson,
        deleteJson,
        requestJson,
        // Action functions delegated from other modules
        search,
        loadListings,
        loadHistory,
        loadTrackers,
        loadLeads,
        loadAnalytics,
        clearAllLeads,
        loadWatchlist,
        clearAllWatchlist,
        createTracker,
        deleteTracker,
        pauseTracker,
        resumeTracker,
        openEditTracker,
        closeEditTracker: closeEditTrackerAction,
        saveTracker,
        openListingDetail,
        openLeadDetail,
        openWatchlistDetail,
        confirmLead,
        cancelLead,
        closeDeal,
        revertLeadStage,
        deleteLead,
        markLeadAsSold,
        updateWatchlistStatus,
        promoteWatchlistToLead,
        deleteWatchlistItem,
        deleteAllWatchlist,
        refreshWatchlist,
        refreshLeads,
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeadsCSV,
        loadAIAnalysis,
        closeAIModal,
        startTrackerRefresh,
        stopTrackerRefresh,
    } = context;

    // ── Event binding ────────────────────────────────────────────────────
    const _searchDebounce = { timer: null };
    const _confirmTimers = {};
    const _preloadCache = [];
    let _filterCloseTimeout = null;

    function bindSearchEvents() {
        // ── Search input ─────────────────────────────────────────────
        elements.searchInput?.addEventListener("input", () => {
            state.search.query = elements.searchInput.value.trim();
            renderLoading();

            clearTimeout(_searchDebounce.timer);
            _searchDebounce.timer = setTimeout(() => {
                if (state.search.query.length >= 2) {
                    void search("overview");

                    _haptic("light");
                }
            }, 1200); // 1.2s debounce — gives users time to finish typing
        });

        elements.searchInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                clearTimeout(_searchDebounce.timer);
                void search("overview");

                _haptic("medium");
            }
        });

        elements.searchButton?.addEventListener("click", () => {
            clearTimeout(_searchDebounce.timer);
            void search("overview");

            _haptic("medium");
        });

        // ── Error bar retry ────────────────────────────────────────────
        elements.errorRetry?.addEventListener("click", () => {
            state.ui.error = null;
            void search(state.ui.activeView || "overview");
        });
    }

    function bindRecentSearchEvents() {
        // ── Recent searches (chips + clear button) ────────────────────
        elements.recentSection?.addEventListener("click", (event) => {
            if (event.target.closest("#recent-clear-btn")) {
                // 1. Instant visual feedback — clear state and hide immediately
                state.search.recentSearches = [];

                // 2. Defer localStorage write to next tick (non-blocking)
                requestAnimationFrame(() => {
                    context.saveRecentSearches();
                });

                // 3. Don't call renderRecentSearches() — we handle it directly
                // to avoid double-render and ensure instant response
                if (elements.recentSection) elements.recentSection.hidden = true;
                if (elements.recentList) domClear(elements.recentList);

                // 4. Optional haptic feedback
                _haptic("light");

                return;
            }
            const chip = event.target.closest("[data-recent-query]");
            if (chip) {
                const query = chip.dataset.recentQuery || "";
                elements.searchInput.value = query;
                state.search.query = query;
                renderLoading();
                clearTimeout(_searchDebounce.timer);
                void search("overview");
            }
        });

        // ── Welcome / helper panel chips ─────────────────────────────
        elements.helperPanel?.addEventListener("click", (event) => {
            const chip = event.target.closest("[data-recent-query]");
            if (chip) {
                const query = chip.dataset.recentQuery || "";
                if (!query) return;
                elements.searchInput.value = query;
                state.search.query = query;
                renderLoading();
                clearTimeout(_searchDebounce.timer);
                void search("overview");
                return;
            }
            const shortcut = event.target.closest("[data-view-shortcut]");
            if (shortcut) {
                const view = shortcut.dataset.viewShortcut;
                if (view) {
                    setActiveView(view);
                    renderAll();
                    requestAnimationFrame(() => {
                        document
                            .getElementById("listing-assistant-section")
                            ?.scrollIntoView({ behavior: "smooth", block: "start" });
                    });
                }
            }
        });

        // ── Refinement chips (summary strip) ─────────────────────────
        elements.summaryRefinements?.addEventListener("click", (event) => {
            const chip = event.target.closest("[data-refinement]");
            if (!chip) return;
            const token = chip.dataset.refinement || "";
            if (!token) return;
            const current = (state.search.query || "").trim();
            const lowerCurrent = current.toLowerCase();
            const lowerToken = token.toLowerCase();
            // Avoid duplicating the token if it's already in the query.
            const merged = lowerCurrent.includes(lowerToken)
                ? current
                : `${current} ${token}`.trim();
            if (merged === current) return;
            elements.searchInput.value = merged;
            state.search.query = merged;
            renderLoading();
            clearTimeout(_searchDebounce.timer);
            _haptic("light");
            void search("overview");
        });
    }

    function bindViewTabEvents() {
        // ── Strict search toggle ─────────────────────────────────────
        elements.strictSearchToggle?.addEventListener("change", () => {
            state.search.strictSearch = Boolean(elements.strictSearchToggle.checked);
            renderStrictSearch();
            if (state.search.query.trim()) {
                void search(state.ui.activeView);
            }
        });
        // ── View tabs ────────────────────────────────────────────────
        const tablist = document.querySelector('[role="tablist"].view-nav');
        if (tablist) {
            tablist.addEventListener("keydown", (event) => {
                const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
                if (!tabs.length) return;
                const idx = tabs.indexOf(document.activeElement);
                if (idx === -1) return;
                let next = -1;
                if (event.key === "ArrowRight") next = (idx + 1) % tabs.length;
                else if (event.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = tabs.length - 1;
                if (next === -1) return;
                event.preventDefault();
                tabs[next].focus();
                tabs[next].click();
            });
        }
        for (const button of elements.viewTabs || []) {
            button.addEventListener("click", () => {
                const view = button.dataset.view;
                if (!view) {
                    return;
                }
                _preloadCache.length = 0;
                Object.values(_confirmTimers).forEach(clearTimeout);
                _confirmTimers.events = undefined;
                _confirmTimers.leads = undefined;
                _confirmTimers.watchlist = undefined;
                setActiveView(view);
                renderAll();
                if (view === "tracking") {
                    void loadTrackers();
                    startTrackerRefresh();
                } else {
                    stopTrackerRefresh();
                }
                if (view === "deals") {
                    // Unified "Мои объявления" — load both leads and
                    // watchlist (formerly the Избранное view) in parallel
                    // so any filter chip ("Слежу" / "В работе" / …)
                    // has data to render against.
                    void loadLeads();
                    void loadWatchlist();
                }
            });
        }

        // ── Items filter tabs (Избранное / Покупки within deals view) ──
        for (const button of elements.itemsFilterButtons || []) {
            button.addEventListener("click", () => {
                const filter = button.dataset.itemsFilter;
                if (!filter) return;
                state.leads.itemsFilter = filter;
                renderLeads();

                _haptic("light");
            });
        }

        // ── Panel toggles (collapsible sections) ─────────────────────
        for (const button of elements.panelToggles || []) {
            button.addEventListener("click", () => {
                const panelName = button.dataset.panelToggle;
                if (!panelName) {
                    return;
                }
                setPanelOpen(panelName, !state.panels[panelName]);
            });
        }

        // ── History range buttons ────────────────────────────────────
        for (const button of elements.historyRangeButtons || []) {
            button.addEventListener("click", () => {
                const nextDays = Number(button.dataset.historyDays);
                if (!nextDays || nextDays === state.misc.historyDays) {
                    return;
                }
                state.misc.historyDays = nextDays;
                renderHistory();
                void loadHistory();
            });
        }

        // ── Analytics period chips (30 дн / 90 дн / Год) ────────────
        for (const button of elements.analyticsPeriodButtons || []) {
            button.addEventListener("click", () => {
                const nextDays = Number(button.dataset.analyticsPeriod);
                if (!nextDays || nextDays === state.analytics.periodDays) {
                    return;
                }
                state.analytics.periodDays = nextDays;
                renderProfitDashboard();
                void loadAnalytics();
            });
        }

        // ── Sort buttons ─────────────────────────────────────────────
        for (const button of elements.sortButtons || []) {
            button.addEventListener("click", () => {
                const sort = button.dataset.sort || "newest";
                if (sort === state.search.sort) {
                    return;
                }
                state.search.sort = sort;
                renderSortButtons();
                // Discount-range controls live below the sort row in the
                // ads view. They're only meaningful for sort=cheap; the
                // hidden attribute toggles their visibility in sync with
                // the sort selection.
                if (elements.dealsControls) {
                    elements.dealsControls.hidden = sort !== "cheap";
                }
                if (state.search.query.trim()) {
                    void loadListings(true);
                }
            });
        }
    }

    function bindFilterEvents() {
        // ── Filter button (toggle dropdown) ───────────────────────────
        elements.filterBtn?.addEventListener("click", () => {
            const prefersReducedMotion = _prefersReducedMotion();
            if (!state.filters.filterDropdownOpen) {
                if (_filterCloseTimeout) { clearTimeout(_filterCloseTimeout); _filterCloseTimeout = null; }
                state.filters.pendingCategory = state.filters.category;
                state.filters.pendingCondition = state.filters.condition;
                state.filters.pendingSellerType = state.filters.sellerType;
                state.filters.pendingMinPrice = state.filters.minPrice;
                state.filters.pendingMaxPrice = state.filters.maxPrice;
                state.filters.pendingRegionName = state.filters.regionName;
                state.filters.filterDropdownOpen = true;
                renderAll();
            } else if (prefersReducedMotion) {
                state.filters.filterDropdownOpen = false;
                renderAll();
            } else {
                elements.filterDropdown?.classList.add("closing");
                _filterCloseTimeout = setTimeout(() => {
                    _filterCloseTimeout = null;
                    elements.filterDropdown?.classList.remove("closing");
                    state.filters.filterDropdownOpen = false;
                    renderAll();
                }, 150);
            }

            _haptic("light");
        });

        // Close filter dropdown when clicking outside
        document.addEventListener("click", (event) => {
            if (!state.filters.filterDropdownOpen) return;
            const dropdown = elements.filterDropdown;
            const btn = elements.filterBtn;
            const prefersReducedMotion = _prefersReducedMotion();
            if (dropdown && !dropdown.hidden && !dropdown.contains(event.target) && btn && !btn.contains(event.target)) {
                if (prefersReducedMotion) {
                    state.filters.filterDropdownOpen = false;
                    renderAll();
                } else {
                    dropdown.classList.add("closing");
                    if (_filterCloseTimeout) clearTimeout(_filterCloseTimeout);
                    _filterCloseTimeout = setTimeout(() => {
                        _filterCloseTimeout = null;
                        dropdown.classList.remove("closing");
                        state.filters.filterDropdownOpen = false;
                        renderAll();
                    }, 150);
                }
            }
        });

        // Prevent clicks inside dropdown from closing it
        elements.filterDropdown?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: category chips (event delegation) ────────
        elements.filterCategories?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-category]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            const rawValue = button.dataset.category;
            const newCategory = rawValue === "" ? null : Number(rawValue);

            // Update pending value and re-render to show selection
            state.filters.pendingCategory = newCategory;
            renderAll();

            _haptic("light");
        });

        // ── Filter dropdown: condition chips (event delegation) ───────
        elements.filterConditions?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-condition]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.filters.pendingCondition = button.dataset.condition;
            renderAll();

            _haptic("light");
        });

        // ── Filter dropdown: seller chips (event delegation) ──────────
        elements.filterSellers?.addEventListener("click", (event) => {
            const button = event.target.closest("[data-seller]");
            if (!button) return;

            event.stopPropagation(); // Prevent dropdown from closing
            // Update pending value and re-render to show selection
            state.filters.pendingSellerType = button.dataset.seller;
            renderAll();

            _haptic("light");
        });

        // ── Filter dropdown: price range inputs ──────────────────────
        elements.filterMinPrice?.addEventListener("input", () => {
            const value = elements.filterMinPrice.value.trim();
            state.filters.pendingMinPrice = value === "" ? null : Math.max(0, Number(value));
        });

        elements.filterMinPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        elements.filterMaxPrice?.addEventListener("input", () => {
            const value = elements.filterMaxPrice.value.trim();
            state.filters.pendingMaxPrice = value === "" ? null : Math.max(0, Number(value));
        });

        elements.filterMaxPrice?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: region select ─────────────────────────────
        elements.filterRegion?.addEventListener("change", () => {
            state.filters.pendingRegionName = elements.filterRegion.value;
        });

        elements.filterRegion?.addEventListener("click", (event) => {
            event.stopPropagation();
        });

        // ── Filter dropdown: Apply button ─────────────────────────────
        elements.filterApplyBtn?.addEventListener("click", () => {
            // Check if category changed to trigger search
            const categoryChanged = state.filters.pendingCategory !== state.filters.category;
            
            // Apply pending filter values
            state.filters.category = state.filters.pendingCategory;
            state.filters.condition = state.filters.pendingCondition;
            state.filters.sellerType = state.filters.pendingSellerType;
            state.filters.minPrice = state.filters.pendingMinPrice;
            state.filters.maxPrice = state.filters.pendingMaxPrice;
            state.filters.regionName = state.filters.pendingRegionName;
            state.filters.filterDropdownOpen = false;
            renderAll();

            // If category changed, trigger new search keeping the
            // freshly-applied filters (otherwise search() would wipe
            // the user's selection).
            if (categoryChanged && state.search.query.trim()) {
                void search(state.ui.activeView, { keepFilters: true });
            }

            _haptic("medium");
        });

        // ── Filter dropdown: Cancel button ────────────────────────────
        elements.filterCancelBtn?.addEventListener("click", () => {
            // Reset pending values to current applied values
            state.filters.pendingCategory = state.filters.category;
            state.filters.pendingCondition = state.filters.condition;
            state.filters.pendingSellerType = state.filters.sellerType;
            state.filters.pendingMinPrice = state.filters.minPrice;
            state.filters.pendingMaxPrice = state.filters.maxPrice;
            state.filters.pendingRegionName = state.filters.regionName;
            state.filters.filterDropdownOpen = false;
            renderAll();

            _haptic("light");
        });
    }

    function bindDiscountEvents() {
        // ── Discount buttons ─────────────────────────────────────────
        // Discount range presets (10-20%, 10-30%, …) and the manual
        // From/To inputs both used to live in their own "Выгодно"
        // view that fired loadDeals(). The view is gone — they now
        // sit inside the ads view and trigger loadListings(true) with
        // sort=cheap so the discount filter is applied to the same
        // listings list the user is already looking at.
        function _activateCheapSort() {
            state.search.sort = "cheap";
            renderSortButtons();
            if (elements.dealsControls) {
                elements.dealsControls.hidden = false;
            }
            setActiveView("ads");
            if (state.search.query.trim()) {
                void loadListings(true);
            }
        }

        for (const button of elements.discountButtons || []) {
            button.addEventListener("click", () => {
                const from = Number(button.dataset.discountFrom);
                const to = Number(button.dataset.discountTo);
                if (!Number.isFinite(from) || !Number.isFinite(to)) {
                    return;
                }
                state.filters.discountFromPercent = Math.min(from, to);
                state.filters.discountToPercent = Math.max(from, to);
                renderDiscountButtons();
                renderDealInputs();
                _activateCheapSort();
            });
        }

        // ── Discount apply button ────────────────────────────────────
        elements.dealApplyButton?.addEventListener("click", () => {
            const from = Math.abs(Number(elements.dealFromInput?.value || state.filters.discountFromPercent));
            const to = Math.abs(Number(elements.dealToInput?.value || state.filters.discountToPercent));
            state.filters.discountFromPercent = Math.min(from, to);
            state.filters.discountToPercent = Math.max(from, to);
            renderDiscountButtons();
            renderDealInputs();
            _activateCheapSort();
        });
    }

    function bindTrackerEvents() {
        // ── Tracker create button ────────────────────────────────────
        elements.trackQueryButton?.addEventListener("click", () => {
            const button = elements.trackQueryButton;
            button.disabled = true;
            button.classList.add('is-loading');
            const originalText = button.textContent;
            button.textContent = 'Создаю...';
            void (async () => {
                try {
                    await createTracker();
                } finally {
                    button.disabled = false;
                    button.classList.remove('is-loading');
                    button.textContent = originalText;
                }
            })().catch((err) => { console.error("tracker create failed", err); });
        });

        // ── Clear events button (double-confirm) ─────────────────────
        let clearEventsConfirmed = false;
        elements.clearEventsButton?.addEventListener("click", () => {
            void (async () => {
                if (state.trackers.events.length === 0) {
                    showToast("Нет событий для удаления");
                    return;
                }

                if (!clearEventsConfirmed) {
                    clearEventsConfirmed = true;
                    elements.clearEventsButton.textContent = "Удалить все?";
                    _confirmTimers.events = setTimeout(() => {
                        clearEventsConfirmed = false;
                        if (elements.clearEventsButton) {
                            elements.clearEventsButton.textContent = "Очистить";
                        }
                    }, 3000);
                    showToast("Нажмите ещё раз для подтверждения");
                    return;
                }
                clearEventsConfirmed = false;
                if (elements.clearEventsButton) {
                    elements.clearEventsButton.textContent = "Очистить";
                }
                try {
                    await deleteJson("/api/v1/tracker-events");
                } catch (_) {
                    // ignore — clear locally anyway
                }
                state.trackers.events = [];
                showToast("События очищены");
                renderTrackerEvents();
                // Refresh the "N событий" hero badge so the count drops
                // to 0 immediately instead of waiting for a page reload.
                if (typeof renderTrackingHeroStats === "function") {
                    renderTrackingHeroStats();
                }
            })();
        });

        // ── Clear all leads button (double-confirm) ──────────────────
        let clearLeadsConfirmed = false;
        elements.clearAllLeadsButton?.addEventListener("click", () => {
            void (async () => {
                const activeLeads = state.leads.items.filter((l) => l.status !== "closed");
                if (activeLeads.length === 0) {
                    showToast("Нет активных сделок для удаления");
                    return;
                }
                if (!clearLeadsConfirmed) {
                    clearLeadsConfirmed = true;
                    elements.clearAllLeadsButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    _confirmTimers.leads = setTimeout(() => {
                        clearLeadsConfirmed = false;
                        if (elements.clearAllLeadsButton) {
                            elements.clearAllLeadsButton.textContent = "Очистить";
                        }
                    }, 3000);
                    return;
                }
                clearLeadsConfirmed = false;
                if (elements.clearAllLeadsButton) {
                    elements.clearAllLeadsButton.textContent = "Очистить";
                }
                await clearAllLeads();
            })();
        });

        // ── Clear all watchlist button (double-confirm) ──────────────
        let clearWatchlistConfirmed = false;
        elements.deleteAllWatchlistButton?.addEventListener("click", () => {
            void (async () => {
                if (state.watchlist.items.length === 0) {
                    showToast("Список уже пуст");
                    return;
                }
                if (!clearWatchlistConfirmed) {
                    clearWatchlistConfirmed = true;
                    elements.deleteAllWatchlistButton.textContent = "Удалить все?";
                    showToast("Нажмите ещё раз для подтверждения");
                    _confirmTimers.watchlist = setTimeout(() => {
                        clearWatchlistConfirmed = false;
                        if (elements.deleteAllWatchlistButton) {
                            elements.deleteAllWatchlistButton.textContent = "Очистить";
                        }
                    }, 3000);
                    return;
                }
                clearWatchlistConfirmed = false;
                if (elements.deleteAllWatchlistButton) {
                    elements.deleteAllWatchlistButton.textContent = "Очистить";
                }
                await clearAllWatchlist();
            })();
        });

        // ── Tracker filter inputs ────────────────────────────────────
        elements.trackerMinDiscountInput?.addEventListener("input", () => {
            const nextValue = Number(elements.trackerMinDiscountInput.value);
            state.trackers.minDiscountPercent = Number.isFinite(nextValue) ? Math.abs(nextValue) : 10;
        });

        elements.trackerMaxPriceInput?.addEventListener("input", () => {
            const rawValue = elements.trackerMaxPriceInput.value.trim();
            if (!rawValue) {
                state.trackers.maxPriceByn = null;
                return;
            }
            const nextValue = Number(rawValue);
            state.trackers.maxPriceByn = Number.isFinite(nextValue) ? Math.abs(nextValue) : null;
        });

        elements.trackerSellerSelect?.addEventListener("change", () => {
            state.trackers.sellerType = elements.trackerSellerSelect.value;
        });

        elements.trackerConditionSelect?.addEventListener("change", () => {
            state.trackers.condition = elements.trackerConditionSelect.value;
        });

        elements.trackerRegionSelect?.addEventListener("change", () => {
            state.trackers.regionName = elements.trackerRegionSelect.value;
        });

        elements.trackerConfigInput?.addEventListener("input", () => {
            state.trackers.configKeyword = elements.trackerConfigInput.value.trim();
        });

        // ── Edit tracker modal ───────────────────────────────────────
        elements.closeEditModal?.addEventListener("click", () => {
            closeEditTrackerAction();
        });
        elements.cancelEditBtn?.addEventListener("click", () => {
            closeEditTrackerAction();
        });
        elements.saveTrackerBtn?.addEventListener("click", () => {
            const button = elements.saveTrackerBtn;
            button.disabled = true;
            button.classList.add('is-loading');
            const originalText = button.textContent;
            button.textContent = 'Сохраняю...';
            void (async () => {
                try {
                    await saveTracker();
                } finally {
                    button.disabled = false;
                    button.classList.remove('is-loading');
                    button.textContent = originalText;
                }
            })().catch((err) => { console.error("save tracker failed", err); });
        });

        elements.editTrackerModal?.addEventListener("click", (event) => {
            if (event.target === elements.editTrackerModal) {
                closeEditTrackerAction();
            }
        });

        // ── Tracker event filter buttons ─────────────────────────────
        for (const button of elements.trackerEventFilterButtons || []) {
            button.addEventListener("click", () => {
                state.trackers.eventFilter = button.dataset.eventFilter || "all";
                renderTrackerEventFilters();
                renderTrackerEvents();
            });
        }
    }

    function bindModalEvents() {
        // ── Detail modal ─────────────────────────────────────────────
        elements.detailClose?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailOverlay?.addEventListener("click", () => {
            closeDetailModal();
        });

        elements.detailAddLeadButton?.addEventListener("click", () => {
            if (state.detail.data) {
                void context.addLeadFromListing(state.detail.data, "detail_modal", state.detail.data.query || state.search.query);
            }
        });

        elements.detailAddWatchlistButton?.addEventListener("click", () => {
            if (state.detail.data) {
                void context.addWatchlistFromListing(state.detail.data, state.detail.data.query || state.search.query);
            }
        });

        elements.detailAiBtn?.addEventListener("click", () => {
            if (state.detail.data?.ad_id) {
                void loadAIAnalysis(state.detail.data.ad_id);
            }
        });

        // ── AI modal close ──
        elements.aiModalClose?.addEventListener("click", () => {
            closeAIModal();
        });
        elements.aiOverlay?.addEventListener("click", () => {
            closeAIModal();
        });

        // ── Escape key (modal close) ─────────────────────────────────
        // Topmost-modal priority. Listing-Assistant deliberately
        // NOT handled here — that module registers its OWN
        // ``keydown`` listener in api_listing_assistant.js which
        // calls its internal ``closeModal()`` (which goes through
        // ``closeModalAnimated`` and therefore runs the full close
        // path: ``is-closing`` animation, ``unlockBodyScroll``,
        // focus-trap cleanup, ``_restoreInertSiblings``).
        //
        // Wave 25.5: the earlier code here had its own LA branch
        // that did ``laModal.hidden = true;`` directly, bypassing
        // every part of that cleanup. Symptom: pressing Esc inside
        // the listing-assistant left ``body.modal-open`` (page
        // scroll-locked), the ``inert`` siblings still inert (no
        // clicks anywhere) and the focus trapped inside the now-
        // hidden modal — UI completely frozen until the user
        // refreshed.
        //
        // FE-04: previously this used an anonymous arrow which made
        // ``bindEvents()`` idempotency impossible — a re-init would
        // pile a second listener on top, and Escape would close two
        // modals at once. Now we hang the handler off ``document``
        // under a sentinel attribute so we can dedupe.
        if (document._kufarEscapeHandler) {
            document.removeEventListener("keydown", document._kufarEscapeHandler);
        }
        const escapeHandler = (event) => {
            if (event.key !== "Escape") return;
            if (!elements.aiModal?.hidden) {
                closeAIModal();
            } else if (!elements.expensesModal?.hidden) {
                closeExpensesModal();
            } else if (state.detail.data) {
                closeDetailModal();
            } else if (!elements.editTrackerModal?.hidden) {
                closeEditTrackerAction();
            }
        };
        document._kufarEscapeHandler = escapeHandler;
        document.addEventListener("keydown", escapeHandler);
    }

    function bindCarouselEvents() {
        // ── Image carousel — minimal opacity-crossfade swipe ──────────
        //
        // Earlier we tried a finger-follow live drag with rAF
        // coalescing and a compositor layer hint. Even with all the
        // tricks the live drag stuttered on Telegram WebView for some
        // users — the cheapest layout-only frame is still 16 ms of
        // composite work and many devices can't keep up at 60 Hz on
        // a full-width image.
        //
        // The simpler approach: don't animate during the gesture at
        // all. Detect the swipe on touchend, then run a 280 ms
        // crossfade with a tiny direction-hint translate (8 px). This
        // is one transition over a fixed time window — no per-frame
        // work, no layer-promotion fight with the page scroll. Looks
        // clean and performs identically on every device.
        //
        // Adjacent images get preloaded so src-swap is instant.
        let isAnimating = false;

        function _preloadAdjacent() {
            const images = state.detail.data?.images || [];
            const idx = state.detail.imageIndex || 0;
            _preloadCache.length = 0;
            for (const i of [idx - 1, idx + 1]) {
                if (i < 0 || i >= images.length) continue;
                const raw = images[i];
                if (typeof raw !== "string" || !raw) continue;
                const validated = (typeof context.safeUrl === "function")
                    ? context.safeUrl(raw)
                    : raw;
                if (!validated) continue;
                const url = (typeof context.optimizedImage === "function")
                    ? context.optimizedImage(validated, { width: 800 })
                    : validated;
                const ghost = new Image();
                ghost.src = url;
                _preloadCache.push(ghost);
            }
        }

        async function navigateDetailImage(direction) {
            const images = state.detail.data?.images;
            if (!images || images.length <= 1) return;
            if (isAnimating) return;

            const total = images.length;
            const newIndex = direction > 0
                ? Math.min(total - 1, state.detail.imageIndex + 1)
                : Math.max(0, state.detail.imageIndex - 1);
            if (newIndex === state.detail.imageIndex) return;

            const img = elements.detailMainImage;
            if (!img) {
                state.detail.imageIndex = newIndex;
                context.renderDetailModal();
                return;
            }
            if (img._pinchController) {
                img._pinchController.reset(false);
            }

            isAnimating = true;

            // Phase 1 — fade old image out + nudge 8 px in swipe dir.
            const exitX = direction > 0 ? -8 : 8;
            img.style.transition = "opacity 140ms ease-out, transform 140ms ease-out";
            img.style.opacity = "0";
            img.style.transform = `translate3d(${exitX}px, 0, 0)`;

            await new Promise((resolve) => setTimeout(resolve, 140));

            // Phase 2 — swap src, pre-position 8 px on the opposite
            // side, then animate to (0, 0) with opacity 1. The
            // opposite-side enter sells the direction of travel; the
            // 8 px is small enough that the composite is trivially
            // cheap on any device.
            state.detail.imageIndex = newIndex;
            context.renderDetailModal();
            _preloadAdjacent();

            const enterX = direction > 0 ? 8 : -8;
            img.style.transition = "none";
            img.style.opacity = "0";
            img.style.transform = `translate3d(${enterX}px, 0, 0)`;
            // Force style flush so the next frame's transition starts
            // from the pre-positioned offset, not the previous one.
            void img.offsetWidth;

            requestAnimationFrame(() => {
                img.style.transition = "opacity 200ms ease-out, transform 220ms ease-out";
                img.style.opacity = "1";
                img.style.transform = "translate3d(0, 0, 0)";
                const release = () => {
                    img.style.transition = "";
                    img.style.opacity = "";
                    img.style.transform = "";
                    isAnimating = false;
                };
                setTimeout(release, 240);
            });
        }

        // ── Detect-on-release swipe ──────────────────────────────────
        // No live drag — we just record the start and check the delta
        // on touchend. Browser scrolls vertically without our
        // interference; we only fire navigateDetailImage when the
        // gesture was clearly horizontal AND past the threshold.
        // Scoped to .detail-media so swiping the thumbnail strip
        // (or any other modal content) never moves the hero.
        const SWIPE_THRESHOLD_PX = 50;
        let touchStartX = 0;
        let touchStartY = 0;
        let touchSkip = false;

        elements.detailMedia?.addEventListener("touchstart", (e) => {
            if (isAnimating) {
                touchSkip = true;
                return;
            }
            if (e.touches && e.touches.length > 1) {
                touchSkip = true;
                return;
            }
            if (elements.detailMainImage?.classList.contains("is-zoomed")) {
                touchSkip = true;
                return;
            }
            touchSkip = false;
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
        }, { passive: true });

        elements.detailMedia?.addEventListener("touchend", (e) => {
            if (touchSkip) {
                touchSkip = false;
                return;
            }
            const dx = e.changedTouches[0].clientX - touchStartX;
            const dy = e.changedTouches[0].clientY - touchStartY;
            if (Math.abs(dx) < SWIPE_THRESHOLD_PX) return;
            // Vertical swipe wins → don't page the photo.
            if (Math.abs(dy) > Math.abs(dx) * 0.8) return;
            void navigateDetailImage(dx < 0 ? 1 : -1);
        }, { passive: true });

        // Preload adjacent images when the modal first becomes active
        // so the first swipe doesn't show the network delay.
        const _preloadOnOpen = () => {
            if (state.detail.data) _preloadAdjacent();
        };
        elements.detailModal?.addEventListener("transitionend", _preloadOnOpen, { passive: true });

        // Keyboard arrows — only react when the detail modal is open.
        // FE-04: dedupe via the same sentinel-attribute trick used for
        // the global Escape handler — a second bindEvents() must not
        // pile up arrow handlers.
        if (document._kufarArrowHandler) {
            document.removeEventListener("keydown", document._kufarArrowHandler);
        }
        const arrowHandler = (event) => {
            if (!state.detail.data) return;
            if (event.key === "ArrowLeft") {
                void navigateDetailImage(-1);
            } else if (event.key === "ArrowRight") {
                void navigateDetailImage(1);
            }
        };
        document._kufarArrowHandler = arrowHandler;
        document.addEventListener("keydown", arrowHandler);
    }

    function bindExpenseEvents() {
        // ── Expenses modal ───────────────────────────────────────────
        elements.expensesClose?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.expensesOverlay?.addEventListener("click", () => {
            closeExpensesModal();
        });

        elements.saveExpenseButton?.addEventListener("click", () => {
            const leadId = state.expenses.currentLeadId;
            if (!leadId) return;
            const type = elements.expenseTypeSelect?.value || "other";
            const rawAmount = elements.expenseAmountInput?.value?.trim();
            const displayAmount = rawAmount ? Number(rawAmount) : null;
            const notes = elements.expenseNotesInput?.value?.trim() || "";
            if (!displayAmount || displayAmount <= 0) {
                showToast("Введите корректную сумму");
                return;
            }
            
            const button = elements.saveExpenseButton;
            button.disabled = true;
            button.classList.add('is-loading');
            const originalText = button.textContent;
            button.textContent = 'Сохраняю...';
            void (async () => {
                try {
                    await createExpense(leadId, { expense_type: type, amount_byn: displayAmount, notes });
                    if (elements.expenseAmountInput) elements.expenseAmountInput.value = "";
                    if (elements.expenseNotesInput) elements.expenseNotesInput.value = "";
                } finally {
                    button.disabled = false;
                    button.classList.remove('is-loading');
                    button.textContent = originalText;
                }
            })().catch((err) => { console.error("save expense failed", err); });
        });

        elements.cancelExpenseButton?.addEventListener("click", () => {
            closeExpensesModal();
        });
    }

    function bindVisibilityEvents() {
        // ── External link interception (Telegram Mini App mobile) ────
        document.addEventListener("click", (event) => {
            const link = event.target.closest("a[target='_blank']");
            if (link && link.href && !link.href.startsWith("#") && !link.href.startsWith("javascript:")) {
                event.preventDefault();
                event.stopPropagation();

                if (window.Telegram?.WebApp?.openLink) {
                    window.Telegram.WebApp.openLink(link.href);
                } else {
                    window.open(link.href, "_blank", "noopener,noreferrer");
                }
            }
        });

        // ── Visibility change (pause/resume tracker refresh) ─────────
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) {
                stopTrackerRefresh();
            } else if (state.ui.activeView === "tracking") {
                startTrackerRefresh();
            }
        });
    }

    function bindEvents() {
        bindSearchEvents();
        bindRecentSearchEvents();
        bindViewTabEvents();
        bindFilterEvents();
        bindDiscountEvents();
        bindTrackerEvents();
        bindModalEvents();
        bindCarouselEvents();
        bindExpenseEvents();
        bindVisibilityEvents();

        // Lazy-load AI modules on first interaction with listing-assistant.
        // The module itself binds its own click handler inside
        // createApiListingAssistant; we intercept the first click to
        // load the scripts, then re-dispatch so the module's handler
        // fires on the next tick.
        const laOpenBtn = document.getElementById("listing-assistant-open-btn");
        laOpenBtn?.addEventListener("click", async function onFirstLaClick(e) {
            e.stopImmediatePropagation();
            await context.ensureAiLoaded();
            laOpenBtn.click();
        }, { once: true });
    }

    return {
        bindEvents,
    };
}


;
/**
 * app_actions.js — Composition entry point for all action / API modules.
 *
 * Delegates to focused modules: api_core, api_listings, api_trackers,
 * api_leads, api_watchlist, api_events.
 *
 * Every function that the original monolithic file exported is still available
 * on the returned object so app.js and renderers continue to work without changes.
 */

function createAppActions(baseContext) {
    const context = { ...baseContext };
    const {
        state,
        elements,
        hasTelegramInitData,
        trapFocus,
        renderAll,
        markDirty,
        renderError,
        renderLoading,
        renderStrictSearch,
        renderViewTabs,
        renderViews,
        renderSortButtons,
        renderDiscountButtons,
        renderDealInputs,
        renderTrackerInputs,
        renderComparison,
        renderHistory,
        renderTrackerStatus,
        renderTrackers,
        renderTrackerEvents,
        renderTrackerEventFilters,
        renderLeads,
        renderWatchlist,
        renderDetailModal,
        closeDetailModal,
        setPanelOpen,
        showToast,
        dismissToast,
        renderExpensesModal,
        openExpensesModal,
        closeExpensesModal,
        renderProfitDashboard,
        renderDealsHeroStats,
    } = context;

    // ── Internal helpers (originally inside the monolithic app_actions) ───
    // These are not part of any module because they bridge multiple modules
    // and the view/scroll lifecycle.

    /**
     * Switches the active view with a subtle enter animation.
     * Updates tab states, triggers view transition, and re-renders view content.
     * Respects prefers-reduced-motion for accessibility.
     *
     * @param {string} view - The view key to activate (e.g., 'overview', 'ads', 'tracking')
     */
    function setActiveView(view) {
        if (!(view in elements.views)) {
            return;
        }
        // No-op when the view is already active. Otherwise an in-page
        // action that happens to re-call setActiveView (e.g. picking a
        // discount preset inside the Объявления view) would run a full
        // renderAll + replay the slide-in animation, making the page
        // look like it's reloading.
        if (state.ui.activeView === view) {
            return;
        }
        state.ui.activeView = view;
        markDirty('tabs', 'views');
        renderAll();

        const viewEl = elements.views[view];
        if (viewEl) {
            const prefersReducedMotion = _prefersReducedMotion();
            if (!prefersReducedMotion) {
                viewEl.classList.add("is-entering");
                setTimeout(() => {
                    viewEl.classList.remove("is-entering");
                }, 200);
            }
        }
    }

    /**
     * Smoothly scrolls a section into view at the top of the viewport.
     *
     * @param {HTMLElement|null} section - The DOM element to scroll to
     */
    function scrollSectionIntoView(section) {
        section?.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    /**
     * Focuses the appropriate view or panel based on the search target.
     * Routes to overview by default, then scrolls to specific sections if needed.
     *
     * "cheap" used to be its own view tab; it's now folded into "ads"
     * with state.search.sort = "cheap" surfacing the discount-range UI.
     *
     * @param {string} target - The target context ('ads', 'cheap', 'deals', 'history')
     */
    function focusTarget(target) {
        if (target === "ads" || target === "cheap") {
            if (target === "cheap") {
                state.search.sort = "cheap";
            }
            setActiveView("ads");
            return;
        }
        if (target === "deals") {
            setActiveView("deals");
            return;
        }

        setActiveView("overview");

        if (target === "history") {
            setPanelOpen("history", true);
            scrollSectionIntoView(elements.historySection);
        }
    }

    // Inject into context so every module can reach them
    context.setActiveView = setActiveView;
    context.scrollSectionIntoView = scrollSectionIntoView;
    context.focusTarget = focusTarget;
    context.markDirty = markDirty;

    // ── Instantiate sub-modules ──────────────────────────────────────────
    const core = createApiCore(context);
    
    // Add core functions to context BEFORE creating other modules
    // This prevents "X is not a function" errors when modules destructure context
    Object.assign(context, {
        telegramHeaders: core.telegramHeaders,
        requestJson: core.requestJson,
        getJson: core.getJson,
        postJson: core.postJson,
        deleteJson: core.deleteJson,
        buildCommonQuery: core.buildCommonQuery,
    });
    
    const listings = createApiListings(context);
    const trackers = createApiTrackers(context);
    const leads = createApiLeads(context);
    // Expose leads.loadLeads on context so watchlist actions can refresh
    // both surfaces atomically when promoting a watching item to a lead.
    context.loadLeads = leads.loadLeads;
    const watchlist = createApiWatchlist(context);
    let _aiModule = null;
    let _listingAssistantModule = null;

    async function ensureAiLoaded() {
        if (_aiModule) return;
        // UX-M8 (Wave 25): api_ai.js was split into 4 files. Load the
        // 3 sub-modules (modal/render/pdf) BEFORE the orchestrator
        // (api_ai.js) so their App namespace registrations are ready
        // by the time createApiAi calls them. They're loaded in
        // parallel and share the same cache-busting version stamp.
        await Promise.all([
            context._loadScript("js/api_ai_modal.js?v=20260511-362e51e"),
            context._loadScript("js/api_ai_render.js?v=20260511-362e51e"),
            context._loadScript("js/api_ai_pdf.js?v=20260511-362e51e"),
            context._loadScript("js/api_ai.js?v=20260511-362e51e"),
            context._loadScript("js/api_listing_assistant.js?v=20260511-362e51e"),
        ]);
        const app = window.App || {};
        if (typeof app.createApiAi !== "function") {
            throw new Error("AI module failed to register");
        }
        _aiModule = app.createApiAi(context);
        _listingAssistantModule = (typeof app.createApiListingAssistant === "function")
            ? app.createApiListingAssistant(context)
            : null;
    }

    async function loadAIAnalysis(...args) {
        await ensureAiLoaded();
        return _aiModule.loadAIAnalysis(...args);
    }

    function closeAIModal() {
        if (_aiModule) _aiModule.closeAIModal();
    }

    // ── Cross-module hooks (actions that modules call into each other) ───
    // These are injected into context so every module can reach them.
    Object.assign(context, {
        // From listings
        search: listings.search,
        loadListings: listings.loadListings,
        loadMoreListings: listings.loadMoreListings,
        openListingDetail: listings.openListingDetail,
        loadSearchDependencies: listings.loadSearchDependencies,
        clearSearchData: listings.clearSearchData,
        loadHistory: listings.loadHistory,

        // From trackers
        loadTrackers: trackers.loadTrackers,
        createTracker: trackers.createTracker,
        pauseTracker: trackers.pauseTracker,
        resumeTracker: trackers.resumeTracker,
        deleteTracker: trackers.deleteTracker,
        openEditTracker: trackers.openEditTracker,
        closeEditTracker: trackers.closeEditTracker,
        saveTracker: trackers.saveTracker,
        refreshTrackerEvents: trackers.refreshTrackerEvents,
        startTrackerRefresh: trackers.startTrackerRefresh,
        stopTrackerRefresh: trackers.stopTrackerRefresh,

        // From leads
        loadLeads: leads.loadLeads,
        loadAnalytics: leads.loadAnalytics,
        clearAllLeads: leads.clearAllLeads,
        confirmLead: leads.confirmLead,
        cancelLead: leads.cancelLead,
        closeDeal: leads.closeDeal,
        revertLeadStage: leads.revertLeadStage,
        deleteLead: leads.deleteLead,
        updateLeadMeta: leads.updateLeadMeta,
        updateLeadStatus: leads.updateLeadStatus,
        markLeadAsSold: leads.markLeadAsSold,
        openLeadDetail: leads.openLeadDetail,

        // From watchlist
        loadWatchlist: watchlist.loadWatchlist,
        clearAllWatchlist: watchlist.clearAllWatchlist,
        addWatchlistFromListing: watchlist.addWatchlistFromListing,
        updateWatchlistMeta: watchlist.updateWatchlistMeta,
        updateWatchlistStatus: watchlist.updateWatchlistStatus,
        promoteWatchlistToLead: watchlist.promoteWatchlistToLead,
        openWatchlistDetail: watchlist.openWatchlistDetail,
        deleteWatchlistItem: watchlist.deleteWatchlistItem,
        deleteAllWatchlist: watchlist.deleteAllWatchlist,
        refreshWatchlist: watchlist.refreshWatchlist,

        // View / focus helpers are injected above as context.setActiveView, context.focusTarget

        // From AI
        loadAIAnalysis,
        closeAIModal,
        ensureAiLoaded,
    });
    // ── Wire events module (needs all action functions on context) ────────
    const events = createApiEvents(context);

    // ── Convenience: addLeadFromListing (cross-cutting: listings → leads) ─
    /**
     * Adds a listing as a lead (purchase) with market estimate data.
     * Prevents duplicate entries and shows appropriate feedback.
     *
     * @param {Object} item - The listing data
     * @param {string} [source='manual'] - How the lead was created
     * @param {string|null} [queryOverride=null] - Override the current query
     * @returns {Promise<void>}
     */
    // Tracks ad_ids with an in-flight watchlist↔leads transition so a
    // double-click on "В покупки" / "В избранное" doesn't fire a second
    // request (the first hasn't reloaded state.leads yet so the
    // alreadyInLeads guard sees the old empty list).
    const _inflightAdMutations = new Set();

    function _guardAdMutation(adId) {
        _inflightAdMutations.add(adId);
        // FE-M14: shared INFLIGHT_GUARD_MS from dom_helpers.js — same
        // window as api_watchlist's per-row dedupe so a click in one
        // surface and a click in the other can't race past each other.
        setTimeout(() => _inflightAdMutations.delete(adId), INFLIGHT_GUARD_MS);
    }

    function _validVersion(value) {
        const numeric = Number(value);
        return Number.isInteger(numeric) && numeric >= 1 ? numeric : null;
    }

    function _findWatchingSnapshot(item) {
        return state.watchlist.items.find(
            (w) => w.id === item?.id || w.ad_id === item?.ad_id,
        ) || null;
    }

    async function _resolveWatchingVersion(item) {
        const current = _findWatchingSnapshot(item) || item;
        const version = _validVersion(current?.version);
        if (version) return version;
        await watchlist.loadWatchlist();
        return _validVersion(_findWatchingSnapshot(item)?.version);
    }

    async function _resolveWatchingItem(item) {
        const current = _findWatchingSnapshot(item);
        if (current) return current;
        await watchlist.loadWatchlist();
        return _findWatchingSnapshot(item);
    }

    async function addLeadFromListing(item, source = "manual", queryOverride = null) {
        if (!hasTelegramInitData() || !item?.ad_id) {
            return;
        }
        if (_inflightAdMutations.has(item.ad_id)) {
            // A previous click for this ad is still mid-flight — ignore.
            return;
        }

        // Closed/skipped leads stay in history but should NOT block a
        // re-add — that's how a returning customer or a re-buy reaches
        // the funnel. Only an actively-tracked lead counts as duplicate.
        const ACTIVE_LEAD_STATUSES = new Set([
            "new",
            "in_progress",
            "researching",
            "bought",
            "sold",
        ]);
        const alreadyInLeads = state.leads.items.some(
            (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
        );
        if (alreadyInLeads) {
            showToast("Уже в покупках", "info", 1600);
            return;
        }

        // If the ad is currently in the watchlist (status='watching'),
        // promotion is a single PATCH on the same lead_items row — no
        // need for a fresh POST. This also avoids race-condition 500s
        // when the user rapid-fires "В избранное" then "В покупки".
        let watchingItem = _findWatchingSnapshot(item);

        _guardAdMutation(item.ad_id);
        const pendingToast = showToast("Добавляю…", "info", 8000);
        try {
            if (!watchingItem && source === "detail_modal" && state.detail.fromWatchlist) {
                watchingItem = await _resolveWatchingItem(item);
            }
            if (watchingItem) {
                const version = await _resolveWatchingVersion(watchingItem);
                if (!version) {
                    if (pendingToast) dismissToast(pendingToast);
                    showToast("Данные устарели. Обновите список и попробуйте ещё раз.", "error");
                    return;
                }
                await core.requestJson(`/api/v1/leads/${watchingItem.id}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        status: "new",
                        version,
                    }),
                });
                // Optimistic: remove from watchlist immediately.
                state.watchlist.items = state.watchlist.items.filter((w) => w.id !== watchingItem.id);
            } else {
                const marketEstimate = (item.flip_estimates || []).find(
                    (entry) => entry.label === "По рынку",
                );
                await core.postJson("/api/v1/leads", {
                    query: queryOverride || state.search.query || "",
                    ad_id: item.ad_id,
                    title: item.title,
                    link: item.link,
                    price_byn: item.price_byn,
                    target_resale_byn: marketEstimate?.target_price || null,
                    status: "new",
                    source,
                    thumbnail: item.thumbnail || null,
                });
            }
            if (pendingToast) dismissToast(pendingToast);
            showToast("В покупках", "success", 1600);
            // Refresh both surfaces so a once-watched item disappears
            // from "Избранное" and shows up in "Покупки" together.
            await Promise.all([leads.loadLeads(), watchlist.loadWatchlist()]);
        } catch (error) {
            if (pendingToast) dismissToast(pendingToast);
            showToast(error.message || "Не удалось добавить в покупки", "error");
        } finally {
            _inflightAdMutations.delete(item.ad_id);
        }
    }

    // Make addLeadFromListing available on context for cross-module calls
    context.addLeadFromListing = addLeadFromListing;

    // ── Additional actions not in any module ──────────────────────────────

    async function deleteHistoryDeal(leadId) {
        try {
            await core.deleteJson(`/api/v1/leads/${leadId}`);
            state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
            renderLeads();
            showToast("Сделка удалена из истории");
            await leads.loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось удалить сделку");
        }
    }

    async function refreshLeads() {
        try {
            const payload = await core.postJson("/api/v1/leads/refresh", {});
            const parts = [`${payload.checked} проверено`, `${payload.active} активно`];
            if (payload.missing > 0) {
                parts.push(`${payload.missing} пропало`);
            }
            showToast(`Покупки: ${parts.join(", ")}`);
            await leads.loadLeads();
        } catch (error) {
            showToast(error.message || "Не удалось проверить покупки");
        }
    }

    /**
     * Parses URL launch parameters (?query=...&view=...) and initializes the app state.
     * Called on app startup to handle deep linking.
     *
     * @returns {Promise<void>}
     */
    async function applyLaunchParams() {
        const params = new URLSearchParams(window.location.search);
        // Support deep linking via start_param (from bot /start tracking)
        // Can come from either URL param or Telegram initDataUnsafe
        const startParam = params.get("start_param") || window.Telegram?.WebApp?.initDataUnsafe?.start_param;
        if (startParam && !params.has("view")) {
            const viewMap = { tracking: "tracking", trackers: "tracking", deals: "deals", monitoring: "monitoring" };
            const mappedView = viewMap[startParam] || startParam;
            if (elements.views[mappedView]) {
                params.set("view", mappedView);
            }
        }
        const query = params.get("query")?.trim() || "";
        const view = params.get("view") || "overview";
        if (view && elements.views[view]) {
            context.setActiveView(view);
        }
        if (!query) {
            renderAll();
            return;
        }
        elements.searchInput.value = query;
        state.search.query = query;
        await listings.search(view);
    }

    // ── Expenses ──────────────────────────────────────────────────────────
    async function loadExpenses(leadId) {
        state.expenses.loading = true;
        renderExpensesModal();
        try {
            state.expenses.items = await core.getJson(`/api/v1/leads/${leadId}/expenses`);
        } catch (_) {
            state.expenses.items = [];
        } finally {
            state.expenses.loading = false;
        }
        renderExpensesModal();
    }

    async function createExpense(leadId, payload) {
        try {
            await core.postJson(`/api/v1/leads/${leadId}/expenses`, payload);
            showToast("Расход добавлен");
            await loadExpenses(leadId);
        } catch (error) {
            showToast(error.message || "Не удалось добавить расход");
        }
    }

    async function deleteExpense(leadId, expenseId) {
        try {
            await core.deleteJson(`/api/v1/leads/${leadId}/expenses/${expenseId}`);
            showToast("Расход удалён");
            await loadExpenses(leadId);
        } catch (error) {
            showToast(error.message || "Не удалось удалить расход");
        }
    }

    // ── Leads export ─────────────────────────────────────────────────────
    /**
     * Download the user's leads as a file. `format` is "csv" (default)
     * or "xlsx" — the backend renders the same row schema in either
     * shape, with proper Excel number formatting on .xlsx.
     */
    async function exportLeads(format = "csv") {
        const fmt = format === "xlsx" ? "xlsx" : "csv";
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120_000);
        try {
            const initData = window.Telegram?.WebApp?.initData;
            const headers = initData ? { "X-Telegram-Init-Data": initData } : {};
            const response = await fetch(`/api/v1/leads/export?format=${fmt}`, {
                headers,
                signal: controller.signal,
            });
            if (!response.ok) {
                throw new Error("Не удалось экспортировать данные");
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = fmt === "xlsx" ? "leads_export.xlsx" : "leads_export.csv";
            a.click();
            // FE-07: defer revoke so the browser actually has time to
            // start the download. On slow Android WebViews / weak
            // CPUs ``a.click()`` queues the navigation but doesn't
            // commit it before the next microtask tick — revoking
            // immediately after caused empty downloads in practice.
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            showToast("Файл загружен");
        } catch (error) {
            if (error.name === "AbortError") {
                showToast("Экспорт занимает слишком долго, попробуйте позже");
            } else {
                showToast(error.message || "Не удалось экспортировать");
            }
        } finally {
            clearTimeout(timeoutId);
        }
    }
    // Backwards-compat name still used by api_events context
    // destructure list and any external bindings.
    const exportLeadsCSV = () => exportLeads("csv");
    const exportLeadsXLSX = () => exportLeads("xlsx");

    // ── Public API (every name the original file exported) ───────────────

    // ── Consent & Privacy ──────────────────────────────────────────────────

    /**
     * Check if user has granted AI consent. Returns true if consent exists.
     * If not, shows the consent modal and returns a Promise that resolves
     * when the user grants consent (or rejects on cancel).
     */
    async function checkAiConsent() {
        try {
            const status = await core.getJson("/api/v1/account/consent/ai_analysis");
            if (status.granted) return true;
        } catch (_) {
            // Not logged in or error — proceed anyway (debug mode)
            return true;
        }
        // Show consent modal
        return new Promise((resolve, reject) => {
            _showConsentModal(resolve, reject);
        });
    }

    function _showConsentModal(resolve, reject) {
        const modal = document.getElementById("consent-modal");
        const aiCb = document.getElementById("consent-ai-checkbox");
        const crossCb = document.getElementById("consent-cross-border-checkbox");
        const pdCb = document.getElementById("consent-pd-checkbox");
        const acceptBtn = document.getElementById("consent-accept-btn");
        const cancelBtn = document.getElementById("consent-cancel-btn");
        const privacyLink = document.getElementById("consent-privacy-link");

        if (!modal) { resolve(true); return; }

        // Reset checkboxes
        aiCb.checked = false;
        crossCb.checked = false;
        pdCb.checked = false;
        acceptBtn.disabled = true;
        modal.hidden = false;
        // FE-H4/UX-H1: install a focus trap when the modal opens so a
        // keyboard user can't Tab out of the consent dialog and
        // interact with the app underneath (which has its own shortcuts
        // and scroll). trapFocus returns a cleanup that restores focus
        // to whatever had it before the modal; we call it from
        // cleanup() on every exit path.
        let focusCleanup = null;
        if (typeof trapFocus === "function") focusCleanup = trapFocus(modal);
        // UX-M2: also hide everything else from assistive tech for
        // the duration of the consent gate.
        if (typeof _applyInertToSiblings === "function") _applyInertToSiblings(modal);

        function updateAcceptBtn() {
            acceptBtn.disabled = !(aiCb.checked && crossCb.checked && pdCb.checked);
        }

        // AbortController to clean up checkbox listeners at once
        const ac = new AbortController();
        const opts = { signal: ac.signal };
        aiCb.addEventListener("change", updateAcceptBtn, opts);
        crossCb.addEventListener("change", updateAcceptBtn, opts);
        pdCb.addEventListener("change", updateAcceptBtn, opts);

        async function onAccept() {
            try {
                await core.postJson("/api/v1/account/consent", { consent_type: "ai_analysis", version: "2026.1" });
                await core.postJson("/api/v1/account/consent", { consent_type: "cross_border", version: "2026.1" });
                await core.postJson("/api/v1/account/consent", { consent_type: "pd_processing", version: "2026.1" });
            } catch (err) {
                showToast(err.message || "Не удалось сохранить согласие");
            }
            cleanup();
            modal.hidden = true;
            resolve(true);
        }

        function onCancel() {
            cleanup();
            modal.hidden = true;
            reject(new Error("consent_denied"));
        }

        function onPrivacyLink(e) {
            e.preventDefault();
            openPrivacyModal();
        }

        // FE-02: close the consent gate on Escape so keyboard users
        // aren't trapped. The global Escape handler in api_events.js
        // doesn't know about this modal (it pre-dates the consent
        // flow), and the focus trap installed above keeps Tab inside
        // the modal — without an Escape exit there is literally no
        // keyboard-only way out except submitting the form. Closing
        // is equivalent to clicking Cancel (rejects the consent
        // Promise) so the call site treats it as denial. The privacy
        // modal is allowed to absorb Escape first when it's open on
        // top, otherwise we'd close both modals together.
        function onKeydown(e) {
            if (e.key !== "Escape") return;
            // If the privacy modal is open ON TOP, let it handle the
            // Escape first.
            const privacyModal = document.getElementById("privacy-modal");
            if (privacyModal && !privacyModal.hidden) return;
            onCancel();
        }

        function cleanup() {
            ac.abort(); // removes all checkbox change listeners
            acceptBtn.removeEventListener("click", onAccept);
            cancelBtn.removeEventListener("click", onCancel);
            if (privacyLink) privacyLink.removeEventListener("click", onPrivacyLink);
            document.removeEventListener("keydown", onKeydown);
            if (typeof focusCleanup === "function") focusCleanup();
            // UX-M2: restore inert AFTER the focus trap cleanup so the
            // restored focus target isn't itself sitting in an inert
            // subtree.
            if (typeof _restoreInertSiblings === "function") _restoreInertSiblings(modal);
        }

        acceptBtn.addEventListener("click", onAccept);
        cancelBtn.addEventListener("click", onCancel);
        if (privacyLink) privacyLink.addEventListener("click", onPrivacyLink);
        document.addEventListener("keydown", onKeydown);
    }

    function openPrivacyModal() {
        const modal = document.getElementById("privacy-modal");
        const overlay = document.getElementById("privacy-overlay");
        const closeBtn = document.getElementById("privacy-modal-close");
        if (!modal) return;
        modal.hidden = false;
        document.body.classList.add("modal-open");
        let focusCleanup = null;
        if (typeof trapFocus === "function") focusCleanup = trapFocus(modal);
        // UX-M2: hide rest of the app from assistive tech while the
        // privacy text is open. The consent modal is usually open
        // underneath this one — the second-call guard inside
        // _applyInertToSiblings (skip already-inert siblings) makes
        // sure we don't double-stamp and mis-restore.
        if (typeof _applyInertToSiblings === "function") _applyInertToSiblings(modal);

        function close() {
            modal.hidden = true;
            document.body.classList.remove("modal-open");
            if (typeof focusCleanup === "function") focusCleanup();
            if (typeof _restoreInertSiblings === "function") _restoreInertSiblings(modal);
            closeBtn?.removeEventListener("click", close);
            overlay?.removeEventListener("click", close);
            document.removeEventListener("keydown", onKeydown);
        }
        // FE-02: Escape closes the privacy modal too — it's a
        // read-only secondary modal on top of the consent gate.
        function onKeydown(e) {
            if (e.key === "Escape") close();
        }
        closeBtn?.addEventListener("click", close);
        overlay?.addEventListener("click", close);
        document.addEventListener("keydown", onKeydown);
    }

    async function deleteAccount() {
        // BE-M3: irreversible action — require the user to type their
        // Telegram first_name (the same one shown in the bot header) to
        // confirm. window.confirm() is unreliable in Telegram WebView,
        // so we use a custom typed-input dialog. Server validates the
        // confirmation independently — this modal is UX, not the
        // security boundary.
        const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user || {};
        const firstName = (tgUser.first_name || "").trim();
        const fallbackId = String(tgUser.id || "");
        // Display the most recognisable identifier — first_name when
        // present, otherwise the numeric id (server accepts either).
        const expected = firstName || fallbackId;
        if (!expected) {
            showToast("Не удалось определить пользователя");
            return;
        }
        const typed = await _showTypedConfirmDialog(
            "Удалить аккаунт?",
            "Все ваши данные будут безвозвратно удалены. Это действие нельзя отменить.",
            expected,
        );
        if (typed === null) return;
        try {
            await core.deleteJson("/api/v1/account", { confirmation: typed });
            showToast("Аккаунт удалён. Данные стёрты.");
            setTimeout(() => window.location.reload(), 1500);
        } catch (err) {
            showToast(err.message || "Не удалось удалить аккаунт");
        }
    }

    function _showTypedConfirmDialog(title, message, expectedText) {
        // BE-M3: typed-input variant of the confirm dialog. Resolves to
        // the entered text on confirm, or ``null`` on cancel. The input
        // has to *exactly* match ``expectedText`` (case-insensitive,
        // stripped) before the confirm button activates — clients that
        // skip this check still hit the server-side validator.
        return new Promise((resolve) => {
            const overlay = document.createElement("div");
            overlay.className = "detail-modal typed-confirm-modal";
            const sheet = document.createElement("div");
            sheet.className = "typed-confirm-sheet";

            const h3 = document.createElement("h3");
            h3.className = "typed-confirm-title";
            h3.textContent = title;

            const p = document.createElement("p");
            p.className = "typed-confirm-message";
            p.textContent = message;

            const hint = document.createElement("p");
            hint.className = "typed-confirm-hint";
            // Build via DOM (not innerHTML) — keeps user-controlled
            // ``expectedText`` away from the HTML parser entirely.
            hint.append("Введите ");
            const expectedSpan = document.createElement("strong");
            expectedSpan.className = "typed-confirm-expected";
            expectedSpan.textContent = expectedText;
            hint.append(expectedSpan, " для подтверждения:");

            const input = document.createElement("input");
            input.type = "text";
            input.autocomplete = "off";
            input.autocapitalize = "off";
            input.spellcheck = false;
            input.className = "typed-confirm-input";

            const btnRow = document.createElement("div");
            btnRow.className = "typed-confirm-actions";

            const cancelBtn = document.createElement("button");
            cancelBtn.setAttribute("data-role", "cancel");
            cancelBtn.className = "typed-confirm-btn typed-confirm-btn--secondary";
            cancelBtn.textContent = "Отмена";

            const confirmBtn = document.createElement("button");
            confirmBtn.setAttribute("data-role", "confirm");
            confirmBtn.className = "typed-confirm-btn typed-confirm-btn--danger";
            confirmBtn.textContent = "Удалить";
            confirmBtn.disabled = true;

            btnRow.append(cancelBtn, confirmBtn);
            sheet.append(h3, p, hint, input, btnRow);
            overlay.appendChild(sheet);
            document.body.appendChild(overlay);
            document.body.classList.add("modal-open");
            let focusCleanup = null;
            if (typeof trapFocus === "function") focusCleanup = trapFocus(sheet);
            // UX-M2: hide the rest of the page from assistive tech
            // while the typed-delete-confirm dialog is open. The
            // overlay was just appended to body so it's a body
            // child by the time we apply.
            if (typeof _applyInertToSiblings === "function") _applyInertToSiblings(overlay);
            // Focus the input so the user can start typing immediately —
            // a typed-confirm dialog where you have to click into the
            // box first is hostile UX.
            setTimeout(() => input.focus(), 0);

            const expectedNorm = expectedText.trim().toLowerCase();
            function refreshConfirm() {
                const matches = input.value.trim().toLowerCase() === expectedNorm;
                confirmBtn.disabled = !matches;
            }
            input.addEventListener("input", refreshConfirm);
            input.addEventListener("keydown", (e) => {
                if (e.key === "Enter" && !confirmBtn.disabled) close(input.value);
            });

            function close(result) {
                document.body.classList.remove("modal-open");
                if (typeof focusCleanup === "function") focusCleanup();
                // UX-M2: restore inert siblings BEFORE removing the
                // overlay — once it's gone, ``_inertSiblings`` is
                // unreachable and the page would stay frozen.
                if (typeof _restoreInertSiblings === "function") _restoreInertSiblings(overlay);
                overlay.remove();
                resolve(result);
            }

            cancelBtn.addEventListener("click", () => close(null));
            confirmBtn.addEventListener("click", () => {
                if (!confirmBtn.disabled) close(input.value);
            });
            overlay.addEventListener("click", (e) => {
                if (e.target === overlay) close(null);
            });
        });
    }


    async function exportAccountData() {
        try {
            const resp = await fetch("/api/v1/account/export", {
                headers: core.telegramHeaders(),
            });
            if (!resp.ok) throw new Error("Экспорт не удался");
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "rafuks_data_export.json";
            a.click();
            // FE-07: see note above the CSV/XLSX revoke — same fix.
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            showToast("Данные экспортированы");
        } catch (err) {
            showToast(err.message || "Не удалось экспортировать данные");
        }
    }

    // Expose consent function on context so api_ai.js can call it
    context.checkAiConsent = checkAiConsent;
    context.openPrivacyModal = openPrivacyModal;
    context.ensureAiLoaded = ensureAiLoaded;

    return {
        bindEvents: events.bindEvents,
        search: listings.search,
        loadListings: listings.loadListings,
        loadMoreListings: listings.loadMoreListings,
        loadHistory: listings.loadHistory,
        loadTrackers: trackers.loadTrackers,
        loadLeads: leads.loadLeads,
        clearAllLeads: leads.clearAllLeads,
        deleteLead: leads.deleteLead,
        confirmLead: leads.confirmLead,
        cancelLead: leads.cancelLead,
        closeDeal: leads.closeDeal,
        deleteHistoryDeal,
        revertLeadStage: leads.revertLeadStage,
        markLeadAsSold: leads.markLeadAsSold,
        openLeadDetail: leads.openLeadDetail,
        loadWatchlist: watchlist.loadWatchlist,
        createTracker: trackers.createTracker,
        addLeadFromListing,
        addWatchlistFromListing: watchlist.addWatchlistFromListing,
        updateLeadStatus: leads.updateLeadStatus,
        updateLeadMeta: leads.updateLeadMeta,
        updateWatchlistStatus: watchlist.updateWatchlistStatus,
        updateWatchlistMeta: watchlist.updateWatchlistMeta,
        promoteWatchlistToLead: watchlist.promoteWatchlistToLead,
        deleteWatchlistItem: watchlist.deleteWatchlistItem,
        deleteAllWatchlist: watchlist.deleteAllWatchlist,
        refreshWatchlist: watchlist.refreshWatchlist,
        refreshLeads,
        openWatchlistDetail: watchlist.openWatchlistDetail,
        deleteTracker: trackers.deleteTracker,
        pauseTracker: trackers.pauseTracker,
        resumeTracker: trackers.resumeTracker,
        openEditTracker: trackers.openEditTracker,
        closeEditTracker: trackers.closeEditTracker,
        saveTracker: trackers.saveTracker,
        openListingDetail: listings.openListingDetail,
        // FE-C4: expose abort hooks so closeDetailModal and any
        // future cleanup paths (view tab change, route navigation)
        // can drop pending fetches before clearing UI state.
        abortDetailRequest: listings.abortDetailRequest,
        abortHistoryRequest: listings.abortHistoryRequest,
        abortSearchRequests: listings.abortSearchRequests,
        applyLaunchParams,
        loadExpenses,
        createExpense,
        deleteExpense,
        exportLeads,
        exportLeadsCSV,
        exportLeadsXLSX,
        loadAIAnalysis,
        closeAIModal,
        checkAiConsent,
        openPrivacyModal,
        deleteAccount,
        exportAccountData,
    };
}


;
function analyticsApp() {
    const core = createAppCore();
    const actionRegistry = {};
    const renderers = createAppRenderers({ ...core, actions: actionRegistry });
    const actions = createAppActions({ ...core, ...renderers });
    Object.assign(actionRegistry, actions);

    // FE-H8: module-scoped handle for the pull-to-refresh uninstall
    // function returned by setupPullToRefresh(). Declared up-front so
    // init() can both re-arm it (idempotency) and pagehide can tear
    // it down without races.
    let _ptrUninstall = null;
    let _telegramBackButtonCleanup = null;

    /**
     * Pick the right "refresh this view" function based on the active
     * tab. Returns null for views where pull-to-refresh is meaningless
     * (e.g. an empty search overview), so the gesture is a no-op
     * instead of bouncing without doing anything useful.
     */
    function getRefreshForActiveView() {
        const view = core.state.ui.activeView || "overview";
        const query = (core.state.search.query || "").trim();
        if (view === "deals") {
            return () => Promise.all([
                actions.loadLeads ? actions.loadLeads() : null,
                actions.loadWatchlist ? actions.loadWatchlist() : null,
            ]);
        }
        if (view === "tracking") {
            return () => actions.loadTrackers && actions.loadTrackers();
        }
        if (view === "overview" || view === "ads") {
            // Repeating the current search is the "refresh" everywhere
            // that depends on Kufar — it re-fetches stats, listings,
            // history together. Pre-merger this had a separate "cheap"
            // branch, but the cheap view is gone — sort=cheap inside
            // the ads view re-uses the same loadListings refresh.
            return query
                ? () => actions.search && actions.search(view === "ads" ? "ads" : "overview")
                : null;
        }
        return null;
    }

    function setupTelegramBackButton() {
        const tg = window.Telegram?.WebApp;
        const backButton = tg?.BackButton;
        if (!backButton || typeof backButton.onClick !== "function") return () => {};

        const isOpen = (el) => Boolean(el && !el.hidden);
        const click = (selector) => {
            const el = document.querySelector(selector);
            if (el && typeof el.click === "function") el.click();
        };
        const topmostClose = () => {
            if (document.querySelector(".typed-confirm-modal")) {
                return () => click(".typed-confirm-modal [data-role='cancel']");
            }
            if (isOpen(document.getElementById("privacy-modal"))) return () => click("#privacy-modal-close");
            if (isOpen(document.getElementById("consent-modal"))) return () => click("#consent-cancel-btn");
            if (isOpen(document.getElementById("la-result-overlay"))) return () => click("#la-result-back");
            if (isOpen(document.getElementById("la-modal"))) return () => click("#la-modal-close");
            if (isOpen(core.elements.aiModal)) return () => actions.closeAIModal?.();
            if (isOpen(core.elements.expensesModal)) return () => renderers.closeExpensesModal?.();
            if (isOpen(core.elements.detailModal)) return () => renderers.closeDetailModal?.();
            if (isOpen(core.elements.editTrackerModal)) return () => actions.closeEditTracker?.();
            return null;
        };
        const sync = () => {
            try {
                if (topmostClose()) backButton.show();
                else backButton.hide();
            } catch (_) {}
        };
        const onBack = () => {
            const close = topmostClose();
            if (!close) {
                sync();
                return;
            }
            close();
            setTimeout(sync, 0);
            setTimeout(sync, 420);
        };

        backButton.onClick(onBack);
        const observer = new MutationObserver(sync);
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["hidden", "class"],
        });
        sync();

        return () => {
            observer.disconnect();
            try { backButton.offClick?.(onBack); } catch (_) {}
            try { backButton.hide(); } catch (_) {}
        };
    }

    function init() {
        core.cacheElements();
        core.populateRegionSelect(core.elements.trackerRegionSelect);
        core.populateRegionSelect(core.elements.editRegionSelect);
        core.populateRegionSelect(core.elements.filterRegion);
        core.initTelegramTheme();
        core.loadRecentSearches();
        actions.bindEvents();

        const themeBtn = document.getElementById("theme-toggle");
        if (themeBtn) themeBtn.addEventListener("click", () => core.toggleTheme());

        const privacyBtn = document.getElementById("privacy-btn");
        if (privacyBtn) privacyBtn.addEventListener("click", () => actions.openPrivacyModal?.());

        core.state.search.query = core.elements.searchInput.value.trim();
        renderers.renderAll();
        void actions.loadTrackers();
        void actions.loadLeads();
        void actions.loadWatchlist();
        void actions.applyLaunchParams();

        // Check PD processing consent on first launch — non-blocking,
        // just shows the consent modal if user hasn't consented yet.
        if (typeof actions.checkAiConsent === "function") {
            // checkAiConsent checks /account/consent/ai_analysis which
            // implies PD processing consent was also granted (both are
            // required together). If missing, the consent modal appears.
            void actions.checkAiConsent().catch((err) => { console.warn("AI consent check failed", err); });
        }

        // Pull-to-refresh — page-scoped, picks the right loader by view.
        // Skipped under prefers-reduced-motion (the helper short-circuits).
        //
        // FE-H8: setupPullToRefresh attaches four document-level touch
        // listeners. The returned uninstall() detaches them. When the
        // mini-app tab is closed (pagehide) or the bfcache restore
        // happens, we run uninstall so the listeners aren't duplicated
        // on next init() — this previously grew unbounded when init
        // was triggered multiple times in dev (HMR, Telegram WebView
        // re-mounts).
        if (typeof setupPullToRefresh === "function") {
            if (typeof _ptrUninstall === "function") {
                try { _ptrUninstall(); } catch (_) { /* already gone */ }
                _ptrUninstall = null;
            }
            _ptrUninstall = setupPullToRefresh({
                getRefreshHandler: getRefreshForActiveView,
                indicatorEl: document.getElementById("ptr-indicator"),
            });
        }
        if (typeof _telegramBackButtonCleanup === "function") {
            try { _telegramBackButtonCleanup(); } catch (_) { /* already gone */ }
            _telegramBackButtonCleanup = null;
        }
        _telegramBackButtonCleanup = setupTelegramBackButton();
    }

    // Detach listeners when the page is unloaded or put into bfcache;
    // the browser normally GCs them, but iOS Safari keeps touch
    // listeners alive across bfcache restores which leads to double
    // firings when we come back.
    window.addEventListener("pagehide", () => {
        if (typeof _ptrUninstall === "function") {
            try { _ptrUninstall(); } catch (_) { /* noop */ }
            _ptrUninstall = null;
        }
        if (typeof _telegramBackButtonCleanup === "function") {
            try { _telegramBackButtonCleanup(); } catch (_) { /* noop */ }
            _telegramBackButtonCleanup = null;
        }
    });

    function search(...args) {
        return actions.search(...args);
    }

    function loadListings(...args) {
        return actions.loadListings(...args);
    }

    function renderChart(...args) {
        return renderers.renderChart(...args);
    }

    return {
        init,
        search,
        loadListings,
        renderChart,
        renderBoxPlot: renderChart,
        formatPrice: core.formatPrice,
    };
}

document.addEventListener("DOMContentLoaded", () => {
    const app = analyticsApp();
    app.init();

    if (window.Telegram?.WebApp) {
        Telegram.WebApp.ready();
        Telegram.WebApp.expand();
    }

    function _applyTelegramTheme() {
        const tp = window.Telegram?.WebApp?.themeParams || {};
        const root = document.documentElement;
        if (tp.bg_color) root.style.setProperty('--tg-theme-bg-color', tp.bg_color);
        if (tp.text_color) root.style.setProperty('--tg-theme-text-color', tp.text_color);
        if (tp.hint_color) root.style.setProperty('--tg-theme-hint-color', tp.hint_color);
        if (tp.link_color) root.style.setProperty('--tg-theme-link-color', tp.link_color);
        if (tp.button_color) root.style.setProperty('--tg-theme-button-color', tp.button_color);
        if (tp.button_text_color) root.style.setProperty('--tg-theme-button-text-color', tp.button_text_color);
        if (tp.secondary_bg_color) root.style.setProperty('--tg-theme-secondary-bg-color', tp.secondary_bg_color);
        if (tp.destructive_text_color) root.style.setProperty('--tg-theme-destructive-text-color', tp.destructive_text_color);
    }
    _applyTelegramTheme();
    window.Telegram?.WebApp?.onEvent?.('themeChanged', _applyTelegramTheme);

    // Offline / online detection
    const _offlineBadge = document.getElementById("offline-badge");
    if (_offlineBadge) {
        window.addEventListener("online", () => _offlineBadge.hidden = true);
        window.addEventListener("offline", () => _offlineBadge.hidden = false);
        if (!navigator.onLine) _offlineBadge.hidden = false;
    }

    // Register the service worker so the shell + read-only API
    // responses survive flaky networks. Skipped on insecure origins
    // (browsers reject SW registration over plain http) so local
    // `python -m http.server` style dev still works without spam in
    // the console. Telegram Mini Apps are always served over HTTPS,
    // so production will always register.
    if (
        "serviceWorker" in navigator &&
        (location.protocol === "https:" || location.hostname === "localhost")
    ) {
        // Defer to after first paint so registration competes with
        // nothing visible — saves ~30 ms on the perceived TTI.
        window.addEventListener("load", () => {
            navigator.serviceWorker
                .register("/sw.js", { scope: "/" })
                .catch((err) => {
                    console.warn("Service worker registration failed", err);
                });
        });
    }
});


window.App = Object.assign(window.App || {}, {
  analyticsApp,
  createAppCore,
  domEl,
  domFragment,
  _prefersReducedMotion,
  openModalAnimated,
  closeModalAnimated,
});
})(window);
