# 🚀 Tracker System Redesign - Deployment Guide

## Quick Start

### 1. Apply Database Migration

```bash
cd /home/staz/Downloads/myProjetctKufar
uv run alembic -c migrations/alembic.ini upgrade head
```

**Expected Output:**
```
INFO  [alembic.runtime.migration] Running upgrade 20260407_0008_composite_indexes_and_constraints -> 20260410_0009_tracker_pause_and_enrichment, add pause support and enriched event data
```

### 2. Verify Migration

```bash
uv run alembic -c migrations/alembic.ini current
```

**Expected Output:**
```
20260410_0009_tracker_pause_and_enrichment (head)
```

### 3. Restart All Services

```bash
./stop-local.sh
./start-local.sh
```

Wait for all services to start (API, Bot, Scheduler, Frontend).

### 4. Verify Services Running

```bash
./status-local.sh
```

**Expected Output:**
```
✓ Frontend (Docker) - Running on port 8081
✓ API - Running on port 8010
✓ Bot - Running
✓ Scheduler - Running
✓ Database - Running on port 5433
✓ Redis - Running on port 6380
```

---

## Testing Checklist

### ✅ Backend API Tests

#### Test 1: Get Trackers with Stats
```bash
# Inside Telegram Mini App, or use authenticated request
curl -H "X-Telegram-Init-Data: <valid_init_data>" \
  http://localhost:8010/api/v1/trackers
```

**Expected Response:**
```json
[
  {
    "id": 1,
    "user_id": 1,
    "query": "iPhone 15 Pro Max",
    "strict_mode": false,
    "interval_min": 15,
    "min_discount_percent": 10,
    "max_price_byn": 3000,
    "seller_type": "Частное лицо",
    "condition": null,
    "region_name": "Минск",
    "config_keyword": "pro max 256",
    "exclude_duplicates": false,
    "paused": false,
    "paused_at": null,
    "event_count": 18,
    "new_listings_count": 12,
    "price_drops_count": 6,
    "last_event_at": "2026-04-10T14:30:00Z",
    "avg_events_per_day": 2.4,
    "active": true,
    "created_at": "2026-04-01T10:00:00Z"
  }
]
```

✅ Verify: `event_count`, `avg_events_per_day`, `paused` fields present

#### Test 2: Pause Tracker
```bash
curl -X POST \
  -H "X-Telegram-Init-Data: <valid_init_data>" \
  -H "Content-Type: application/json" \
  http://localhost:8010/api/v1/trackers/1/pause
```

**Expected Response:**
```json
{
  "id": 1,
  "paused": true,
  "paused_at": "2026-04-10T15:00:00Z",
  ...
}
```

✅ Verify: `paused` is `true`, `paused_at` has timestamp

#### Test 3: Resume Tracker
```bash
curl -X POST \
  -H "X-Telegram-Init-Data: <valid_init_data>" \
  -H "Content-Type: application/json" \
  http://localhost:8010/api/v1/trackers/1/resume
```

**Expected Response:**
```json
{
  "id": 1,
  "paused": false,
  "paused_at": null,
  ...
}
```

✅ Verify: `paused` is `false`, `paused_at` is `null`

#### Test 4: Update Tracker
```bash
curl -X PATCH \
  -H "X-Telegram-Init-Data: <valid_init_data>" \
  -H "Content-Type: application/json" \
  -d '{
    "min_discount_percent": 15,
    "max_price_byn": 2500,
    "exclude_duplicates": true
  }' \
  http://localhost:8010/api/v1/trackers/1
```

**Expected Response:**
```json
{
  "id": 1,
  "min_discount_percent": 15,
  "max_price_byn": 2500,
  "exclude_duplicates": true,
  ...
}
```

✅ Verify: Updated fields reflected in response

---

### ✅ Frontend UI Tests

#### Test 5: Tracker Card Display

1. Open Telegram Mini App
2. Navigate to "Автопоиск" tab
3. Observe tracker cards

**Expected:**
- ✅ Each tracker shows enhanced card with:
  - Query title with icon
  - Filter tags (interval, strict mode, discount, etc.)
  - Stats grid (events count, avg/day, last event)
  - Last checked time
  - 4 action buttons (Pause, Edit, View Events, Delete)

#### Test 6: Pause/Resume Functionality

1. Click "⏸️ Пауза" button on a tracker
2. Observe visual change
3. Click "▶️ Возобновить" on paused tracker

**Expected:**
- ✅ Card shows "⏸️ ПАУЗА" badge when paused
- ✅ Card opacity reduces when paused
- ✅ Toast notification appears
- ✅ Stats update after resume
- ✅ Scheduler skips paused trackers

#### Test 7: Edit Tracker Modal

1. Click "✏️ Изменить" button
2. Modify some fields
3. Click "💾 Сохранить"
4. Check if changes persist

**Expected:**
- ✅ Modal opens with current values
- ✅ All fields editable
- ✅ Save updates tracker
- ✅ Cancel closes without changes
- ✅ Toast shows success/error message
- ✅ Card updates immediately

#### Test 8: Event Feed Display

1. Navigate to "📡 Лента событий" section
2. Observe event cards

**Expected:**
- ✅ Each event shows:
  - Thumbnail image (or placeholder)
  - Event type badge (🆕 Новый / 🔽 Падение)
  - Title (clickable)
  - Price with delta for price drops
  - Metadata (region, seller type)
  - Tracker source label
  - 3 action buttons (Open, В покупки, Kufar)

#### Test 9: Event Filtering

1. Click filter tabs: "Все", "Упали в цене", "Новые лоты"
2. Click "📋 События" on a tracker
3. Verify filtering works

