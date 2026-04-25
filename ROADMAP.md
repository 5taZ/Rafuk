# 📋 Техническое задание: Превращение Rafuks в полноценный помощник перекупа

**Дата:** Апрель 2025  
**Проект:** Rafuks — Telegram Mini App для анализа Kufar.by  
**Цель:** Добавить критически важные функции для полноценной работы перекупа

---

## 🎯 ОБЗОР ПРОЕКТА

### Текущее состояние
Приложение уже имеет мощный функционал:
- ✅ Аналитика рынка (медиана, среднее, min/max)
- ✅ Трекеры с фильтрами и уведомлениями
- ✅ Вотчлист для отслеживания конкретных объявлений
- ✅ Лиды/Покупки — базовый CRM пайплайн
- ✅ Opportunity Board — лучшие предложения
- ✅ История цен по запросам
- ✅ Сравнение запросов
- ✅ География и сегменты рынка
- ✅ Telegram бот для уведомлений

### Чего НЕ ХВАТАЕТ
Для полноценного помощника перекупа не хватает:
- ❌ Учёта расходов на сделку (доставка, ремонт, комиссии)
- ❌ Расчёта реальной прибыли (с учётом расходов)
- ❌ Расширенного пайплайна сделок (переговоры → куплено → продажа)
- ❌ Аналитики эффективности (ROI, win rate, история)
- ❌ Умных алертов по порогу цены
- ❌ Детекции рисков/скама на объявлениях
- ❌ Скорости рынка (как быстро продаются товары)
- ❌ Экспорта данных (CSV/Excel)
- ❌ Напоминаний для сделок
- ❌ Извлечения телефонов продавцов и управления контактами

---

## 🔴 ПРИОРИТЕТ 1 (P0) — КРИТИЧНО

### 1.1 Учёт расходов на сделку

**Проблема:** Перекуп не знает реальную себестоимость товара. Цена покупки 1200 BYN + доставка 30 BYN + ремонт 50 BYN = 1280 BYN реальная стоимость.

**Решение:** Добавить модель расходов, связанных с лидом.

#### Бэкенд

**Новая модель `DealExpense`:**
```python
# api/models.py
class DealExpense(Base):
    __tablename__ = "deal_expenses"
    
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("lead_items.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    expense_type = Column(String(32), nullable=False)  # delivery, repair, commission, other
    amount_byn = Column(Float, nullable=False)
    notes = Column(String(255), nullable=True)
    expense_date = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
```

**Новые схемы:**
```python
# api/schemas.py
class ExpenseCreate(BaseModel):
    expense_type: str  # delivery, repair, commission, other
    amount_byn: float
    notes: str | None = None
    expense_date: datetime | None = None

class ExpenseRead(ExpenseCreate):
    id: int
    lead_id: int
    created_at: datetime
```

**Новые API Endpoints:**
```
POST   /api/v1/leads/{lead_id}/expenses          # Добавить расход
GET    /api/v1/leads/{lead_id}/expenses          # Список расходов
PATCH  /api/v1/leads/{lead_id}/expenses/{id}     # Редактировать расход
DELETE /api/v1/leads/{lead_id}/expenses/{id}     # Удалить расход
```

**Логика:**
- При добавлении расхода — валидировать `expense_type` из списка разрешенных
- `amount_byn` должен быть > 0
- Только владелец лида может добавлять/редактировать расходы

#### Фронтенд

