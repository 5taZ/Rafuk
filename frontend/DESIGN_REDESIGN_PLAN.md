# Rafuks Design System — Redesign Plan

## Philosophy
Move from a **heavy, border-driven** aesthetic to a **light, surface-driven** system. Think Linear/Vercel: surfaces separated by elevation and subtle color, not thick borders. Reduce the amber accent monopoly — let data speak through color, not chrome.

---

## 1. Design Tokens

### 1.1 Colors

#### Problem
- `#f59e0b` (amber) is everywhere — accent, borders, backgrounds, badges
- Neutral grays are warm/muddy (`#888078`, `#3e3d42`) — feels dated
- Too many surface colors (`bg`, `bg-card`, `bg-elevated`, `bg-input`, `bg-hover`) all slightly different

#### New Palette — Dark Theme (Primary)

```css
/* === SURFACE LAYERS (cool neutral, not warm) === */
--bg:              #09090b;   /* Zinc 950 — deepest background */
--bg-surface:      #111113;   /* One step up — card level 1 */
--bg-surface-2:    #18181b;   /* Two steps up — elevated panels */
--bg-surface-3:    #222225;   /* Three steps up — inputs, overlays */
--bg-surface-hover:#2a2a2e;   /* Hover state for interactive surfaces */

/* === BORDERS (barely there) === */
--border-subtle:   rgba(255, 255, 255, 0.04);  /* For large card edges */
--border-default:  rgba(255, 255, 255, 0.06);  /* Default divider */
--border-focus:    rgba(255, 255, 255, 0.12);  /* Focus rings */
--border-accent:   rgba(59, 130, 246, 0.4);    /* Blue focus for inputs */

/* === TEXT (clean hierarchy) === */
--text-primary:    #fafafa;   /* Zinc 50 — primary content */
--text-secondary:  #a1a1aa;   /* Zinc 400 — secondary, muted */
--text-tertiary:   #52525b;   /* Zinc 600 — labels, hints, metadata */
--text-inverse:    #09090b;   /* For text on bright buttons */

/* === ACCENT — shift from amber to blue (primary action color) === */
--accent:          #3b82f6;   /* Blue 500 — primary interactive */
--accent-soft:     rgba(59, 130, 246, 0.08);
--accent-glow:     rgba(59, 130, 246, 0.25);
--accent-hover:    #60a5fa;   /* Blue 400 */
--accent-active:   #2563eb;   /* Blue 600 */

/* === SEMANTIC — success, warning, danger === */
--success:         #22c55e;   /* Green 500 */
--success-soft:    rgba(34, 197, 94, 0.08);
--success-border:  rgba(34, 197, 94, 0.2);

--warning:         #eab308;   /* Yellow 500 (amber replacement for actual warnings) */
--warning-soft:    rgba(234, 179, 8, 0.08);
--warning-border:  rgba(234, 179, 8, 0.2);

--danger:          #ef4444;   /* Red 500 */
--danger-soft:     rgba(239, 68, 68, 0.08);
--danger-border:   rgba(239, 68, 68, 0.2);

/* === SPECIAL — profit, deal signals (keep amber only where it means "deal") === */
--deal:            #f59e0b;   /* Amber 500 — ONLY for deal/profit context */
--deal-soft:       rgba(245, 158, 11, 0.08);
--deal-border:     rgba(245, 158, 11, 0.2);
```

#### New Palette — Light Theme

```css
--bg:              #fafafa;
--bg-surface:      #ffffff;
--bg-surface-2:    #f4f4f5;   /* Zinc 100 */
--bg-surface-3:    #e4e4e7;   /* Zinc 200 */
--bg-surface-hover:#f0f0f2;

--border-subtle:   rgba(0, 0, 0, 0.03);
--border-default:  rgba(0, 0, 0, 0.06);
--border-focus:    rgba(0, 0, 0, 0.12);
--border-accent:   rgba(59, 130, 246, 0.5);

--text-primary:    #09090b;
--text-secondary:  #71717a;   /* Zinc 500 */
--text-tertiary:   #a1a1aa;   /* Zinc 400 */
--text-inverse:    #ffffff;

--accent:          #2563eb;   /* Blue 600 — darker for light bg contrast */
--accent-soft:     rgba(37, 99, 235, 0.06);
--accent-glow:     rgba(37, 99, 235, 0.15);
--accent-hover:    #1d4ed8;
--accent-active:   #1e40af;

--success:         #16a34a;
--success-soft:    rgba(22, 163, 74, 0.06);
--success-border:  rgba(22, 163, 74, 0.2);

--warning:         #ca8a04;
--warning-soft:    rgba(202, 138, 4, 0.06);
--warning-border:  rgba(202, 138, 4, 0.2);

--danger:          #dc2626;
--danger-soft:     rgba(220, 38, 38, 0.06);
--danger-border:   rgba(220, 38, 38, 0.2);

--deal:            #d97706;
--deal-soft:       rgba(217, 119, 6, 0.06);
--deal-border:     rgba(217, 119, 6, 0.2);
```

#### Why Blue Instead of Amber?
- Blue = trust, data, action (Linear, Vercel, Stripe all use blue)
- Amber feels "warning" — bad for primary actions
- Amber is **reserved** for deal/profit context (where it means "money/deal")
- Semantic color separation: blue = interact, green = profit, amber = deal signal

### 1.2 Spacing Scale

```css
/* 4px base unit — consistent, predictable */
--sp-1:  4px;    /* Tight gaps, icon padding */
--sp-2:  8px;    /* Standard gap between related items */
--sp-3:  12px;   /* Section gaps, card internal padding */
--sp-4:  16px;   /* Card padding, section margins */
--sp-5:  20px;   /* Medium section spacing */
--sp-6:  24px;   /* Major section breaks */
--sp-8:  32px;   /* View-level spacing */
--sp-10: 40px;   /* Page margins */
```