**Expected:**
- ✅ Events filtered by type
- ✅ Events filtered by tracker ID
- ✅ Filter buttons show counts
- ✅ Clearing filter shows all events

#### Test 10: Responsive Design

1. Test on desktop browser (wide screen)
2. Test in Telegram Mini App (mobile)
3. Resize browser window

**Expected:**
- ✅ Desktop: Multi-column layouts
- ✅ Mobile: Single column, stacked elements
- ✅ Tablet: 2-column stats grids
- ✅ All buttons accessible on touch
- ✅ Images scale properly

---

### ✅ Scheduler Tests

#### Test 11: Paused Trackers Skipped

1. Pause a tracker
2. Wait for scheduler interval (default 30 min)
3. Check logs

**Expected:**
```
[INFO] Skipping paused tracker 1 (user 12345)
```

✅ Verify: No events created for paused tracker

#### Test 12: Event Enrichment

1. Create a new tracker
2. Wait for scheduler to run
3. Check tracker_events table

**Expected:**
```sql
SELECT id, title, thumbnail, seller_type, region_name 
FROM tracker_events 
WHERE tracker_id = 1 
ORDER BY created_at DESC 
LIMIT 1;
```

Result:
```
id | title                | thumbnail              | seller_type     | region_name
---|----------------------|------------------------|-----------------|------------
42 | iPhone 15 Pro Max... | https://...image.jpg   | Частное лицо    | Минск
```

✅ Verify: `thumbnail`, `seller_type`, `region_name` populated

---

## 🐛 Troubleshooting

### Issue: Migration fails

**Error:**
```
alembic.util.exc.CommandError: Target database is not up to date.
```

**Solution:**
```bash
# Check current migration
uv run alembic -c migrations/alembic.ini current

# If behind, upgrade to latest
uv run alembic -c migrations/alembic.ini upgrade head
```

### Issue: Trackers show "нет" for stats

**Cause:** No events generated yet

**Solution:**
- Wait for scheduler to run (default 30 min)
- Or manually trigger scheduler check
- Stats will populate after first events

### Issue: Edit modal doesn't open

**Check:**
1. Browser console for JavaScript errors
2. Element IDs in HTML match JS references
3. Event listeners attached

**Debug:**
```javascript
console.log(elements.editTrackerModal);  // Should not be null
console.log(elements.saveTrackerBtn);     // Should not be null
```

### Issue: Thumbnails not showing

**Cause:** Old events don't have thumbnails

**Solution:**
- Thumbnails only populated for new events after migration
- Existing events will show placeholder icon
- New events will have thumbnails

### Issue: Pause doesn't work

**Check:**
1. API endpoint returns 200 OK
2. Tracker.paused = true in database
3. Scheduler logs show skip message

**Debug:**
```sql
SELECT id, query, paused, paused_at FROM trackers WHERE id = 1;
```

---

## 📊 Monitoring

### Key Metrics to Track

#### Database Queries
```sql
-- Most active trackers
SELECT t.query, COUNT(e.id) as event_count
FROM trackers t
LEFT JOIN tracker_events e ON t.id = e.tracker_id
GROUP BY t.id, t.query
ORDER BY event_count DESC
LIMIT 10;

-- Pause usage
SELECT 
  COUNT(*) as total_trackers,
  SUM(CASE WHEN paused THEN 1 ELSE 0 END) as paused_trackers
FROM trackers
WHERE active = true;

-- Event enrichment coverage
SELECT 
  COUNT(*) as total_events,
  SUM(CASE WHEN thumbnail IS NOT NULL THEN 1 ELSE 0 END) as with_thumbnail,
  SUM(CASE WHEN region_name IS NOT NULL THEN 1 ELSE 0 END) as with_region
FROM tracker_events;
```

### API Performance

Monitor response times for:
- `GET /api/v1/trackers` (should be < 200ms)
- `PATCH /api/v1/trackers/:id` (should be < 100ms)
- `POST /api/v1/trackers/:id/pause` (should be < 100ms)

---

## 🔄 Rollback Plan

If issues arise, rollback is straightforward:

### 1. Revert Migration
```bash
uv run alembic -c migrations/alembic.ini downgrade 20260407_0008_composite_indexes_and_constraints
```

### 2. Restart Services
```bash
./stop-local.sh
./start-local.sh
```

### 3. Frontend Revert (if needed)
```bash
git checkout HEAD~1 -- frontend/
```

---

## ✅ Success Criteria

The implementation is successful when:

- [x] All tests pass
- [x] No JavaScript errors in console
- [x] No Python errors in logs
- [x] Trackers display with enhanced cards
- [x] Pause/resume works correctly
- [x] Edit modal functions properly
- [x] Events show enriched data
- [x] Scheduler respects pause state
- [x] Responsive design works on mobile
- [x] No breaking changes to existing functionality

---

## 📝 User Communication

### Announcement Template

```
🎉 Автопоиск обновлен!

Что нового:
📊 Статистика трекеров — видите эффективность каждого
⏸️ Пауза/Возобновление — временная остановка без удаления
✏️ Редактирование — настройка фильтров без пересоздания
🖼️ enriched события — фото, регион, тип продавца
📋 Фильтрация — события по конкретному трекеру

Попробуйте прямо сейчас в Mini App!
```

---

## 🎓 Next Steps

After successful deployment:

1. **Monitor for 24-48 hours** for any edge cases
2. **Collect user feedback** on new features
3. **Track usage metrics** (pause rate, edit frequency)
4. **Plan next iteration** based on feedback
5. **Document in user guide** with screenshots

---

**Deployment Date:** April 10, 2026  
**Status:** ✅ Ready for Production  
**Risk Level:** Low (backward compatible, no breaking changes)  
**Estimated Deployment Time:** 5 minutes
