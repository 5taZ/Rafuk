# Профили, ручные премиум-статусы и админка для Rafuk

## Цель

Сделать внутри Telegram Mini App личный профиль пользователя, ручную выдачу премиум-статусов через админку и простую систему лимитов для AI-функций.

Первая версия не должна превращаться в биллинг, подписки и CRM. Нужен управляемый фундамент:

1. любой пользователь видит свой профиль, статус и остатки лимитов;
2. дефолтный пользователь может пользоваться обычным поиском и не трогает AI;
3. админ вручную выдаёт статус пользователю;
4. админ меняет у статусов только два числа:
   - дневной лимит обычных AI-запросов;
   - дневной лимит запросов к AI-помощнику продавца;
5. backend, а не frontend, решает, кто админ и кому можно пользоваться AI.

## Что уже есть в проекте

- Пользователь уже существует как `users` в `api/models.py`.
- Авторизация уже идёт через Telegram initData в `api/dependencies.py`.
- Первый запрос пользователя уже авто-создаёт `User`.
- AI endpoints уже централизованно проходят через `_check_rate_limit` в `api/services/ai_guards.py`.
- AI вызовы уже пишутся в `ai_audit_log`.
- Frontend — vanilla JS Mini App: `frontend/index.html`, `frontend/js/app_core.js`, `frontend/js/app_actions.js`, `frontend/js/app_renderers.js`.
- Навигация сейчас держится на `data-view`, `state.ui.activeView` и `elements.views`.

Это значит, что профили и админку можно добавить без смены архитектуры.

## Названия статусов

Не стоит публично называть дефолтного пользователя “бомжом”: внутри это смешно, но в продукте может выглядеть дешево и бить по конверсии. Лучше оставить дерзкий тон, но без прямого унижения.

Предлагаемая линейка:

| Код | Публичное название | Вайб | AI / день | AI-помощник / день |
|---|---|---:|---:|---:|
| `bare_search` | **Голый Поиск** | базовый режим: ищешь руками, без магии | 0 | 0 |
| `scout` | **Скаут Барахолки** | попробовать AI, понять ценность | 10 | 3 |
| `flipper` | **Флиппер** | регулярный поиск выгодных лотов | 40 | 12 |
| `shark` | **Куфарная Акула** | активный ресейл / постоянные сделки | 120 | 35 |
| `market_maker` | **Имба Маркетмейкер** | почти “god mode”, высокий лимит | 300 | 100 |

Альтернативные названия, если хочется ещё более мемно:

- `bare_search`: **Режим Тапки**, **Голый Парсер**, **Без Бустов**
- `scout`: **Полевой Скаут**, **Сигнальщик**
- `flipper`: **Перекуп Junior**, **Флиппер+**
- `shark`: **Акула Сделок**, **Охотник за Маржой**
- `market_maker`: **Куфарный Босс**, **Rafuk Ultra**, **Имба Ресейлер**

Рекомендую первую таблицу: звучит живо, но не превращает продукт в шутку.

## Простая модель статусов

Нужны две сущности:

### 1. `account_statuses`

Справочник статусов. Коды фиксированные, названия фиксированные, из админки меняются только лимиты.

Поля:

- `code` — primary key: `bare_search`, `scout`, `flipper`, `shark`, `market_maker`;
- `display_name`;
- `tagline`;
- `accent` — цвет/тон бейджа: `gray`, `blue`, `violet`, `amber`, `red`;
- `sort_order`;
- `ai_daily_limit`;
- `assistant_daily_limit`;
- `updated_at`.

Админский PATCH разрешает менять только:

- `ai_daily_limit`;
- `assistant_daily_limit`.

Так у нас есть “простая настройка статусов”, но нет хаоса с переименованием тарифов из UI.

### 2. Новые поля в `users`

Добавить в `User`:

- `account_status_code` — FK на `account_statuses.code`, default `bare_search`;
- `status_granted_at`;
- `status_expires_at` — nullable, на будущее для временных промо;
- `status_note` — nullable, внутренняя заметка админа.

Если `status_expires_at` уже в прошлом, effective status считается `bare_search`, даже если в `users.account_status_code` ещё лежит старый премиум-код. Так промо не превращаются в вечный доступ из-за забытых cleanup jobs.

Админов лучше не хранить как обычный статус. Админство должно быть отдельным backend-правом.

## Как определять админа

Добавить в settings:

```env
ADMIN_TELEGRAM_USER_IDS=123456789,987654321
```

На backend:

- `is_admin_user(telegram_user_id, settings)` проверяет ID по allowlist;
- `require_admin_user` — FastAPI dependency;
- frontend может скрывать кнопку админки, но это только UX;
- все `/admin/*` endpoints обязаны проверять `require_admin_user`.

