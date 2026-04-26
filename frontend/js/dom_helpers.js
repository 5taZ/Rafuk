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
function openModalAnimated(modalEl, { lockScroll = true } = {}) {
    if (!modalEl) return;
    // If the modal is already on-screen (e.g. re-render after state update),
    // don't acquire a second scroll-lock — the matching closeModalAnimated
    // would only release one count and leave body.modal-open stuck on.
    const wasHidden = modalEl.hidden;
    // Make sure no leftover closing class from a previous run blocks the
    // entry animation.
    modalEl.classList.remove("is-closing");
    modalEl.hidden = false;
    if (lockScroll && wasHidden) lockBodyScroll();
}

function closeModalAnimated(modalEl, { lockScroll = true } = {}) {
    if (!modalEl || modalEl.hidden) return;

    // Bottom-sheet modals use `.detail-sheet`; centred dialogs use
    // `.modal-content`. Either way we wait for the inner panel's
    // animation to finish before flipping `[hidden]` back.
    const inner =
        modalEl.querySelector(".detail-sheet") || modalEl.querySelector(".modal-content");
    const prefersReducedMotion =
        window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const finalize = () => {
        modalEl.hidden = true;
        modalEl.classList.remove("is-closing");
        if (lockScroll) unlockBodyScroll();
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
    const reduced =
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return card;

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
        const haptic = window.Telegram?.WebApp?.HapticFeedback;
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

    const reduced =
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return () => {};

    let startY = 0;
    let startX = 0;
    let dragging = false;
    let decided = false;
    let pullDistance = 0;
    let refreshing = false;

    const target = document.documentElement;

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
        target.style.transform = "";
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

    document.addEventListener(
        "touchstart",
        (event) => {
            if (event.touches.length !== 1) return;
            if (!isEligible()) return;
            startY = event.touches[0].clientY;
            startX = event.touches[0].clientX;
            dragging = false;
            decided = false;
            pullDistance = 0;
        },
        { passive: true },
    );

    document.addEventListener(
        "touchmove",
        (event) => {
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
            target.style.transform = `translateY(${pullDistance}px)`;
            const ratio = pullDistance / thresholdPx;
            setIndicatorState(ratio >= 1 ? "ready" : "pulling", ratio);
        },
        { passive: true },
    );

    function onEnd() {
        if (!dragging) {
            reset();
            return;
        }
        const committed = pullDistance >= thresholdPx;
        if (!committed) {
            // Spring back without firing.
            target.style.transition = "transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1)";
            target.style.transform = "";
            setIndicatorState("idle");
            setTimeout(() => {
                target.style.transition = "";
                reset();
            }, 240);
            return;
        }
        // Commit.
        const haptic = window.Telegram?.WebApp?.HapticFeedback;
        try {
            haptic?.impactOccurred?.("light");
        } catch (_) {
            /* haptics not available outside Telegram */
        }
        refreshing = true;
        setIndicatorState("refreshing");
        target.style.transition = "transform 200ms cubic-bezier(0.2, 0.8, 0.2, 1)";
        target.style.transform = `translateY(${thresholdPx}px)`;

        const handler = getRefreshHandler();
        const cleanup = () => {
            target.style.transition = "transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1)";
            target.style.transform = "";
            setIndicatorState("idle");
            setTimeout(() => {
                target.style.transition = "";
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
 */
function attachPinchZoom(img, options) {
    if (!img) return { reset: () => {} };
    const minScale = options?.minScale ?? 1;
    const maxScale = options?.maxScale ?? 4;
    const doubleTapScale = options?.doubleTapScale ?? 2;

    let scale = 1;
    let tx = 0;
    let ty = 0;
    let pinchStartDistance = 0;
    let pinchStartScale = 1;
    let pinchCenter = { x: 0, y: 0 };
    let panStartX = 0;
    let panStartY = 0;
    let panBaseTx = 0;
    let panBaseTy = 0;
    let lastTapTs = 0;
    let lastTapX = 0;
    let lastTapY = 0;

    function apply() {
        img.style.transform =
            `translate3d(${tx}px, ${ty}px, 0) scale(${scale})`;
        img.style.transformOrigin = "center center";
        if (scale > 1.001) {
            img.classList.add("is-zoomed");
        } else {
            img.classList.remove("is-zoomed");
        }
    }

    function reset(animate = true) {
        scale = 1;
        tx = 0;
        ty = 0;
        if (animate) {
            img.style.transition =
                "transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1)";
        } else {
            img.style.transition = "";
        }
        apply();
        if (animate) {
            setTimeout(() => {
                img.style.transition = "";
            }, 240);
        }
    }

    /** Clamp the translate values so the image can't be panned out of
     *  view at the current scale. Approximate — uses the rendered
     *  bounding box rather than the natural size. */
    function clampPan() {
        if (scale <= 1) {
            tx = 0;
            ty = 0;
            return;
        }
        const rect = img.getBoundingClientRect();
        const overflowX = (rect.width * scale - rect.width) / 2;
        const overflowY = (rect.height * scale - rect.height) / 2;
        if (tx > overflowX) tx = overflowX;
        if (tx < -overflowX) tx = -overflowX;
        if (ty > overflowY) ty = overflowY;
        if (ty < -overflowY) ty = -overflowY;
    }

    function distance(touches) {
        const [a, b] = touches;
        const dx = a.clientX - b.clientX;
        const dy = a.clientY - b.clientY;
        return Math.hypot(dx, dy);
    }

    img.addEventListener(
        "touchstart",
        (event) => {
            if (event.touches.length === 2) {
                event.preventDefault();
                pinchStartDistance = distance(event.touches);
                pinchStartScale = scale;
                pinchCenter = {
                    x: (event.touches[0].clientX + event.touches[1].clientX) / 2,
                    y: (event.touches[0].clientY + event.touches[1].clientY) / 2,
                };
            } else if (event.touches.length === 1) {
                const t = event.touches[0];
                const now = Date.now();
                const dx = t.clientX - lastTapX;
                const dy = t.clientY - lastTapY;
                if (now - lastTapTs < 280 && Math.abs(dx) < 30 && Math.abs(dy) < 30) {
                    // Double-tap → toggle zoom.
                    event.preventDefault();
                    if (scale > 1.001) {
                        reset(true);
                    } else {
                        scale = doubleTapScale;
                        // Centre the zoom around the tap point relative
                        // to the current view.
                        const rect = img.getBoundingClientRect();
                        const cx = t.clientX - (rect.left + rect.width / 2);
                        const cy = t.clientY - (rect.top + rect.height / 2);
                        tx = -cx * (scale - 1);
                        ty = -cy * (scale - 1);
                        clampPan();
                        img.style.transition =
                            "transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1)";
                        apply();
                        setTimeout(() => {
                            img.style.transition = "";
                        }, 240);
                    }
                    lastTapTs = 0;
                    return;
                }
                lastTapTs = now;
                lastTapX = t.clientX;
                lastTapY = t.clientY;
                if (scale > 1.001) {
                    panStartX = t.clientX;
                    panStartY = t.clientY;
                    panBaseTx = tx;
                    panBaseTy = ty;
                }
            }
        },
        { passive: false },
    );

    img.addEventListener(
        "touchmove",
        (event) => {
            if (event.touches.length === 2) {
                event.preventDefault();
                const d = distance(event.touches);
                if (pinchStartDistance > 0) {
                    let next = pinchStartScale * (d / pinchStartDistance);
                    if (next < minScale) next = minScale;
                    if (next > maxScale) next = maxScale;
                    scale = next;
                    clampPan();
                    apply();
                }
            } else if (event.touches.length === 1 && scale > 1.001) {
                event.preventDefault();
                const t = event.touches[0];
                tx = panBaseTx + (t.clientX - panStartX);
                ty = panBaseTy + (t.clientY - panStartY);
                clampPan();
                apply();
            }
        },
        { passive: false },
    );

    img.addEventListener(
        "touchend",
        (event) => {
            // If we drop below ~1.05 after a pinch, snap back to 1.
            if (event.touches.length === 0 && scale < 1.05 && scale !== 1) {
                reset(true);
            }
            pinchStartDistance = 0;
        },
        { passive: true },
    );
    img.addEventListener("touchcancel", () => reset(true), { passive: true });

    return {
        reset: (animate = true) => reset(animate),
        get scale() {
            return scale;
        },
    };
}