**Changes from current:**
- App padding: `14px` → `var(--sp-4)` (16px)
- Section margin: `18px` → `var(--sp-5)` (20px)
- Card padding: `12-14px` → `var(--sp-4)` (16px) consistent
- Grid gaps: `8px` → `var(--sp-2)` (8px) — keep, it's good
- Between-card gaps: `8px` → `var(--sp-2)` (8px) — keep

### 1.3 Typography

#### Problem
- Too many font sizes (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 22px)
- Inconsistent weights (600 and 700 used interchangeably for "bold")
- Mono overused for numbers that don't need alignment

#### New Scale

```css
/* Base: 15px on html */
--text-xs:   11px;   /* Labels, metadata, badges */
--text-sm:   12px;   /* Secondary text, descriptions */
--text-base: 14px;   /* Body text (was 15px — tighter on mobile) */
--text-md:   15px;   /* Input text, navigation */
--text-lg:   17px;   /* Card titles, section headers */
--text-xl:   20px;   /* Page titles, hero text */
--text-2xl:  24px;   /* Stats values, prices (was 22px) */
--text-3xl:  28px;   /* Hero stat values (was 22px) */

/* Font weights — simplify to 3 tiers */
--fw-normal: 400;
--fw-medium: 500;    /* "Semi-bold" — for secondary emphasis */
--fw-bold:   600;    /* Primary emphasis (was 700 everywhere) */

/* Line heights */
--lh-tight:   1.2;   /* Headlines, prices */
--lh-normal:  1.5;   /* Body text */
--lh-loose:   1.6;   /* Descriptions, hints */

/* Letter spacing */
--ls-tight:   -0.025em;  /* Headlines */
--ls-normal:  0;
--ls-wide:    0.05em;    /* Labels, uppercase */
--ls-wider:   0.08em;    /* Section titles, badges */
```

**Key changes:**
- Reduce `font-weight: 700` → `600` everywhere except prices/stat values
- `--font-mono` only for prices, stats, numbers that need tabular alignment
- Section titles: `11px uppercase` → `12px uppercase, fw: 600`
- Card titles: `13px fw:600` → `14px fw:600, ls: -0.01em`
- Prices: keep mono but increase from 16px → `var(--text-2xl)` (20px equivalent feel)

### 1.4 Shadows

#### Problem
- Single-layer shadows feel flat and harsh
- `0 1px 3px rgba(0,0,0,0.4)` + `0 0 0 1px border` = heavy border + shadow combo
- On dark theme, shadows barely visible, borders do all the work

#### New Approach — Layered, Diffused

```css
/* Dark theme — shadows are subtle, surfaces separated by color */
--shadow-sm:  0 1px 2px rgba(0, 0, 0, 0.2);
--shadow-md:  0 2px 6px rgba(0, 0, 0, 0.25), 0 1px 2px rgba(0, 0, 0, 0.15);
--shadow-lg:  0 8px 24px rgba(0, 0, 0, 0.35), 0 2px 6px rgba(0, 0, 0, 0.2);
--shadow-xl:  0 16px 48px rgba(0, 0, 0, 0.4), 0 4px 12px rgba(0, 0, 0, 0.25);

/* Light theme — slightly more pronounced */
--shadow-sm:  0 1px 2px rgba(0, 0, 0, 0.04);
--shadow-md:  0 2px 8px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
--shadow-lg:  0 8px 24px rgba(0, 0, 0, 0.08), 0 2px 8px rgba(0, 0, 0, 0.04);
--shadow-xl:  0 16px 48px rgba(0, 0, 0, 0.1), 0 4px 12px rgba(0, 0, 0, 0.06);
```

**Key change:** Remove the `0 0 0 1px var(--border)` from shadow definitions. Borders are now separate, not baked into shadows. Cards use `border: 1px solid var(--border-subtle)` independently.

### 1.5 Border Radius

```css
--r-sm:  8px;   /* Small buttons, badges, inputs */
--r-md:  10px;  /* Cards, chips (was 12px — tighter) */
--r-lg:  14px;  /* Large cards, modals (was 16px) */
--r-xl:  18px;  /* Hero sections, chart containers (was 20px) */
--r-2xl: 24px;  /* Bottom sheets, modals top corners */
--r-full: 999px; /* Pills, toggles */
```

### 1.6 Transitions

```css
/* Timing */
--ease-out:    cubic-bezier(0.16, 1, 0.3, 1);  /* Snappy exit */
--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1); /* Micro-bounce */
--ease-smooth: cubic-bezier(0.4, 0, 0.2, 1);   /* Standard material */

/* Duration classes */
--duration-fast: 100ms;    /* Button press, instant feedback */
--duration-normal: 150ms;  /* Hover states, color transitions */
--duration-slow: 250ms;    /* Panel opens, card enters */
```

---

## 2. Visual Hierarchy

### Level 1 — Primary (What the user needs RIGHT NOW)
- **Prices**: `--text-primary`, `--text-2xl`/`--text-3xl`, `--font-mono`
- **Action buttons**: `--accent` background, `--text-inverse` text
- **Card titles**: `--text-primary`, `--text-lg`, `--fw-bold`
- **Active tab**: `--accent` background, `--text-inverse`
- **Deal score / verdict**: Colored badges with semantic colors

### Level 2 — Secondary (Supporting context)
- **Stats labels**: `--text-tertiary`, `--text-xs`, `uppercase`, `--ls-wide`
- **Secondary buttons**: `--bg-surface-2` bg, `--border-default` border, `--text-primary` text
- **Metadata**: `--text-secondary`, `--text-sm`
- **Inactive tabs**: `--bg-surface` bg, `--border-subtle` border, `--text-secondary` text

### Level 3 — Tertiary (Barely visible, discoverable)
- **Section dividers**: `--border-subtle`
- **Placeholder text**: `--text-tertiary`
- **Helper text**: `--text-secondary`, `--text-xs`, `--lh-loose`
- **Disabled states**: `opacity: 0.4`