Почему не делать `is_admin` в базе в первой версии:

- если случайно выдать себе/другому флаг через админку, можно потерять контроль;
- allowlist в env проще и безопаснее для первого релиза;
- позже можно добавить DB-роли, если появится команда.

## Лимиты и buckets

Сейчас AI guard использует глобальные `ai_hourly_limit` / `ai_daily_limit`. Для статусов нужно перейти на лимиты пользователя.

Делаем два quota bucket:

### `ai`

Обычные AI-запросы:

- `/api/v1/ai/analyze`;
- `/api/v1/ai/negotiate`;
- `/api/v1/ai/price-advice`.

### `assistant`

AI-помощник продавца:

- `/api/v1/ai/listing-assistant`.

Ключи Redis:

```text
quota:ai:{telegram_user_id}:{YYYY-MM-DD}
quota:assistant:{telegram_user_id}:{YYYY-MM-DD}
```

Дата — по `Europe/Minsk`, чтобы пользователю было понятно: “лимиты обновятся в 00:00”.

TTL:

- до следующей полуночи + небольшой запас, например 48 часов;
- ключ включает дату, поэтому старые счетчики не смешиваются с новыми.

Поведение:

- если лимит равен `0`, endpoint возвращает `403 premium_required`;
- если лимит исчерпан, endpoint возвращает `429 quota_exceeded`;
- cached AI response не списывает quota.

Пользователь не должен тратить лимит на то, что уже было посчитано и почти ничего не стоит. В текущем коде часть AI endpoints уже проверяет cache до `_check_rate_limit`, это хорошо сохранить.

Для UI `used` нужно показывать как `min(raw_counter, limit)`, а `remaining` как `max(0, limit - raw_counter)`. Если пользователь пять раз жмёт кнопку после исчерпания лимита, внутренний counter может уйти выше лимита из-за atomic `INCR`, но профиль всё равно должен показывать понятные `10 / 10`, а не `15 / 10`.

Ответ ошибки должен быть пригоден для UI:

```json
{
  "error": "quota_exceeded",
  "bucket": "ai",
  "used": 10,
  "limit": 10,
  "resets_at": "2026-05-19T00:00:00+03:00",
  "message": "Лимит AI-запросов на сегодня закончился"
}
```

## Профиль пользователя

Добавить endpoint:

```http
GET /api/v1/profile/me
```

Ответ:

```json
{
  "user": {
    "telegram_user_id": 123456789,
    "first_name": "Staz",
    "username": "staz",
    "created_at": "...",
    "last_seen_at": "..."
  },
  "status": {
    "code": "flipper",
    "display_name": "Флиппер",
    "tagline": "Регулярный поиск выгодных лотов",
    "accent": "violet",
    "granted_at": "...",
    "expires_at": null
  },
  "limits": {
    "ai": {
      "used": 12,
      "limit": 40,
      "remaining": 28,
      "resets_at": "2026-05-19T00:00:00+03:00"
    },
    "assistant": {
      "used": 2,
      "limit": 12,
      "remaining": 10,
      "resets_at": "2026-05-19T00:00:00+03:00"
    }
  },
  "permissions": {
    "can_use_ai": true,
    "can_use_assistant": true,
    "is_admin": false
  }
}
```

Профиль должен подгружаться при старте Mini App и после изменения статуса админом.

`/profile/me` также должен best-effort обновлять `users.first_name` / `users.username` из Telegram initData, чтобы админка показывала живые имена, а не только ID. Если username недоступен в текущем `TelegramInitData`, можно распарсить поле `user` из `TelegramInitData.raw`.

## Как красиво показать профиль

Не нужно добавлять огромную пятую вкладку в основную навигацию, потому что сейчас в Mini App уже есть четыре рабочие зоны. Лучше сделать профиль через компактный header-chip.

### Header

В правой части header добавить кнопку:

```text
[Флиппер · 28 AI]
```

Для `bare_search`:

```text
[Голый Поиск]
```

Если пользователь админ:

```text
[Имба Маркетмейкер · Admin]
```

Клик открывает отдельный `profile` view. Bottom-sheet можно добавить позже, но для первой реализации view проще тестировать и переиспользовать под админку.

### Profile view

Секция:

1. Hero-карта статуса:
   - название статуса;
   - короткий tagline;
   - “выдано вручную” / “до даты”;
   - красивый gradient/accent.
2. Две карточки лимитов:
   - “AI-анализ”;
   - “AI-помощник продавца”;
   - progress bar: `used / limit`;
   - “обновится в 00:00”.
3. “Что доступно”:
   - обычный поиск;
   - объявления;
   - автопоиск;
   - мои объявления;
   - AI-функции, если статус позволяет.
4. Если статус `bare_search`, показать мягкий locked-block:
   - “AI закрыт”;
   - “Попроси доступ у админа / напиши владельцу”.

