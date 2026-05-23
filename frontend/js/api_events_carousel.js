function _bindCarouselEvents(ctx) {
    let isAnimating = false;
    const _preloadCache = [];

    function _preloadAdjacent() {
        const images = ctx.state.detail.data?.images || [];
        const idx = ctx.state.detail.imageIndex || 0;
        _preloadCache.length = 0;
        for (const i of [idx - 1, idx + 1]) {
            if (i < 0 || i >= images.length) continue;
            const raw = images[i];
            if (typeof raw !== "string" || !raw) continue;
            const validated = (typeof ctx.safeImageUrl === "function")
                ? ctx.safeImageUrl(raw)
                : "";
            if (!validated) continue;
            const proxyUrl = (typeof ctx.optimizedImage === "function")
                ? ctx.optimizedImage(validated, { width: 800, useProxy: true })
                : validated;
            if (
                proxyUrl &&
                proxyUrl !== validated &&
                typeof ctx.fetchProxyImageObjectUrl === "function"
            ) {
                void ctx.fetchProxyImageObjectUrl(proxyUrl).catch(() => {});
                continue;
            }
            const url = (typeof ctx.optimizedImage === "function")
                ? ctx.optimizedImage(validated, { width: 800 })
                : validated;
            const ghost = new Image();
            ghost.src = url;
            _preloadCache.push(ghost);
        }
    }

    async function navigateDetailImage(direction) {
        const images = ctx.state.detail.data?.images;
        if (!images || images.length <= 1) return;
        if (isAnimating) return;

        const total = images.length;
        const newIndex = direction > 0
            ? Math.min(total - 1, ctx.state.detail.imageIndex + 1)
            : Math.max(0, ctx.state.detail.imageIndex - 1);
        if (newIndex === ctx.state.detail.imageIndex) return;

        const img = ctx.elements.detailMainImage;
        if (!img) {
            ctx.state.detail.imageIndex = newIndex;
            ctx.renderDetailModal();
            return;
        }
        if (img._pinchController) {
            img._pinchController.reset(false);
        }

        isAnimating = true;

        const exitX = direction > 0 ? -8 : 8;
        img.style.transition = "opacity 140ms ease-out, transform 140ms ease-out";
        img.style.opacity = "0";
        img.style.transform = `translate3d(${exitX}px, 0, 0)`;

        await new Promise((resolve) => setTimeout(resolve, 140));

        ctx.state.detail.imageIndex = newIndex;
        ctx.renderDetailModal();
        _preloadAdjacent();

        const enterX = direction > 0 ? 8 : -8;
        img.style.transition = "none";
        img.style.opacity = "0";
        img.style.transform = `translate3d(${enterX}px, 0, 0)`;
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

    const SWIPE_THRESHOLD_PX = 50;
    let touchStartX = 0;
    let touchStartY = 0;
    let touchSkip = false;

    ctx.elements.detailMedia?.addEventListener("touchstart", (e) => {
        if (isAnimating) {
            touchSkip = true;
            return;
        }
        if (e.touches && e.touches.length > 1) {
            touchSkip = true;
            return;
        }
        if (ctx.elements.detailMainImage?.classList.contains("is-zoomed")) {
            touchSkip = true;
            return;
        }
        touchSkip = false;
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
    }, { passive: true });

    ctx.elements.detailMedia?.addEventListener("touchend", (e) => {
        if (touchSkip) {
            touchSkip = false;
            return;
        }
        const dx = e.changedTouches[0].clientX - touchStartX;
        const dy = e.changedTouches[0].clientY - touchStartY;
        if (Math.abs(dx) < SWIPE_THRESHOLD_PX) return;
        if (Math.abs(dy) > Math.abs(dx) * 0.8) return;
        void navigateDetailImage(dx < 0 ? 1 : -1);
    }, { passive: true });

    const _preloadOnOpen = () => {
        if (ctx.state.detail.data) _preloadAdjacent();
    };
    ctx.elements.detailModal?.addEventListener("transitionend", _preloadOnOpen, { passive: true });

    if (document._kufarArrowHandler) {
        document.removeEventListener("keydown", document._kufarArrowHandler);
    }
    const arrowHandler = (event) => {
        if (!ctx.state.detail.data) return;
        if (event.key === "ArrowLeft") {
            void navigateDetailImage(-1);
        } else if (event.key === "ArrowRight") {
            void navigateDetailImage(1);
        }
    };
    document._kufarArrowHandler = arrowHandler;
    document.addEventListener("keydown", arrowHandler);
}