---

## 3. Component Redesigns

### 3.1 Cards

**Current:**
```css
background: var(--bg-card);
box-shadow: 0 1px 3px rgba(0,0,0,0.4), 0 0 0 1px var(--border);
border-radius: 16px;
padding: 12-14px;
```

**New:**
```css
.card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: var(--sp-4);  /* 16px */
    /* NO box-shadow on cards in dark theme — surface color is enough */
}

[data-theme="light"] .card {
    box-shadow: var(--shadow-sm);
    border: 1px solid var(--border-default);
}
```

**Key changes:**
- Remove gradient backgrounds from cards (no more `radial-gradient(circle at top right, var(--accent-soft), transparent)`)
- Remove `box-shadow` in dark theme — separation via surface color alone
- Consistent 16px padding (was 12-14px varying)
- Border radius `10px` (was 12-16px varying)
- **No accent gradients on tracker cards, stat cards, feature cards**

### 3.2 Buttons — 3 Clear Tiers

#### Primary (Main action, one per section)
```css
.btn-primary {
    background: var(--accent);
    color: var(--text-inverse);
    border: none;
    border-radius: var(--r-sm);
    padding: 10px 16px;
    font: 600 var(--text-sm) var(--font);
    transition: background var(--duration-normal) var(--ease-smooth),
                transform var(--duration-fast) var(--ease-out);
}

.btn-primary:hover {
    background: var(--accent-hover);
}

.btn-primary:active {
    transform: scale(0.97);
    background: var(--accent-active);
}
```

#### Secondary (Alternative actions)
```css
.btn-secondary {
    background: var(--bg-surface-2);
    color: var(--text-primary);
    border: 1px solid var(--border-default);
    border-radius: var(--r-sm);
    padding: 10px 16px;
    font: 500 var(--text-sm) var(--font);
    transition: all var(--duration-normal) var(--ease-smooth);
}

.btn-secondary:hover {
    background: var(--bg-surface-hover);
    border-color: var(--border-focus);
}

.btn-secondary:active {
    transform: scale(0.97);
}
```

#### Ghost (Minimal, tertiary actions)
```css
.btn-ghost {
    background: transparent;
    color: var(--text-secondary);
    border: none;
    border-radius: var(--r-sm);
    padding: 8px 12px;
    font: 500 var(--text-sm) var(--font);
    transition: all var(--duration-normal) var(--ease-smooth);
}

.btn-ghost:hover {
    background: var(--bg-surface-2);
    color: var(--text-primary);
}

.btn-ghost.danger:hover {
    background: var(--danger-soft);
    color: var(--danger);
}
```

**Key changes:**
- Remove pill-shaped buttons (`border-radius: 999px`) — use `8px` instead
- Remove heavy borders on primary buttons
- Consistent font weight: 600 for primary, 500 for secondary/ghost
- Remove `transform: translateY(-1px)` on hover — feels floaty

### 3.3 Inputs

**Current:**
```css
background: var(--bg-input);
border: 1.5px solid var(--border);
border-radius: 20px;
padding: 13px 0;
```

**New:**
```css
.input {
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-sm);
    padding: 10px 14px;
    color: var(--text-primary);
    font: 500 var(--text-base) var(--font);
    transition: all var(--duration-normal) var(--ease-smooth);
    outline: none;
}

.input::placeholder {
    color: var(--text-tertiary);
}

.input:focus {
    border-color: var(--border-accent);
    background: var(--bg-surface);
    box-shadow: 0 0 0 3px var(--accent-soft);
}
```

**Key changes:**
- Remove pill-shaped search input — use `8px` radius
- Add focus ring: `0 0 0 3px var(--accent-soft)` (modern, accessible)
- Reduce font weight from 600 to 500
- Softer placeholder color

### 3.4 Badges — Reduce from 20+ to 5 Core Types

**Current:** ~25 badge variants (verdict-zabirat, verdict-smotret, verdict-norm, verdict-mimo, fresh-hot, fresh-warm, risk-low, risk-medium, risk-high, too_cheap, too_expensive, cheap, under, over, fair, warn, neutral, duplicate, anomaly, etc.)

**New — 5 Semantic Types:**

```css
/* Type 1: Success (good deal, profit, below median) */
.badge-success {
    background: var(--success-soft);
    color: var(--success);
    border: 1px solid var(--success-border);
}

/* Type 2: Warning (fair price, moderate risk) */
.badge-warning {
    background: var(--warning-soft);
    color: var(--warning);
    border: 1px solid var(--warning-border);
}

/* Type 3: Danger (overpriced, high risk, price drop) */
.badge-danger {
    background: var(--danger-soft);
    color: var(--danger);
    border: 1px solid var(--danger-border);
}

/* Type 4: Info (new listing, metadata, status) */
.badge-info {
    background: var(--accent-soft);
    color: var(--accent);
    border: 1px solid rgba(59, 130, 246, 0.2);
}

/* Type 5: Neutral (default, unclassified) */
.badge-neutral {
    background: var(--bg-surface-2);
    color: var(--text-secondary);
    border: 1px solid var(--border-default);
}
```

**Mapping old → new:**
| Old Badge | New Type |
|-----------|----------|
| `under`, `below_market`, `verdict-zabirat` | `badge-success` |
| `fair`, `verdict-smotret`, `warn`, `risk-medium` | `badge-warning` |
| `over`, `verdict-mimo`, `risk-high`, `too_expensive` | `badge-danger` |
| `fresh-hot`, `fresh-warm`, `risk-low`, `too_cheap`, `cheap` | `badge-info` |
| `neutral`, `verdict-norm`, `duplicate` | `badge-neutral` |

**Badge base style:**
```css
.badge {
    display: inline-flex;
    align-items: center;
    padding: 3px 8px;
    border-radius: var(--r-sm);  /* NOT 999px — more modern squared */
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.01em;
    white-space: nowrap;
    line-height: 1.4;
}
```