### Визуальная система

Цвета можно привязать к существующим CSS tokens:

- `bare_search` → gray / `--bg-elevated`;
- `scout` → blue / `--accent`;
- `flipper` → violet / `--violet`;
- `shark` → amber / `--amber`;
- `market_maker` → red+violet gradient.

Не надо делать casino UI. Статус должен выглядеть как дорогой бейдж, а не как баннер из мобильной игры.

## Админка

Админка живёт внутри Mini App, но видна только если `profile.permissions.is_admin === true`.

Лучший UX:

- в профиле появляется кнопка **Админка**;
- кнопка открывает `admin` view;
- обычным пользователям такой view не показывается;
- backend всё равно защищает endpoints.

### Раздел 1: Пользователи

Функции:

- поиск по Telegram ID;
- поиск по username / first_name;
- фильтр по статусу;
- список пользователей карточками.

Карточка пользователя:

```text
@username / First Name
TG: 123456789
Статус: Флиппер
AI сегодня: 12 / 40
Помощник сегодня: 2 / 12
Создан: ...
Был: ...
[Изменить статус]
```

Изменение статуса:

- select статуса;
- optional `expires_at`;
- optional admin note;
- кнопка “Сохранить”.

### Раздел 2: Статусы

Таблица статусов:

```text
Голый Поиск         AI: 0     Помощник: 0
Скаут Барахолки    AI: 10    Помощник: 3
Флиппер            AI: 40    Помощник: 12
Куфарная Акула     AI: 120   Помощник: 35
Имба Маркетмейкер  AI: 300   Помощник: 100
```

Редактируемые поля:

- `AI / день`;
- `Помощник / день`.

Не редактируем из UI:

- код;
- название;
- цвет;
- порядок;
- описание.

Так настройка остаётся простой и безопасной.

### Раздел 3: Аудит

Минимально нужен audit trail для админских действий:

- кто изменил;
- кому изменил;
- старый статус;
- новый статус;
- старые лимиты / новые лимиты, если менял настройки статуса;
- IP;
- timestamp.

Таблица:

```text
admin_audit_log
```

Поля:

- `id`;
- `actor_user_id`;
- `target_user_id` nullable;
- `action`;
- `payload` JSON;
- `ip_address`;
- `created_at`.

Это важно, даже если админ пока один. Через месяц будет непонятно, почему кто-то получил “Имбу”.

## Backend endpoints

### Profile

```http
GET /api/v1/profile/me
```

Возвращает профиль, статус, лимиты, permissions.

### Admin users

```http
GET /api/v1/admin/users?query=&status=&limit=50&offset=0
```

```http
PATCH /api/v1/admin/users/{telegram_user_id}/status
```

Body:

```json
{
  "status_code": "shark",
  "expires_at": null,
  "note": "Выдал вручную перед запуском"
}
```

### Admin status config

```http
GET /api/v1/admin/statuses
```

```http
PATCH /api/v1/admin/statuses/{code}
```

Body:

```json
{
  "ai_daily_limit": 120,
  "assistant_daily_limit": 35
}
```

Validation:

- лимиты integer;
- `0 <= limit <= 10000`;
- нельзя удалить `bare_search`;
- нельзя сделать неизвестный status code;
- если лимит уменьшили ниже уже потраченного сегодня, пользователь просто ждёт reset.

## Backend services

Добавить сервис:

```text
api/services/account_status.py
```

Ответственность:

- загрузить статус пользователя;
- применить default `bare_search`, если что-то сломалось в данных;
- посчитать текущие quota keys;
- проверить лимит;
- вернуть `remaining`;
- дать admin helper для изменения статуса;
- дать helper для изменения лимитов статуса.

AI guard меняется так:

```python
await _check_rate_limit(request, _user.user_id, endpoint="analyze", quota_bucket="ai")
await _check_rate_limit(request, _user.user_id, endpoint="listing_assistant", quota_bucket="assistant")
```

Для `/ai/analyze`, `/ai/negotiate`, `/ai/price-advice` bucket = `ai`.

Для `/ai/listing-assistant` bucket = `assistant`.

Существующий `@limiter.limit("10/minute")` оставить как burst-защиту. Статусные лимиты отвечают за дневную продуктовую квоту, SlowAPI — за защиту от спама.

## Frontend изменения

### Новое состояние

В `state`:

```js
profile: {
  data: null,
  loading: false,
  error: "",
},
admin: {
  users: [],
  statuses: [],
  query: "",
  selectedStatus: "",
  loading: false,
  error: "",
}
```

### DOM

В `frontend/index.html`:

- `profile-chip` в header;
- `profile-view`;
- `admin-view`;
- templates/containers для лимитов и статусов.

