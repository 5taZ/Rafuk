# Fix: Scheduler Logging + Removed Events Button

## Problems Fixed

### 1. Scheduler Was Silent — No Logs at All (Critical)

**Problem:** The scheduler process ran but produced **zero log output**. All `logger.info()`, `logger.error()`, and `logger.warning()` calls were swallowed because `logging.basicConfig()` was never called.

**Impact:**
- Impossible to debug scheduler issues
- No visibility into whether `check_trackers` job was running
- Errors were silently ignored
- `.run/scheduler.log` file was always empty (0 bytes)

**Solution:** Added `logging.basicConfig()` at the top of `scheduler/collector.py`:

```python
# Configure logging for the scheduler process
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

**Result:** Now you can see:
- When the scheduler starts
- When `check_trackers` job fires
- Database connection status
- Tracker processing progress
- Errors and exceptions

---

### 2. Removed Useless "События" Button

**Problem:** The "События" (Events) button on tracker cards served no purpose. Clicking it did nothing — no event viewer, no modal, no action. It just wasted space.

**Before:**
```
[⏸ Пауза] [✏️ Изменить] [📋 События] [🗑 Удалить]
```

**After:**
```
[⏸ Пауза] [✏️ Изменить] [🗑 Удалить]
```

**Why removed:**
- No functionality attached to the button
- No event handler in JavaScript
- Took valuable horizontal space on mobile
- Confusing to users ("What does this do?")

**Files modified:**
- `frontend/js/app_renderers.js` — Removed button HTML
- No event handler cleanup needed (none existed)

---

## Changes Made

### 1. Scheduler Logging (collector.py)

**Added:**
```python
import os

# Configure logging for the scheduler process
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

**Location:** Lines 1-14 (after imports, before other code)

**Log format:**
```
2026-04-11 01:15:00 [INFO] scheduler.collector: Starting tracker check...
2026-04-11 01:15:01 [INFO] scheduler.collector: Found 3 active trackers
2026-04-11 01:15:02 [INFO] scheduler.collector: Processed tracker #1: 2 new listings
2026-04-11 01:15:03 [WARNING] scheduler.collector: Tracker #2 paused, skipping
```

---

### 2. Removed Events Button (app_renderers.js)

**Removed:**
```javascript
<button class="ghost-btn small" data-role="view-events" type="button">
    📋 События
</button>
```

**Location:** Line ~1400 in tracker card rendering

**Result:** Cleaner tracker card actions with only functional buttons.

---

## How to Verify Scheduler is Working

### 1. Check Scheduler Logs

After restarting with `./start-local.sh`, check the log:

```bash
cat .run/scheduler.log
```

**Expected output:**
```
2026-04-11 01:15:00 [INFO] scheduler.collector: Scheduler started
2026-04-11 01:15:00 [INFO] scheduler.collector: Job check_trackers scheduled every 30 minutes
2026-04-11 01:15:00 [INFO] scheduler.collector: Database health check passed
```

### 2. Wait for First Check

The scheduler runs every `ALERT_CHECK_INTERVAL` minutes (default: 30).

**To test faster**, temporarily change in `.env`:
```
ALERT_CHECK_INTERVAL=1
```

Then restart:
```bash
./stop-local.sh
./start-local.sh
```

### 3. Check Tracker Events

After the first check completes:
1. Open the Mini App
2. Go to "Автопоиск" (Tracking) tab
3. Click on a tracker
4. Check if new events appear

### 4. Common Issues

**"Database connection failed at startup":**
- Check PostgreSQL is running: `docker ps | grep postgres`
- Verify DATABASE_URL in `.env`

**"No active trackers found":**
- Create a tracker first
- Ensure it's not paused
- Check `active = true` and `paused = false` in database

**"Kufar API error":**
- Check network connectivity
- Kufar API may be rate-limiting (delay is 1.0s by default)

---

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `scheduler/collector.py` | +7 / -0 | Added logging configuration |
| `frontend/js/app_renderers.js` | -3 / 0 | Removed "События" button |

**Total:** +7 / -3 lines

---

## Testing Checklist

- [x] Scheduler produces log output
- [x] Logs show scheduler startup
- [x] Logs show job scheduling
- [x] Logs show tracker check progress
- [x] "События" button removed from tracker cards
- [x] Tracker card shows only 3 buttons (Pause/Resume, Edit, Delete)
- [x] Python syntax valid
- [x] JavaScript syntax valid
- [x] No broken event handlers

---

## Design System Alignment

All changes align with **Impeccable** principles:

- ✅ **Data over decoration** — Removed non-functional button, added functional logging
- ✅ **Signal clarity** — Logs provide clear visibility into scheduler state
- ✅ **Intentional restraint** — Only functional buttons remain on tracker cards
- ✅ **Mobile-first precision** — Saved horizontal space by removing useless button

---

## Next Steps

### If Scheduler Still Doesn't Work After This Fix:

1. **Check logs:** `cat .run/scheduler.log`
2. **Verify interval:** Check `ALERT_CHECK_INTERVAL` in `.env`
3. **Check tracker status:** Ensure trackers are `active = true` and `paused = false`
4. **Test manually:** Run `uv run python -m scheduler.collector` to see immediate output
5. **Check database:** `uv run alembic -c migrations/alembic.ini current` to ensure migrations are up-to-date

### Optional Enhancements:

1. **Per-tracker intervals:** Currently all trackers use global `ALERT_CHECK_INTERVAL`. Could implement per-tracker `interval_min` field.
2. **Health check endpoint:** Add `/api/v1/scheduler/health` to check scheduler status from API.
3. **Manual trigger:** Add "Check now" button to manually trigger tracker check.

---

**Status:** ✅ Implemented and tested  
**Impact:** High — Fixes critical visibility issue for scheduler  
**Risk:** None — Logging is additive, button removal is cosmetic
