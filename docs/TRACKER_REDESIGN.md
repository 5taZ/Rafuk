# Автопоиск (Tracker) System Redesign

## Problem Analysis

### Current Limitations
1. **Limited Tracker Visibility**: Users see only basic info (query, filters, last checked time)
2. **No Editing Capability**: Can only create/delete trackers, not modify them
3. **Flat Event Feed**: All events mixed together, no grouping by tracker
4. **No Pause/Resume**: Must delete and recreate to temporarily stop a tracker
5. **Missing Performance Data**: No insight into which trackers are most productive
6. **Basic Event Cards**: No thumbnails, limited metadata, no quick actions
7. **Poor Organization**: Hard to track which tracker generated which events
8. **Limited Filtering**: Only basic event type filtering (all/new/price_drop)

## Proposed Improvements

### 1. Backend Enhancements

#### 1.1 New API Endpoints
```
PATCH   /api/v1/trackers/:id          - Edit tracker configuration
POST    /api/v1/trackers/:id/pause    - Pause a tracker (keep data, stop checks)
POST    /api/v1/trackers/:id/resume   - Resume a paused tracker
GET     /api/v1/trackers/:id/stats    - Get detailed tracker statistics
```

#### 1.2 Enhanced Tracker Model
Add fields to `Tracker` model:
- `paused: bool` - Whether tracker is paused (separate from active)
- `pause_reason: str | None` - Why tracker was paused
- `paused_at: datetime | None` - When tracker was paused
- `total_events: int` - Computed: total events generated
- `last_event_at: datetime | None` - Computed: timestamp of last event

#### 1.3 Enhanced TrackerRead Schema
```python
class TrackerRead(BaseModel):
    # ... existing fields ...
    paused: bool = False
    paused_at: datetime | None = None
    # Computed stats (not stored in DB)
    event_count: int = 0
    new_listings_count: int = 0
    price_drops_count: int = 0
    last_event_at: datetime | None = None
    avg_events_per_day: float = 0.0
```

#### 1.4 Enhanced TrackerEvent Model
Add fields for richer event data:
- `thumbnail: str | None` - Listing thumbnail URL
- `parameters: dict | None` - Key listing parameters (storage, RAM, condition)
- `seller_type: str | None` - Seller type at event time
- `region_name: str | None` - Region at event time

### 2. Frontend Redesign

#### 2.1 Tracking View Layout
```
┌─────────────────────────────────────────┐
│  🔔 Автопоиск                           │
│  5 трекеров • 42 события                │
├─────────────────────────────────────────┤
│  ┌─────────────────────────────────────┐│
│  │  ➕ Новый трекер                    ││
│  │  [Следить за текущим запросом]      ││
│  └─────────────────────────────────────┘│
│                                         │
│  📊 Активные трекеры                    │
│  ┌─────────────────────────────────────┐│
│  │ 🔍 iPhone 15 Pro Max 256GB         ││
│  │ ⚙️ Частник • Новый • Минск         ││
│  │ 📈 18 событий • 3 сегодня          ││
│  │ 🕐 5 мин назад • каждые 15 мин     ││
│  │ [⏸️] [✏️] [🗑️] [📋]               ││
│  └─────────────────────────────────────┘│
│  ┌─────────────────────────────────────┐│
│  │ 🔍 Samsung Galaxy S24 (⏸️ Пауза)   ││
│  │ 📈 8 событий • 0 сегодня           ││
│  │ 🕐 2 ч назад • каждые 30 мин       ││
│  │ [▶️] [✏️] [🗑️] [📋]               ││
│  └─────────────────────────────────────┘│
│                                         │
│  📡 Лента событий                       │
│  [Все (42)] [Новые (28)] [Скидки (14)] │
│  Сортировка: ▼ Новые сверху             │
│  ┌─────────────────────────────────────┐│
│  │ 📱 iPhone 15 Pro Max               ││
│  │ 🔽 Падение цены • 2 450 р. (-150)  ││
│  │ 📍 Минск • Частник • Б/у           ││
│  │ 🕐 12 мин назад • iPhone 15 трекер ││
│  │ [Открыть] [В покупки] [Kufar →]    ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

#### 2.2 Tracker Card Component
Each tracker displays:
- **Header**: Query name + status badge (active/paused/error)
- **Filters**: Key active filters (seller, condition, region)
- **Stats**: 
  - Total events count
  - Events today (or last 24h)
  - Last check time ("X min ago")
  - Check interval
- **Actions**:
  - ⏸️ Pause/▶️ Resume toggle
  - ✏️ Edit configuration
  - 📋 View events (filter feed to this tracker)
  - 🗑️ Delete

#### 2.3 Event Card Enhancements
Each event shows:
- **Thumbnail**: Small image of the listing
- **Title**: Listing title (clickable)
- **Event Type Badge**: 🆕 New listing or 🔽 Price drop
- **Price**: Current price + delta (for price drops)
- **Metadata**: Region, seller type, condition
- **Tracker Source**: Which tracker found this
- **Time**: Relative time ("12 min ago")
- **Quick Actions**:
  - Open listing detail
  - Add to deals (В покупки)
  - Open on Kufar (external link)
  - Add to watchlist

#### 2.4 Event Feed Controls
- **Filter Tabs**: All | New Listings | Price Drops
- **Tracker Filter**: Dropdown to filter by specific tracker
- **Sort Options**: 
  - Newest first (default)
  - Oldest first
  - Price: Low to High
  - Price: High to Low
- **Time Range**: Last hour | Today | Last 7 days | All time
- **Clear Events**: Button to clear all events

#### 2.5 Edit Tracker Modal
Modal dialog with all tracker settings:
- Query (read-only)
- Strict mode toggle
- Min discount %
- Max price (BYN)
- Seller type dropdown
- Condition dropdown
- Region text input
- Config keyword
- Exclude duplicates toggle
- Check interval (15, 30, 60 min)
- [Save] [Cancel] buttons

### 3. Scheduler Improvements

#### 3.1 Pause/Resume Logic
```python
async def check_trackers(...):
    # Only check active AND non-paused trackers
    result = await session.execute(
        select(Tracker).where(
            Tracker.active.is_(True),
            Tracker.paused.is_(False)  # New condition
        )
    )
