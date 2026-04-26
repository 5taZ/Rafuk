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
    // Make sure no leftover closing class from a previous run blocks the
    // entry animation.
    modalEl.classList.remove("is-closing");
    modalEl.hidden = false;
    if (lockScroll) lockBodyScroll();
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