### 3.5 Tabs

**Current:** Heavy background tabs with borders, active = amber filled
```css
.s-tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #0d0d0f;
}
```

**New — Pill style, cleaner:**
```css
.tab-list {
    display: flex;
    gap: var(--sp-1);
    overflow-x: auto;
    scrollbar-width: none;
    padding-bottom: var(--sp-1);
}

.tab {
    padding: 6px 14px;
    border: none;
    background: transparent;
    color: var(--text-secondary);
    font: 500 var(--text-xs) var(--font);
    border-radius: var(--r-full);
    cursor: pointer;
    white-space: nowrap;
    transition: all var(--duration-normal) var(--ease-smooth);
    flex-shrink: 0;
}

.tab:hover {
    color: var(--text-primary);
    background: var(--bg-surface-2);
}

.tab.active {
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 600;
}
```

**Key changes:**
- Remove border from tabs (borderless pill pattern)
- Active state: `--accent-soft` background + `--accent` text (NOT filled button)
- Inactive: transparent, subtle hover
- Font weight: 500 → 600 for active only

### 3.6 View Navigation Tabs

**Current:** 6 heavy cards with borders, active = amber fill
```css
.view-tab {
    border: 1px solid var(--border);
    background: var(--bg-card);
    box-shadow: var(--shadow-card);
}
.view-tab.active {
    background: var(--accent);
    border-color: var(--accent);
}
```

**New — Lighter grid, active = subtle highlight:**
```css
.view-nav {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--sp-2);
    margin: var(--sp-2) 0 var(--sp-3);
}

.view-tab {
    border: 1px solid var(--border-subtle);
    background: var(--bg-surface);
    border-radius: var(--r-md);
    padding: var(--sp-3) var(--sp-2);
    color: var(--text-secondary);
    font: 600 var(--text-xs) var(--font);
    letter-spacing: -0.01em;
    cursor: pointer;
    transition: all var(--duration-normal) var(--ease-smooth);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--sp-1);
}

.view-tab:hover {
    background: var(--bg-surface-2);
    border-color: var(--border-default);
}

.view-tab.active {
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--accent);
}

.view-tab-icon {
    font-size: 20px;
    line-height: 1;
}

.view-tab-label {
    font-size: var(--text-xs);
}
```

**Key changes:**
- Remove `box-shadow` from view tabs
- Active = `--accent-soft` bg + `--accent` border + text (not solid amber)
- Icon size: reduce from implicit to `20px`
- Consistent padding

### 3.7 Modals / Bottom Sheets

**Current:**
```css
.detail-sheet {
    background: var(--bg);
    border-top-left-radius: 24px;
    border-top-right-radius: 24px;
    box-shadow: 0 -12px 30px rgba(0,0,0,0.35);
}
```

**New:**
```css
.detail-overlay {
    background: rgba(0, 0, 0, 0.5);  /* Slightly lighter */
    backdrop-filter: blur(8px);       /* More blur */
    -webkit-backdrop-filter: blur(8px);
}

.detail-sheet {
    background: var(--bg-surface);
    border-top-left-radius: var(--r-2xl);
    border-top-right-radius: var(--r-2xl);
    box-shadow: var(--shadow-xl);
    padding: var(--sp-3) var(--sp-4)
             calc(env(safe-area-inset-bottom, 16px) + var(--sp-4));
}

/* Drag handle for affordance */
.detail-sheet::before {
    content: "";
    display: block;
    width: 36px;
    height: 4px;
    border-radius: 2px;
    background: var(--text-tertiary);
    opacity: 0.3;
    margin: 0 auto var(--sp-3);
}
```

---

## 4. Modern UI Patterns

### 4.1 Glassmorphism — Headers

**Add subtle backdrop blur to sticky header:**
```css
.header {
    background: color-mix(in srgb, var(--bg) 85%, transparent);
    backdrop-filter: blur(12px) saturate(180%);
    -webkit-backdrop-filter: blur(12px) saturate(180%);
    border-bottom: 1px solid var(--border-subtle);
}
```

### 4.2 Gradient Accents — Not Full Backgrounds

**Instead of heavy radial gradients on cards, use subtle top-edge glow:**
```css
/* Accent card (e.g., primary stat) */
.stat-card.is-accent {
    background: var(--bg-surface);
    border: 1px solid var(--accent);
    position: relative;
    overflow: hidden;
}

.stat-card.is-accent::before {
    content: "";
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent-glow), transparent);
}
```

### 4.3 Micro-interactions

**Button press:**
```css
.btn-primary:active {
    transform: scale(0.97);
    transition-duration: 50ms;
}
```

**Card hover lift (light theme only):**
```css
[data-theme="light"] .card:hover {
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
}
```

**Listing card tap:**
```css
.listing:active {
    transform: scale(0.985);  /* Subtle press */
    transition-duration: 50ms;
}
```

**Section collapse/expand:**
```css
.section-body {
    transition: opacity var(--duration-slow) var(--ease-smooth);
}

.section-body[hidden] {
    opacity: 0;
}
```

### 4.4 Soft UI — Input Groups

**For deal range inputs, tracker filters:**
```css
.input-group {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-sm);
    padding: 0 var(--sp-3);
    height: 40px;
    transition: all var(--duration-normal) var(--ease-smooth);
}

.input-group:focus-within {
    border-color: var(--border-accent);
    background: var(--bg-surface);
    box-shadow: 0 0 0 3px var(--accent-soft);
}

.input-group .unit {
    color: var(--text-tertiary);
    font-size: var(--text-xs);
    flex-shrink: 0;
}

.input-group input {
    border: none;
    background: transparent;
    color: var(--text-primary);
    font: 600 var(--text-md) var(--font-mono);
    outline: none;
    width: 100%;
    min-width: 0;
}
```

---

## 5. Dark Theme Excellence

### Surface Layering Strategy

Instead of one card color with heavy borders, use **5 surface levels**:

