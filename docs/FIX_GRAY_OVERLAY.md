# Fix: Gray Overlay on First Interaction with Ads View

## Problem
When first interacting with the ads view (Объявления), the page appears covered with a gray overlay/pelena. Only after clicking somewhere does it disappear.

## Root Cause
The `.view-panel` had a default animation with `animation-play-state: paused`, but the animation's initial state (`opacity: 0`) was causing a gray overlay effect when the view was first shown.

## Solution

### Changed in `frontend/css/style.css`:

**Before:**
```css
.view-panel {
    animation: viewEnter 200ms ease-out;
    animation-play-state: paused;
}

.view-panel.is-entering {
    animation-play-state: running;
}
```

**After:**
```css
/* View transitions - only animate when explicitly triggered */
.view-panel {
    /* No default animation to prevent gray overlay on first interaction */
}

.view-panel.is-entering {
    animation: viewEnter 200ms ease-out;
}
```

### Updated Media Query:

**Before:**
```css
@media (prefers-reduced-motion: reduce) {
    .view-content {
        animation: none;
    }
}
```

**After:**
```css
@media (prefers-reduced-motion: reduce) {
    .view-panel.is-entering {
        animation: none;
    }
}
```

## How It Works Now

1. **Default state:** `.view-panel` has NO animation by default
2. **First interaction:** View appears immediately without any opacity transition
3. **Subsequent tab switches:** `setActiveView()` adds `.is-entering` class, triggering the 200ms slide-up animation
4. **Animation cleanup:** After 200ms, `.is-entering` class is removed

## Result

✅ No gray overlay on first interaction  
✅ Smooth transitions when switching tabs  
✅ Respects `prefers-reduced-motion`  
✅ Instant visibility of content  

## Files Modified

- `frontend/css/style.css` - Removed default animation from `.view-panel`, updated media query
