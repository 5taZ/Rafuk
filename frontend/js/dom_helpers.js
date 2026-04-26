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
 * The base CSS keeps `.detail-modal[hidden]` rendered (display: block) but
 * invisible (opacity:0, sheet translateY 100%). Toggling `[hidden]` then
 * triggers the transitions for free.
 *
 * `closeModalAnimated` waits for the transition to finish before flipping
 * `[hidden]` back so the slide-down animation actually plays.
 */
function openModalAnimated(modalEl, { lockScroll = true } = {}) {
    if (!modalEl) return;
    modalEl.hidden = false;
    // Force a reflow so `[hidden]→visible` transitions actually start
    // (otherwise the browser collapses both states into one paint).
    void modalEl.offsetWidth;
    if (lockScroll) lockBodyScroll();
}

function closeModalAnimated(modalEl, { lockScroll = true } = {}) {
    if (!modalEl || modalEl.hidden) return;
    const sheet = modalEl.querySelector(".detail-sheet");
    const prefersReducedMotion =
        window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const finalize = () => {
        modalEl.hidden = true;
        if (lockScroll) unlockBodyScroll();
    };

    if (prefersReducedMotion || !sheet) {
        finalize();
        return;
    }

    let done = false;
    const onEnd = (event) => {
        if (event && event.target !== sheet) return;
        if (event && event.propertyName !== "transform") return;
        if (done) return;
        done = true;
        sheet.removeEventListener("transitionend", onEnd);
        finalize();
    };
    sheet.addEventListener("transitionend", onEnd);

    // Trigger the close transition by toggling the closing class first;
    // CSS animates opacity + sheet translate.
    modalEl.classList.add("is-closing");
    // Belt-and-braces: even if transitionend never fires, finalise after
    // the longest transition we've defined (320ms) + some slack.
    setTimeout(() => {
        if (done) return;
        done = true;
        sheet.removeEventListener("transitionend", onEnd);
        finalize();
        modalEl.classList.remove("is-closing");
    }, 380);

    // Need to wait one frame, then reset the flag once we've animated out.
    setTimeout(() => modalEl.classList.remove("is-closing"), 380);
}