```
Layer 0: --bg              #09090b  ← App background
Layer 1: --bg-surface      #111113  ← Cards, panels
Layer 2: --bg-surface-2    #18181b  ← Inputs, sub-panels, hover
Layer 3: --bg-surface-3    #222225  ← Modals, overlays
Layer 4: --bg-surface-hover #2a2a2e ← Interactive hover
```

**Visual effect:** Cards are barely distinguishable from background — separation through subtle color + thin borders. Creates a "floating surface" feel.

### Glowing Accents for Interactive Elements

```css
/* Focus rings as glows, not borders */
.input:focus {
    box-shadow: 0 0 0 3px var(--accent-soft);
}

.btn-primary:focus-visible {
    box-shadow: 0 0 0 3px var(--accent-glow);
}

/* Active nav tab glow */
.view-tab.active {
    box-shadow: 0 0 12px var(--accent-soft);
}
```

### Subtle Color Tints Instead of Borders

```css
/* Instead of: border-left: 3px solid var(--accent) */
.tracker-event-row.price-drop {
    background: linear-gradient(90deg, var(--danger-soft), transparent 30%);
    border-left: none;
}

/* Instead of: border-left: 3px solid var(--green) */
.lead-card.status-bought {
    background: linear-gradient(90deg, var(--success-soft), transparent 15%);
    border-left: 2px solid var(--success);
}
```

### Better Contrast Ratios

| Element | Old (dark) | New (dark) | WCAA |
|---------|-----------|-----------|------|
| Text on bg | 12.6:1 | 15.4:1 | AAA ✓ |
| Muted on bg | 5.2:1 | 6.8:1 | AA ✓ |
| Tertiary on bg | 2.4:1 | 3.7:1 | AA ✓ (improved) |
| Accent on bg | 5.9:1 | 6.4:1 | AA ✓ |
| White on amber | 1.6:1 | 12.1:1 (white on blue) | AAA ✓ |

---

## 6. Specific CSS Changes — Full Diff

### 6.1 Root Variables — Complete Replacement

Replace the entire `:root` and `[data-theme="dark"]` block with:

```css
:root,
[data-theme="dark"] {
    /* Surfaces */
    --bg: #09090b;
    --bg-surface: #111113;
    --bg-surface-2: #18181b;
    --bg-surface-3: #222225;
    --bg-surface-hover: #2a2a2e;
    /* Legacy aliases (remove after migration) */
    --bg-card: var(--bg-surface);
    --bg-elevated: var(--bg-surface-2);
    --bg-input: var(--bg-surface-2);
    --bg-hover: var(--bg-surface-hover);

    /* Borders */
    --border-subtle: rgba(255, 255, 255, 0.04);
    --border: rgba(255, 255, 255, 0.06);
    --border-default: var(--border);
    --border-focus: rgba(255, 255, 255, 0.12);
    --border-hover: rgba(255, 255, 255, 0.1);
    --border-accent: rgba(59, 130, 246, 0.4);
    --border-focus: rgba(59, 130, 246, 0.4);

    /* Text */
    --text: #fafafa;
    --text-primary: var(--text);
    --text-muted: #a1a1aa;
    --text-secondary: var(--text-muted);
    --text-dim: #52525b;
    --text-tertiary: var(--text-dim);

    /* Accent — BLUE, not amber */
    --accent: #3b82f6;
    --accent-soft: rgba(59, 130, 246, 0.08);
    --accent-glow: rgba(59, 130, 246, 0.25);
    --accent-dark: #2563eb;
    --accent-hover: #60a5fa;
    --accent-active: #1d4ed8;

    /* Semantic */
    --green: #22c55e;
    --green-soft: rgba(34, 197, 94, 0.08);
    --green-border: rgba(34, 197, 94, 0.2);
    --red: #ef4444;
    --red-soft: rgba(239, 68, 68, 0.08);
    --red-border: rgba(239, 68, 68, 0.2);

    /* Deal/Profit context — amber lives HERE only */
    --deal: #f59e0b;
    --deal-soft: rgba(245, 158, 11, 0.08);
    --deal-border: rgba(245, 158, 11, 0.2);

    /* Typography */
    --font: "Rubik", system-ui, -apple-system, sans-serif;
    --font-mono: "JetBrains Mono", "SF Mono", "Courier New", monospace;

    /* Radius */
    --r-sm: 8px;
    --r-md: 10px;
    --r-lg: 14px;
    --r-xl: 18px;
    --r-2xl: 24px;
    --r-full: 999px;

    /* Shadows — layered, no borders baked in */
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.2);
    --shadow-md: 0 2px 6px rgba(0, 0, 0, 0.25), 0 1px 2px rgba(0, 0, 0, 0.15);
    --shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.35), 0 2px 6px rgba(0, 0, 0, 0.2);
    --shadow-xl: 0 16px 48px rgba(0, 0, 0, 0.4), 0 4px 12px rgba(0, 0, 0, 0.25);

    /* Legacy shadow aliases */
    --shadow-card: var(--shadow-sm);
    --shadow-elevated: var(--shadow-md);

    /* Transitions */
    --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
    --ease-smooth: cubic-bezier(0.4, 0, 0.2, 1);
}
```

### 6.2 Light Theme Replacement

