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
    const {
        itemHeight = 160,
        bufferSize = 5,
        maxHeight = "70vh",
        renderFn,
    } = options;

    let items = [];
    let scrollTop = 0;
    let visibleStart = 0;
    let visibleEnd = 0;
    let rafId = null;
    let destroyed = false;

    // DOM recycling map: index → DOM element for currently rendered items
    let renderedMap = new Map();

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
    viewport.setAttribute("aria-live", "polite");
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
                if (itemHeight) {
                    el.style.minHeight = `${itemHeight}px`;
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
        const sortedKeys = [...renderedMap.keys()].sort((a, b) => a - b);
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
            domClear(viewport);
            viewport.appendChild(bottomSpacer);
            visibleStart = -1; // Force re-render
            visibleEnd = -1;
            renderVisibleItems();
        },

        /**
         * Clean up event listeners and DOM.
         */
        destroy: function () {
            destroyed = true;
            renderedMap.clear();
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