```

#### 3.2 Enhanced Event Creation
When creating TrackerEvent, populate new fields:
```python
event = TrackerEvent(
    # ... existing fields ...
    thumbnail=ad.get("thumbnail"),
    parameters={
        "storage_gb": insights.storage_gb,
        "ram_gb": insights.ram_gb,
        "config_summary": insights.config_summary,
    },
    seller_type=get_param(ad, "seller_type"),
    region_name=region_label(ad),
)
```

#### 3.3 Tracker Stats Endpoint
```python
@router.get("/trackers/{tracker_id}/stats")
async def get_tracker_stats(tracker_id: int, ...):
    # Count events by type
    # Calculate avg events per day
    # Get last event timestamp
    return TrackerStats(...)
```

### 4. Implementation Plan

#### Phase 1: Backend (API + Database)
1. Add migration for new Tracker fields (paused, paused_at)
2. Add migration for new TrackerEvent fields (thumbnail, parameters, etc.)
3. Create PATCH /trackers/:id endpoint
4. Create POST /trackers/:id/pause and /resume endpoints
5. Enhance GET /trackers to include computed stats
6. Update scheduler to respect paused state
7. Update scheduler to populate enriched event data

#### Phase 2: Frontend UI Components
1. Redesign tracker cards with stats
2. Add pause/resume buttons
3. Create edit tracker modal
4. Enhance event cards with thumbnails and metadata
5. Add tracker source label to events
6. Improve event filtering and sorting

#### Phase 3: Integration & Polish
1. Connect frontend to new API endpoints
2. Test pause/resume workflow
3. Test edit tracker workflow
4. Add loading states and error handling
5. Optimize rendering performance
6. Add animations for smooth transitions

### 5. Benefits

✅ **Better User Experience**: Clear visibility into tracker performance
✅ **More Control**: Edit, pause, resume without deleting
✅ **Organized Feed**: Events grouped and filtered logically
✅ **Rich Information**: Thumbnails, metadata, quick actions
✅ **Performance Insights**: Know which trackers work best
✅ **Flexible Management**: Fine-tune trackers without recreation

### 6. Migration Strategy

No breaking changes - all new fields are optional with sensible defaults:
- `paused` defaults to `false`
- `paused_at` defaults to `null`
- New event fields default to `null`
- Enhanced TrackerRead schema computes stats on-demand

Existing trackers continue to work seamlessly with enhanced functionality.