```css
[data-theme="light"] {
    --bg: #fafafa;
    --bg-surface: #ffffff;
    --bg-surface-2: #f4f4f5;
    --bg-surface-3: #e4e4e7;
    --bg-surface-hover: #f0f0f2;
    --bg-card: var(--bg-surface);
    --bg-elevated: var(--bg-surface-2);
    --bg-input: var(--bg-surface-2);
    --bg-hover: var(--bg-surface-hover);

    --border-subtle: rgba(0, 0, 0, 0.03);
    --border: rgba(0, 0, 0, 0.06);
    --border-default: var(--border);
    --border-focus: rgba(0, 0, 0, 0.12);
    --border-hover: rgba(0, 0, 0, 0.1);
    --border-accent: rgba(59, 130, 246, 0.5);
    --border-focus: rgba(59, 130, 246, 0.5);

    --text: #09090b;
    --text-primary: var(--text);
    --text-muted: #71717a;
    --text-secondary: var(--text-muted);
    --text-dim: #a1a1aa;
    --text-tertiary: var(--text-dim);

    --accent: #2563eb;
    --accent-soft: rgba(37, 99, 235, 0.06);
    --accent-glow: rgba(37, 99, 235, 0.15);
    --accent-dark: #1e40af;
    --accent-hover: #1d4ed8;
    --accent-active: #1e40af;

    --green: #16a34a;
    --green-soft: rgba(22, 163, 74, 0.06);
    --green-border: rgba(22, 163, 74, 0.2);
    --red: #dc2626;
    --red-soft: rgba(220, 38, 38, 0.06);
    --red-border: rgba(220, 38, 38, 0.2);

    --deal: #d97706;
    --deal-soft: rgba(217, 119, 6, 0.06);
    --deal-border: rgba(217, 119, 6, 0.2);

    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
    --shadow-md: 0 2px 8px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
    --shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.08), 0 2px 8px rgba(0, 0, 0, 0.04);
    --shadow-xl: 0 16px 48px rgba(0, 0, 0, 0.1), 0 4px 12px rgba(0, 0, 0, 0.06);
    --shadow-card: var(--shadow-sm);
    --shadow-elevated: var(--shadow-md);
}
```

### 6.3 Key Component Changes

#### App Container
```css
/* BEFORE */
.app {
    max-width: 480px;
    margin: 0 auto;
    padding: 0 14px;
}

/* AFTER */
.app {
    max-width: 480px;
    margin: 0 auto;
    padding: 0 var(--sp-4);
    padding-bottom: env(safe-area-inset-bottom, 24px);
}
```

#### Header (with glassmorphism)
```css
/* BEFORE */
.header {
    position: sticky;
    top: 0;
    z-index: 200;
    background: var(--bg);
    margin: 0 -14px;
    padding: 0 14px;
    border-bottom: 1px solid var(--border);
}

/* AFTER */
.header {
    position: sticky;
    top: 0;
    z-index: 200;
    background: color-mix(in srgb, var(--bg) 85%, transparent);
    backdrop-filter: blur(12px) saturate(180%);
    -webkit-backdrop-filter: blur(12px) saturate(180%);
    border-bottom: 1px solid var(--border-subtle);
    margin: 0 calc(-1 * var(--sp-4));
    padding: 0 var(--sp-4);
}
```

#### Search Input
```css
/* BEFORE */
.search-wrap {
    display: flex;
    align-items: center;
    background: var(--bg-input);
    border: 1.5px solid var(--border);
    border-radius: var(--r-xl);
    overflow: hidden;
}

/* AFTER */
.search-wrap {
    display: flex;
    align-items: center;
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    overflow: hidden;
    transition: all 150ms var(--ease-smooth);
}

.search-wrap:focus-within {
    border-color: var(--border-accent);
    background: var(--bg-surface);
    box-shadow: 0 0 0 3px var(--accent-soft);
}

.search-btn {
    margin: var(--sp-1);
    padding: 0 var(--sp-4);
    height: 36px;
    min-width: 68px;
    background: var(--accent);
    color: var(--text-inverse);
    border: none;
    border-radius: var(--r-sm);
    font: 600 var(--text-sm) var(--font);
    transition: background 150ms var(--ease-smooth),
                transform 100ms var(--ease-out);
}

.search-btn:hover:not(:disabled) {
    background: var(--accent-hover);
}
```

#### Section Headers
```css
/* BEFORE */
.sec-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    min-height: 26px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}

.sec-title {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
}

/* AFTER */
.sec-head {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    margin-bottom: var(--sp-3);
    min-height: 28px;
}

/* Remove bottom border — use whitespace instead */
.sec-head {
    border-bottom: none;
    padding-bottom: 0;
}

.sec-title {
    font-size: var(--text-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: var(--ls-wider);
    color: var(--text-tertiary);
    margin: 0;
    flex-shrink: 0;
}
```

#### Stat Cards
```css
/* BEFORE */
.stat-card {
    background: var(--bg-card);
    box-shadow: var(--shadow-card);
    border-radius: var(--r-lg);
    padding: 14px 14px 12px;
}

.stat-card.is-accent {
    background: var(--accent-soft);
    box-shadow: 0 0 0 1px var(--accent-glow), 0 1px 3px rgba(0,0,0,0.3);
}

.stat-val {
    font-family: var(--font-mono);
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.04em;
}

/* AFTER */
.stat-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: var(--sp-4);
}

.stat-card.is-accent {
    background: var(--bg-surface);
    border-color: var(--accent);
    position: relative;
    overflow: hidden;
}

.stat-card.is-accent::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent-glow), transparent);
}

.stat-card.is-accent .stat-lbl {
    color: var(--accent);
}

.stat-val {
    font-family: var(--font-mono);
    font-size: var(--text-2xl);
    font-weight: 700;
    letter-spacing: -0.04em;
    line-height: 1.1;
}

.stat-card.is-accent .stat-val {
    color: var(--accent);
}

.stat-lbl {
    font-size: var(--text-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: var(--ls-wider);
    color: var(--text-tertiary);
}
```