**UI в разделе "Покупки":**
- На карточке сделки — кнопка "💰 Расходы"
- При клике — модалка со списком расходов:
  - Строки: тип (🚚//💳), сумма, дата, заметка
  - Кнопка "+" для добавления
  - Итого расходов вверху
- Форма добавления:
  - Тип: dropdown (Доставка / Ремонт / Комиссия / Другое)
  - Сумма: number input
  - Дата: date picker (по умолчанию сегодня)
  - Заметка: text input (опционально)

---

### 1.2 Полный Profit/Loss трекинг

**Проблема:** Сейчас видно только цену покупки и целевую цену продажи. Нет реальной прибыли.

**Решение:** Расширить модель `LeadItem` и добавить расчёт P/L.

#### Бэкенд

**Расширение модели `LeadItem`:**
```python
# api/models.py — добавить поля в LeadItem
class LeadItem(Base):
    # ... существующие поля ...
    
    # НОВЫЕ ПОЛЯ:
    sold_price_byn = Column(Float, nullable=True)  # Цена продажи
    sold_at = Column(DateTime, nullable=True)       # Дата продажи
```

**Расширение схемы `LeadRead`:**
```python
# api/schemas.py
class LeadRead(BaseModel):
    # ... существующие поля ...
    sold_price_byn: float | None = None
    sold_at: datetime | None = None
    total_expenses_byn: float = 0.0          # Вычисляется
    actual_profit_byn: float | None = None   # Вычисляется
    roi_percent: float | None = None         # Вычисляется
```

**Логика вычисления (в сервисе):**
```python
# При сериализации лида:
total_expenses = sum(e.amount_byn for e in lead.expenses)
all_in_cost = lead.price_byn + total_expenses

if lead.sold_price_byn is not None:
    actual_profit = lead.sold_price_byn - all_in_cost
    roi_percent = (actual_profit / all_in_cost) * 100 if all_in_cost > 0 else 0
else:
    actual_profit = None
    roi_percent = None
```

**Расширить `LeadUpdate`:**
```python
class LeadUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    target_resale_byn: float | None = None
    sold_price_byn: float | None = None  # НОВОЕ
```

#### Фронтенд

**UI в карточке сделки:**
1. Когда статус = `completed` (Завершено):
   - Показать поле "Цена продажи" (редактируемое)
   - После ввода — автоматически показать:
     - Куплено: X BYN
     - Расходы: Y BYN
     - Продано: Z BYN
     - **Прибыль: +450 BYN (32%)** — зелёный бейдж если >0, красный если <0

2. На карточке в списке (compact view):
   - Добавить бейдж прибыли справа: `+450 (32%)` зелёным или `-120 (8%)` красным

3. Вверху раздела "Покупки" — сводная карточка:
   ```
   ┌─────────────────────────────────────┐
   │  Вложено: 12,400 │ Выручка: 15,800  │
   │  Прибыль: +3,400 │ ROI: 27%         │
   │  Сделок: 24      │ Win rate: 79%    │
   └─────────────────────────────────────┘
   ```

---

### 1.3 Расширенный пайплайн сделок

**Проблема:** Текущие статусы (`new`, `in_progress`, `deferred`, `done`) слишком грубые. Перекуп нуждается в детализации этапов.

**Решение:** Добавить промежуточные статусы и визуализацию.

#### Бэкенд

**Расширить допустимые статусы:**
```python
# api/models.py — обновить комментарий/валидацию
VALID_STATUSES = [
    "new",          # Новый
    "reviewing",    # На проверке
    "negotiating",  # Переговоры
    "bought",       # Куплено
    "reselling",    # На продаже
    "sold",         # Продано
    "completed",    # Завершено
    "cancelled",    # Отменено
]
```

**Логика переходов (опционально):**
```python
# api/services/pipeline.py
VALID_TRANSITIONS = {
    "new": ["reviewing", "cancelled"],
    "reviewing": ["negotiating", "bought", "cancelled"],
    "negotiating": ["bought", "cancelled"],
    "bought": ["reselling"],
    "reselling": ["sold"],
    "sold": ["completed"],
}
```

#### Фронтенд

**Визуальный степпер в разделе "Покупки":**
```
[Новый 3] → [Проверка 2] → [Переговоры 5] → [Куплено 1] → [На продаже 2] → [Завершено 8]
```

- Каждый этап — кликабельная кнопка с бейджем количества
- Активный этап выделен оранжевым
- Пройденные этапы — зелёная галочка
- Будущие — серые

**Фильтры по этапам:**
- Заменить текущие filter-tabs на полный набор статусов
- Добавить "Отменено" в фильтры

---

### 1.4 Аналитика сделок и история

**Проблема:** Нет возможности посмотреть общую статистику эффективности.

**Решение:** Добавить дашборд аналитики в раздел "Покупки".

#### Бэкенд

**Новый endpoint:**
```
GET /api/v1/deals/analytics?days={n}
```

**Ответ:**
```json
{
  "total_deals": 24,
  "completed_deals": 18,
  "win_rate": 0.79,
  "total_invested": 12400,
  "total_revenue": 15800,
  "net_profit": 3400,
  "avg_roi": 27.4,
  "avg_time_to_sell_days": 5.2,
  "profit_by_week": [
    {"week": "2024-W01", "profit": 450},
    {"week": "2024-W02", "profit": 820}
  ],
  "recent_deals": [
    {
      "id": 1,
      "title": "iPhone 15 128GB",
      "buy_price": 1200,
      "expenses": 80,
      "sell_price": 1800,
      "profit": 520,
      "roi": 40.6,
      "sold_at": "2024-01-22"
    }
  ]
}
```

**Логика:**
- `win_rate` = profitable_deals / completed_deals
- `profitable` = sold_price > (buy_price + total_expenses)
- `avg_roi` = средний ROI по завершённым сделкам
- `profit_by_week` = группировка по ISO неделе

#### Фронтенд

**Секция "📈 Аналитика сделок" под Opportunity Board:**
- 4 карточки-метрики в ряд (или 2×2 на мобильных):
  - Всего сделок
  - Win rate %
  - Чистая прибыль
  - Средний ROI
- Мини-график прибыли по неделям (Chart.js)
- Таблица последних 10 сделок:
  - Дата | Товар | Куплено | Продано | Прибыль | ROI
  - Сортировка, фильтрация
  - Клик → модалка с деталями

---

## 🟡 ПРИОРИТЕТ 2 (P1) — ВАЖНО

### 2.1 Умные алерты по порогу цены

**Проблема:** Трекер уведомляет о любом падении цены, но перекуп хочет получать уведомления только когда цена падает ниже определённого порога.

**Решение:** Добавить поля порога в модель `Tracker`.

#### Бэкенд

**Расширение модели `Tracker`:**
```python
# api/models.py — добавить поля
class Tracker(Base):
    # ... существующие поля ...
    alert_price_threshold = Column(Float, nullable=True)      # Уведомить если цена ≤ X
    alert_discount_percent = Column(Float, nullable=True)     # Уведомить при скидке от X%
```

**Логика в scheduler:**
```python
# scheduler/collector.py — при проверке трекера:
if tracker.alert_price_threshold:
    for ad in ads:
        if ad.price_byn <= tracker.alert_price_threshold:
            create_tracker_event(tracker, "price_threshold_alert", ad)

if tracker.alert_discount_percent:
    for ad in ads:
        discount = ((median - ad.price_byn) / median) * 100
        if discount >= tracker.alert_discount_percent:
            create_tracker_event(tracker, "discount_alert", ad)
```

**Новый тип события:**
```python
# TrackerEvent.event_type — добавить значения:
# - price_threshold_alert
# - discount_alert
```

#### Фронтенд

**Форма создания трекера — добавить секцию "🔔 Алерты":**
```
┌─────────────────────────────────┐
│  🔔 Алерты                      │
│  Уведомить если цена ≤ ___ BYN  │
│  Уведомить при скидке от ___ %  │
└─────────────────────────────────┘
```

**В ленте событий:**
- Отдельный бейдж 🎯 для threshold-алертов
- Фильтр "Порог цены"

---

### 2.2 Детекция рисков на объявлениях

**Проблема:** Перекуп не знает, есть ли риски на конкретном объявлении (скам, слишком низкая цена, новый продавец).

**Решение:** Добавить сервис анализа рисков и бейджи на карточках.

#### Бэкенд

**Новый сервис:**
```python
# api/services/risk_detector.py
def detect_risks(ad: dict, market_stats: dict, seller_info: dict | None = None) -> list[dict]:
    risks = []
    
    # 1. Слишком дешёвое (>40% ниже медианы)
    if ad.price_byn < market_stats.median * 0.6:
        risks.append({
            "type": "too_cheap",
            "level": "high",
            "message": "Цена >40% ниже медианы"
        })
    
    # 2. Новый продавец (<2 активных объявлений)
    if seller_info and seller_info.active_listings < 2:
        risks.append({
            "type": "new_seller",
            "level": "medium",
            "message": "Новый продавец"
        })
    
    # 3. Подозрительные слова в описании
    suspicious_words = ["предоплата", "на карту", "перевод", "аванс"]
    if any(w in (ad.description or "").lower() for w in suspicious_words):
        risks.append({
            "type": "suspicious_desc",
            "level": "medium",
            "message": "Подозрительное описание"
        })
    
    # 4. Дубль (тот же продавец, тот же товар за 7 дней)
    if seller_info and seller_info.duplicate_within_7d:
        risks.append({
            "type": "duplicate",
            "level": "high",
            "message": "Дубль от того же продавца"
        })
    
    return risks
```

**Расширить `ListingDetailResponse`:**
```python
# api/schemas.py
class ListingDetailResponse(BaseModel):
    # ... существующие поля ...
    risk_score: str | None = None          # low, medium, high
    risk_factors: list[dict] = []          # Список рисков
```

**Интеграция в `listing_detail.py`:**
```python
# Вызвать detect_risks() и добавить в ответ
risks = detect_risks(ad, market_stats, seller_info)
risk_score = "high" if any(r["level"] == "high" for r in risks) else "medium" if risks else "low"
```

#### Фронтенд

**Бейджи на карточках объявлений:**
- 🔴 `Слишком дёшево` (красный фон)
- 🟡 `Новый продавец` (жёлтый фон)
- 🟡 `Подозрительно` (жёлтый фон)
- 🔴 `Дубль` (красный фон)

**В модалке объявления:**
- Блок "⚠️ Оценка рисков" с детальным объяснением
- Score: Низкий / Средний / Высокий риск

---

### 2.3 Скорость рынка (Market Velocity)

**Проблема:** Перекуп не знает, как быстро продаются товары в данной категории. Дешёвый товар бесполезен, если его никто не купит 3 месяца.

**Решение:** Анализировать `QueryListingState` для определения скорости продаж.

#### Бэкенд

**Новый endpoint:**
```
GET /api/v1/market-velocity?query={q}&days={n}
```

**Логика:**
```python
# Анализировать QueryListingState:
# - Средний возраст объявления = среднее время от first_seen до last_seen
# - % объявлений старше 30 дней = "зависшие"
# - Velocity rating:
#   - 🟢 Быстро: avg age < 7 дней
#   - 🟡 Средне: 7-14 дней
#   - 🔴 Медленно: > 14 дней
```

**Ответ:**
```json
{
  "query": "iphone 15",
  "avg_listing_age_days": 5.2,
  "stale_percent": 12,
  "listings_per_day": 40,
  "velocity_rating": "fast"
}
```

#### Фронтенд

**Новая панель в разделе "Обзор":**
```
┌─────────────────────────────────┐
│ ⚡ Скорость рынка: 🟢 Быстро    │
│ Среднее время продажи: 5 дней   │
│ Зависших объявлений: 12%        │
│ Объявлений в день: ~40          │
└─────────────────────────────────┘
```

---

### 2.4 Улучшения Telegram бота

**Проблема:** Бот имеет базовые команды, но перекуп хочет больше функционала прямо в чате.

**Решение:** Добавить новые команды и улучшить уведомления.

#### Новые команды:
```python
# bot/handlers/analytics.py

@router.message(Command("deals"))
async def cmd_deals(message: Message):
    """Сводка активных сделок"""
    # GET /api/v1/leads с фильтрацией по status != completed
    # Ответ: "Активные сделки: Переговоры — 3, Куплено — 1, На продаже — 2"

@router.message(Command("profit"))
async def cmd_profit(message: Message):
    """Прибыль за месяц"""
    # GET /api/v1/deals/analytics?days=30
    # Ответ: "📊 Январь 2024: Вложено 12,400 │ Выручка 15,800 │ Прибыль +3,400 (27%)"

@router.message(Command("stats"))
async def cmd_stats(message: Message, args: str):
    """Статистика по запросу"""
    # args = "iphone 15"
    # GET /api/v1/price-stats?query=iphone+15
    # Ответ: "📱 iPhone 15: Медиана 2,500 BYN │ На рынке 200 │ Дешевле 1,800"
```

#### Улучшенные уведомления:
```
🔔 НОВЫЙ ЛОТ: iPhone 15 128GB
💰 1,800 BYN (медиана: 2,500)
📉 -28% от медианы
⚡ Высокая ликвидность

[📌 В покупки] [👁 Отслеживать] [🔗 Открыть]
```

---

## 🟢 ПРИОРИТЕТ 3 (P2) — НИЗКИЙ

### 3.1 Экспорт CSV

**Проблема:** Перекуп не может выгрузить данные для внешнего анализа или бухгалтерии.

**Решение:** Добавить endpoints для экспорта.

#### Бэкенд

**Новые endpoints:**
```
GET /api/v1/leads/export?format=csv
GET /api/v1/watchlist/export?format=csv
```

**CSV формат для leads:**
```csv
id,title,category,buy_price,expenses,sell_price,profit,roi,status,created_at,sold_at
1,iPhone 15 128GB,Телефоны,1200,80,1800,520,40.6,sold,2024-01-15,2024-01-22
```

#### Фронтенд

**Кнопка "📥 Экспорт" в разделах "Покупки" и "Избранное":**
- При клике — скачать файл `leads_2024-01.csv`

---

### 3.2 Напоминания для сделок

**Проблема:** Перекуп забывает про сделки, которые требуют внимания через несколько дней.

**Решение:** Добавить систему напоминаний.

#### Бэкенд

**Новая модель:**
```python
class LeadReminder(Base):
    __tablename__ = "lead_reminders"
    
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("lead_items.id"))
    user_id = Column(BigInteger, nullable=False)
    remind_at = Column(DateTime, nullable=False)
    message = Column(String(255), nullable=True)
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
```

**Scheduler job (каждые 15 мин):**
```python
async def check_reminders():
    due = SELECT * FROM lead_reminders WHERE remind_at <= NOW() AND sent = FALSE
    for reminder in due:
        await bot.send_message(reminder.user_id, f"⏰ {reminder.message}")
        reminder.sent = TRUE
```

#### Фронтенд

**В модалке сделки:**
- Секция "🔔 Напоминание"
- Date/time picker + быстрые чипы: "Завтра", "Через 3 дня", "Через неделю"

**На карточке сделки:**
- Иконка 📅 если есть напоминание

**Вверху раздела "Покупки":**
- Мини-лента "Ближайшие напоминания"

---

### 3.3 Извлечение телефонов и контакты

**Проблема:** Перекуп хочет сохранять контакты проверенных продавцов для будущих сделок.

**Решение:** Добавить извлечение телефонов и управление контактами.

#### Бэкенд

**Сервис извлечения:**
```python
# api/services/phone_extractor.py
import re

PHONE_PATTERN = re.compile(r'(\+?\d{2,3}[\s\-]?\(?\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})')

def extract_phone(description: str) -> str | None:
    match = PHONE_PATTERN.search(description)
    return match.group(1) if match else None
```

**Новая модель:**
```python
class Contact(Base):
    __tablename__ = "contacts"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    phone = Column(String(32), nullable=False)
    seller_name = Column(String(128), nullable=True)
    kufar_profile = Column(String(512), nullable=True)
    saved_at = Column(DateTime, server_default=func.now())
    notes = Column(String(255), nullable=True)
    
    __table_args__ = (UniqueConstraint("user_id", "phone"),)
```

**API:**
```
GET    /api/v1/contacts                    # Список контактов
POST   /api/v1/contacts                    # Сохранить контакт
GET    /api/v1/listing-detail/{id}/phone   # Извлечь телефон
```

#### Фронтенд

**В модалке объявления:**
- Если найден телефон, показать "📱 +375 XX XXX-XX-XX" + кнопка "Сохранить"

**В разделе "Избранное":**
- Подраздел "Контакты" со списком сохранённых продавцов

**При просмотре объявления:**
- Если продавец уже в контактах, показать "✅ Знакомый продавец"

---

## 📊 ПОЛНАЯ СХЕМА БАЗЫ ДАННЫХ

### Новые таблицы:
```sql
-- Расходы на сделку
CREATE TABLE deal_expenses (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER REFERENCES lead_items(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    expense_type VARCHAR(32) NOT NULL,  -- delivery, repair, commission, other
    amount_byn NUMERIC(10, 2) NOT NULL,
    notes VARCHAR(255),
    expense_date TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_expenses_lead ON deal_expenses(lead_id);

-- Напоминания
CREATE TABLE lead_reminders (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER REFERENCES lead_items(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    remind_at TIMESTAMP NOT NULL,
    message VARCHAR(255),
    sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_reminders_due ON lead_reminders(remind_at, sent);

-- Контакты продавцов
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY,
    user_id BIGINT NOT NULL,
    phone VARCHAR(32) NOT NULL,
    seller_name VARCHAR(128),
    kufar_profile VARCHAR(512),
    saved_at TIMESTAMP DEFAULT NOW(),
    notes VARCHAR(255),
    UNIQUE(user_id, phone)
);
```

### Расширения существующих таблиц:
```sql
-- LeadItem: полный P/L трекинг
ALTER TABLE lead_items ADD COLUMN sold_price_byn NUMERIC(10, 2);
ALTER TABLE lead_items ADD COLUMN sold_at TIMESTAMP;

-- Tracker: алерты по порогу
ALTER TABLE trackers ADD COLUMN alert_price_threshold NUMERIC(10, 2);
ALTER TABLE trackers ADD COLUMN alert_discount_percent FLOAT;
```

---

## 📋 ПОШАГОВЫЙ ПЛАН РЕАЛИЗАЦИИ

### Этап 1: Бэкенд — Модели и API (3-4 дня)
1. Создать модель `DealExpense` + CRUD endpoints
2. Расширить `LeadItem` (sold_price, sold_at)
3. Создать модель `LeadReminder` + CRUD + scheduler job
4. Создать модель `Contact` + CRUD + phone extractor
5. Расширить `Tracker` (alert thresholds)
6. Добавить `/api/v1/deals/analytics` endpoint
7. Добавить `/api/v1/market-velocity` endpoint
8. Добавить export endpoints (CSV)
9. Добавить risk detector service

### Этап 2: Scheduler (1 день)
10. Добавить job проверки напоминаний
11. Расширить проверку трекеров (threshold alerts)
12. Новый тип события `price_threshold_alert`

### Этап 3: Фронтенд — Покупки (3-4 дня)
13. Визуальный степпер пайплайна
14. Модалка расходов (список + добавление)
15. Поле "Цена продажи" + расчёт прибыли
16. Сводная карточка P/L вверху
17. Секция аналитики (метрики + график)
18. Напоминания (date picker + лента)
19. Кнопка экспорта CSV

### Этап 4: Фронтенд — Остальные вкладки (2-3 дня)
20. Бейджи рисков на карточках объявлений
21. Панель "Скорость рынка" в Обзоре
22. Поля алертов в форме трекера
23. Извлечение телефонов в модалке
24. Подраздел "Контакты" в Избранном

### Этап 5: Telegram бот (1 день)
25. Команды /deals, /profit, /stats
26. Улучшенное форматирование уведомлений
27. Inline кнопки в сообщениях

### Этап 6: Тестирование (2 дня)
28. Unit-тесты для новых сервисов
29. Интеграционные тесты API
30. Ручное тестирование Mini App
31. Исправление багов

**Итого: ~12-15 рабочих дней**

---

## ✅ КРИТЕРИИ ПРИЁМКИ

### P0 (Обязательно):
- [ ] Расходы можно добавлять/редактировать/удалять
- [ ] Прибыль считается автоматически (продажа - покупка - расходы)
- [ ] Пайплайн показывает расширенные стадии
- [ ] Алерты по порогу цены работают в трекерах
- [ ] Аналитика показывает win rate и ROI

### P1 (Желательно):
- [ ] Бейджи рисков отображаются на карточках
- [ ] Скорость рынка показывает рейтинг 🟢🔴
- [ ] Напоминания отправляются через бота
- [ ] Телефоны извлекаются из описания
- [ ] Команды бота /deals, /profit, /stats работают

### P2 (Бонус):
- [ ] CSV экспорт скачивает файл
- [ ] Контакты сохраняются и отображаются
- [ ] Inline кнопки в уведомлениях бота

---

## 🔧 ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ

### Бэкенд:
- Python 3.12+, FastAPI, SQLAlchemy async
- PostgreSQL 16 (использовать `Numeric` для денег)
- Alembic для миграций
- Ruff для линтинга
- pytest для тестов

### Фронтенд:
- Vanilla JS (без фреймворков)
- Chart.js для графиков
- Telegram Web App SDK
- CSS с CSS-переменными для темы

### Стиль:
- Сохранить существующий дизайн (тёмная тема, оранжевый акцент)
- Компактные карточки, без пустого пространства
- Иконки-эмодзи для быстрой навигации
- Мобильная адаптация (min touch target 44px)

---

**Версия документа:** 1.0  
**Дата создания:** Апрель 2025  
**Статус:** ✅ Готово к реализации
