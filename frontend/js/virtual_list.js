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
 *   - DocumentFragment batching for minimal DOM thrashing
 *   - Automatic cleanup via destroy()
 */

function createVirtualList(container, options) {
    const {
        itemHeight = 160,
        bufferSize = 5,
        renderFn,
    } = options;

    let items = [];
    let scrollTop = 0;
    let visibleStart = 0;
    let visibleEnd = 0;
    let rafId = null;
    let destroyed = false;

    // Validate required params
    if (!container || typeof renderFn !== "function") {
        console.error("[virtual-list] container and renderFn are required");
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
    viewport.setAttribute("aria-live", "polite");
    viewport.style.cssText =
        "position:sticky;top:0;overflow:hidden;";
    container.appendChild(viewport);

    // Configure container scrolling
    container.style.overflowY = "auto";
    container.style.maxHeight = "70vh";
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
            Math.floor(scrollTop / itemHeight) - bufferSize
        );
        const end = Math.min(
            items.length,
            Math.ceil((scrollTop + containerHeight) / itemHeight) + bufferSize
        );

        // Skip if visible range hasn't changed
        if (start === visibleStart && end === visibleEnd) return;
        visibleStart = start;
        visibleEnd = end;

        // Calculate spacer heights
        const topPadding = start * itemHeight;
        const bottomPadding = (items.length - end) * itemHeight;

        // Clear and re-render visible items
        viewport.innerHTML = "";
        spacer.style.height = `${topPadding}px`;

        // Create a bottom spacer if needed
        let bottomSpacer = viewport.querySelector("[data-vl-bottom-spacer]");
        if (!bottomSpacer) {
            bottomSpacer = document.createElement("div");
            bottomSpacer.setAttribute("data-vl-bottom-spacer", "true");
            bottomSpacer.setAttribute("aria-hidden", "true");
        }
        bottomSpacer.style.height = `${bottomPadding}px`;

        // Build fragment for batch DOM insertion
        const fragment = document.createDocumentFragment();
        for (let i = start; i < end; i++) {
            const el = renderFn(items[i], i);
            if (el) {
                // Don't force height - let CSS handle it naturally
                // Only add margin if using gap-less container
                if (itemHeight) {
                    el.style.minHeight = `${itemHeight}px`;
                }
                // Mark as virtualized item for potential debugging
                el.setAttribute("data-vl-index", String(i));
                fragment.appendChild(el);
            }
        }
        fragment.appendChild(bottomSpacer);
        viewport.appendChild(fragment);
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
            visibleStart = -1; // Force re-render
            visibleEnd = -1;
            renderVisibleItems();
        },

        /**
         * Clean up event listeners and DOM.
         */
        destroy: function () {
            destroyed = true;
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

/**
 * Estimate how many items are currently rendered in a virtual list.
 * Useful for debugging and performance monitoring.
 */
function getVirtualListRenderedCount(container) {
    const viewport = container.querySelector("[role='list']");
    if (!viewport) return 0;
    return viewport.children.length;
}

/**
 * Check if a container has an active virtual list.
 */
function hasVirtualList(container) {
    return container._virtualList != null;
}