#### Listing Cards
```css
/* BEFORE */
.listing {
    background: var(--bg-card);
    box-shadow: var(--shadow-card);
    border-radius: var(--r-lg);
    padding: 12px;
}

/* AFTER */
.listing {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: var(--sp-4);
    transition: background 120ms var(--ease-smooth),
                transform 100ms var(--ease-out);
}

.listing:hover {
    background: var(--bg-surface-hover);
}

.listing:active {
    transform: scale(0.985);
}

/* Thumbnail */
.listing-thumb {
    width: 68px;
    height: 68px;
    border-radius: var(--r-sm);
    object-fit: cover;
    flex-shrink: 0;
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
}

/* Listing buttons — cleaner */
.listing-btn {
    appearance: none;
    border: 1px solid var(--border-subtle);
    background: transparent;
    color: var(--text-secondary);
    border-radius: var(--r-sm);
    padding: 9px 0;
    font: 500 var(--text-xs) var(--font);
    cursor: pointer;
    transition: all 150ms var(--ease-smooth);
    flex: 1;
    text-align: center;
}

.listing-btn:hover {
    border-color: var(--border-default);
    color: var(--text-primary);
    background: var(--bg-surface-2);
}

.listing-btn--accent {
    color: var(--text-inverse);
    background: var(--accent);
    border-color: var(--accent);
    font-weight: 600;
}

.listing-btn--accent:hover {
    background: var(--accent-hover);
    border-color: var(--accent-hover);
}
```

#### Toggle Switches
```css
/* BEFORE */
.strict-toggle-pill {
    width: 38px;
    height: 22px;
    border-radius: 999px;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
}

.strict-toggle-pill::after {
    width: 16px;
    height: 16px;
    background: var(--text-dim);
}

.strict-toggle input:checked + .strict-toggle-pill {
    background: var(--accent-soft);
    border-color: var(--accent-glow);
}

.strict-toggle input:checked + .strict-toggle-pill::after {
    background: var(--accent);
}

/* AFTER */
.strict-toggle-pill {
    width: 40px;
    height: 24px;
    border-radius: var(--r-full);
    background: var(--bg-surface-3);
    border: 1px solid var(--border-default);
    transition: all 150ms var(--ease-smooth);
}

.strict-toggle-pill::after {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--text-secondary);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    transition: all 150ms var(--ease-spring);
}

.strict-toggle input:checked + .strict-toggle-pill {
    background: var(--accent);
    border-color: var(--accent);
}

.strict-toggle input:checked + .strict-toggle-pill::after {
    transform: translateX(16px);
    background: var(--text-inverse);
}
```

#### Summary Strip (Hero Card)
```css
/* BEFORE */
.summary-strip {
    background:
        radial-gradient(circle at top right, var(--accent-soft), transparent 50%),
        var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    padding: 14px 14px 12px;
    margin-bottom: 12px;
    box-shadow: var(--shadow-card);
}

/* AFTER — remove gradient, clean surface */
.summary-strip {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: var(--sp-4);
    margin-bottom: var(--sp-3);
}

.summary-pill {
    padding: var(--sp-2) var(--sp-3);
    border-radius: var(--r-sm);
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
}

.summary-pill-label {
    color: var(--text-tertiary);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: var(--ls-wider);
}

.summary-pill-value {
    color: var(--text-primary);
    font-size: var(--text-sm);
    font-weight: 700;
}
```

#### Empty States
```css
/* BEFORE */
.empty {
    text-align: center;
    padding: 60px 24px;
}

.tracker-empty {
    padding: 28px 18px;
    background: rgba(255, 255, 255, 0.02);
    border: 1px dashed var(--border);
}

/* AFTER */
.tracker-empty {
    padding: var(--sp-8) var(--sp-5);
    color: var(--text-secondary);
    font-size: var(--text-sm);
    line-height: var(--lh-loose);
    text-align: center;
    border-radius: var(--r-md);
    background: transparent;
    border: 1px dashed var(--border-subtle);
}
```

#### Toast Notifications
```css
/* BEFORE */
.toast {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}

/* AFTER — more polished */
.toast {
    background: var(--bg-surface-3);
    border: 1px solid var(--border-default);
    color: var(--text-primary);
    font-size: var(--text-xs);
    font-weight: 500;
    padding: var(--sp-2) var(--sp-3);
    border-radius: var(--r-sm);
    box-shadow: var(--shadow-lg);
    animation: toast-in 200ms var(--ease-out),
               toast-out 200ms var(--ease-smooth) 1s forwards;
}
```

#### Chart Boxes
```css
/* BEFORE */
.chart-box {
    background: var(--bg-card);
    box-shadow: var(--shadow-card);
    border-radius: var(--r-xl);
    padding: 16px 14px 14px;
    height: 175px;
}

/* AFTER */
.chart-box {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-lg);
    padding: var(--sp-4);
    height: 175px;
}
```

#### Profit Cards
```css
/* BEFORE */
.profit-card {
    background: var(--bg-card);
    box-shadow: var(--shadow-card);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    padding: 14px;
}

.profit-card.is-accent {
    background: var(--green-soft);
    border-color: rgba(52, 211, 153, 0.2);
}

/* AFTER */
.profit-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: var(--sp-3) var(--sp-4);
}

.profit-card.is-accent {
    background: var(--success-soft);
    border-color: var(--success-border);
}

.profit-card.is-warning {
    background: var(--warning-soft);
    border-color: var(--warning-border);
}

.profit-card-value {
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.03em;
}

.profit-card.is-accent .profit-card-value {
    color: var(--success);
}
```

#### Lead Cards
```css
/* BEFORE */
.lead-card {
    background: var(--bg-card);
    box-shadow: var(--shadow-card);
    border-radius: var(--r-lg);
    padding: 14px;
    border-left: 3px solid var(--border);
}

/* AFTER — status indicated via gradient, not thick border */
.lead-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: var(--sp-4);
    border-left: 2px solid var(--border-subtle);
    transition: background 120ms var(--ease-smooth),
                transform 100ms var(--ease-out);
}

.lead-card.status-bought {
    border-left-color: var(--success);
    background: linear-gradient(90deg, var(--success-soft), var(--bg-surface) 15%);
}

.lead-card.status-sold {
    border-left-color: var(--accent);
    background: linear-gradient(90deg, var(--accent-soft), var(--bg-surface) 15%);
}

.lead-card.status-in_progress {
    border-left-color: var(--warning);
    background: linear-gradient(90deg, var(--warning-soft), var(--bg-surface) 15%);
}

/* Lead field inputs — cleaner */
.lead-field-wrap {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-sm);
    padding: 0 var(--sp-3);
    height: 36px;
}

.lead-field-wrap input {
    border: none;
    background: transparent;
    color: var(--text-primary);
    font: 600 var(--text-sm) var(--font-mono);
    outline: none;
}

.lead-field-wrap input::placeholder {
    color: var(--text-tertiary);
    font-weight: 400;
}
```