В `frontend/js/app_core_dom.js`:

- добавить `profile` / `admin` в `elements.views`;
- закешировать элементы profile/admin.

### API

Добавить:

```text
frontend/js/api_profile.js
frontend/js/api_admin.js
```

Функции:

- `loadProfile()`;
- `loadAdminUsers()`;
- `updateUserStatus()`;
- `loadAdminStatuses()`;
- `updateStatusLimits()`.

### Render

Добавить:

```text
frontend/js/render_profile.js
frontend/js/render_admin.js
```

Новые dirty flags:

- `profile`;
- `adminUsers`;
- `adminStatuses`.

Не забыть после изменения frontend/js или frontend/css:

```bash
scripts/bump_static_version.sh
```

## Как AI-кнопки должны вести себя для дефолтного пользователя

Если статус `bare_search`:

- AI-кнопки можно показывать disabled с замком;
- при клике показывать profile/paywall sheet:
  - “AI доступен со статуса Скаут Барахолки”;
  - “Сейчас у тебя Голый Поиск”;
  - “Напиши владельцу, чтобы получить доступ”.

Лучше disabled/locked, чем полностью скрывать: пользователь должен понимать, что фича существует.

Если лимит закончился:

- кнопки не исчезают;
- показывают “Лимит на сегодня закончился”;
- рядом “обновится в 00:00”.

## Миграция

Одна Alembic migration:

1. создать `account_statuses`;
2. засеять 5 статусов;
3. добавить поля в `users`;
4. создать `admin_audit_log`;
5. проставить всем текущим users `bare_search`.

Важно: миграция должна работать и на Postgres, и на SQLite tests.

## Схемы Pydantic

Добавить в `api/schemas.py`:

- `AccountStatusRead`;
- `QuotaBucketRead`;
- `ProfileRead`;
- `AdminUserRead`;
- `AdminUserStatusUpdate`;
- `AdminStatusLimitUpdate`;
- `AdminAuditRead` если сразу показываем аудит.

## Тесты

Минимальный набор:

1. `GET /profile/me` для нового пользователя возвращает `bare_search`.
2. `bare_search` получает `403 premium_required` на cold AI request.
3. `scout` с лимитом `1` может сделать первый AI request и получает `429` на второй.
4. `listing-assistant` тратит `assistant`, а не `ai`.
5. cached AI response не тратит quota.
6. обычный пользователь получает `403` на `/admin/users`.
7. admin из `ADMIN_TELEGRAM_USER_IDS` может менять статус пользователя.
8. admin может менять только лимиты статуса.
9. изменение статуса пишет `admin_audit_log`.
10. уменьшение лимита ниже used не ломает профиль: `remaining = 0`.

## Реализация волнами

### Wave A — backend data model

- migration;
- SQLAlchemy models;
- seed статусов;
- schemas.

### Wave B — profile + quota service

- `account_status.py`;
- `/profile/me`;
- quota keys;
- tests на профиль и quota math.

### Wave C — AI guard integration

- заменить текущий global daily limit на status daily limits;
- подключить bucket `ai`;
- подключить bucket `assistant`;
- сохранить SlowAPI minute caps;
- tests на AI endpoints.

### Wave D — admin backend

- `require_admin_user`;
- `/admin/users`;
- `/admin/users/{telegram_user_id}/status`;
- `/admin/statuses`;
- `admin_audit_log`;
- tests.

### Wave E — frontend profile/admin UI

- header profile chip;
- profile view;
- locked AI state;
- admin users/statuses views;
- CSS in `frontend/css/parts/`;
- static version bump.

## Главные решения

1. Статус пользователя — это не роль и не админство.
2. Админство — только через env allowlist в первой версии.
3. Статусы редактируются минимально: только два лимита.
4. Обычный поиск остаётся доступен всем.
5. AI считается премиум-фичей.
6. Cached AI responses лучше не списывать из лимита.
7. Профиль должен быть не отдельным “кабинетом из SaaS”, а красивой компактной карточкой внутри Mini App.
8. Админка должна быть функциональной, но не публичной частью UX.

## Итоговый продуктовый образ

Пользователь открывает Rafuk и видит вверху свой статус: **Голый Поиск**, **Флиппер**, **Куфарная Акула** и т.д. В профиле видно, сколько AI-запросов осталось сегодня и когда они обновятся. Если AI закрыт, интерфейс честно показывает locked-состояние и мотивирует попросить доступ.

Ты как владелец заходишь в тот же Mini App, открываешь админку, находишь пользователя по Telegram ID или username, выдаёшь ему нужный статус и при необходимости подкручиваешь лимиты статусов. Без платежей, без Stripe, без лишней бюрократии — но уже с нормальной архитектурой, аудитом и местом для будущей монетизации.