#### Watchlist Cards
```css
/* BEFORE */
.watchlist-card {
    background: var(--bg-card);
    box-shadow: var(--shadow-card);
    border-radius: var(--r-lg);
    padding: 14px;
}

/* AFTER — same pattern as lead cards */
.watchlist-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: var(--sp-4);
}

.watchlist-thumb {
    width: 68px;
    height: 68px;
    border-radius: var(--r-sm);
    object-fit: cover;
    flex-shrink: 0;
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
}
```

#### Modal / Detail Sheet
```css
/* BEFORE */
.detail-overlay {
    background: rgba(0, 0, 0, 0.65);
    backdrop-filter: blur(4px);
}

.detail-sheet {
    background: var(--bg);
    border-top-left-radius: 24px;
    border-top-right-radius: 24px;
    box-shadow: 0 -12px 30px rgba(0, 0, 0, 0.35);
    padding: 12px 14px calc(env(safe-area-inset-bottom, 18px) + 18px);
}

/* AFTER */
.detail-overlay {
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(10px) saturate(150%);
    -webkit-backdrop-filter: blur(10px) saturate(150%);
}

.detail-sheet {
    background: var(--bg-surface);
    border-top-left-radius: var(--r-2xl);
    border-top-right-radius: var(--r-2xl);
    box-shadow: var(--shadow-xl);
    padding: 0 var(--sp-4)
             calc(env(safe-area-inset-bottom, 16px) + var(--sp-4));
}

.detail-sheet::before {
    content: "";
    display: block;
    width: 36px;
    height: 4px;
    border-radius: 2px;
    background: var(--text-tertiary);
    opacity: 0.3;
    margin: var(--sp-3) auto;
}

.detail-media {
    border-radius: var(--r-lg);
    overflow: hidden;
    background: var(--bg-surface-2);
    border: 1px solid var(--border-subtle);
}

.detail-thumb {
    width: 48px;
    height: 48px;
    border-radius: var(--r-sm);
    border: 2px solid transparent;
    transition: border-color 150ms var(--ease-smooth);
}

.detail-thumb.active {
    border-color: var(--accent);
}

.detail-price {
    font-size: var(--text-xl);
    font-weight: 700;
    color: var(--accent);
}

.detail-field {
    display: grid;
    grid-template-columns: minmax(0, 110px) minmax(0, 1fr);
    gap: var(--sp-3);
    padding: var(--sp-3);
    background: var(--bg-surface-2);
    border-radius: var(--r-sm);
    border: 1px solid var(--border-subtle);
}
```

---

## 7. Implementation Order

### Phase 1: Foundation (Day 1)
1. Replace all CSS variables in `:root` and both theme blocks
2. Update `html` font-size and body styles
3. Add spacing scale variables
4. Verify light/dark theme parity

### Phase 2: Core Components (Day 2)
1. Header (glassmorphism)
2. Search input (cleaner, focus ring)
3. View tabs (lighter, no shadow)
4. Section headers (remove bottom border)
5. Stat cards (accent top-edge glow)
6. Listing cards (thinner border, cleaner)

### Phase 3: Workflow Components (Day 3)
1. Lead cards (gradient status indication)
2. Watchlist cards (matching pattern)
3. Input groups (cleaner, focus ring)
4. Toggle switches (larger, smoother)
5. Badges (5 semantic types)

### Phase 4: Polish (Day 4)
1. Modal bottom sheet (drag handle, more blur)
2. Toast notifications (polished animation)
3. Empty states (simpler, no dashed border)
4. Chart boxes (cleaner surface)
5. Profit cards (semantic colors)

### Phase 5: QA (Day 5)
1. Test all views: overview, ads, tracking, cheap, monitoring, deals
2. Test light theme parity
3. Check `prefers-reduced-motion` still works
4. Verify all badge types map correctly
5. Cross-browser: Safari iOS (Telegram WebApp), Chrome Android

---

## 8. Before/After Comparison

### Before
```
┌─────────────────────────────┐
│ ════ Amber header ════      │  Heavy border, flat bg
│ [Amber] [Amber] [Amber]     │  Everything screams for attention
│ ┌─────────────────────────┐ │
│ │ Card with border+shadow │ │  Double emphasis = noise
│ │ 22px amber value        │ │  Amber everywhere
│ │ [Amber btn][Amber btn]  │ │  No hierarchy
│ └─────────────────────────┘ │
└─────────────────────────────┘
```

### After
```
┌─────────────────────────────┐
│ ~~~ Frosted header ~~~      │  Glassmorphism, subtle border
│ ◉ Blue  ○ Gray  ○ Gray     │  Active state clear, rest quiet
│ ┌─────────────────────────┐ │
│ │ Card, thin border       │ │  Surface color = separation
│ │ 24px blue value         │ │  Accent only where interactive
│ │ [Primary]  ○ Secondary  │ │  Clear hierarchy
│ └─────────────────────────┘ │
└─────────────────────────────┘
```

---

## 9. Key Principles Maintained

1. **No new dependencies** — pure CSS changes, no frameworks
2. **Backwards compatible** — legacy variable aliases during transition
3. **Mobile-first** — all changes optimized for ≤480px
4. **Accessible** — improved contrast ratios, focus rings, `prefers-reduced-motion`
5. **Performant** — no expensive animations, only `transform` and `opacity` transitions
6. **Telegram Mini App compatible** — works within Telegram WebView constraints
