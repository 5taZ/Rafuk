# Атомарный план исправлений после deep-dive аудита

Дата: 2026-05-14

Этот файл фиксирует найденные проблемы и разбивает их на небольшие
независимые волны. Каждая волна должна закрываться отдельным commit,
содержать только связанные изменения и проходить релевантную проверку.

## Текущий caveat рабочей копии

До начала этих волн в рабочем дереве уже были сторонние удаления:

- `.codex`
- `.qwenrules`
- `CHANGELOG.md`
- `audit.md`
- `claude.md`
- `gpt.md`
- `mybad.md`
- `problems.md`

Эти удаления не относятся к плану ниже и не должны попадать в commits
исправлений.

## Wave 95 — сохранить результаты аудита

**Scope:** только этот файл.

**Цель:** зафиксировать findings и порядок атомарных волн, чтобы дальше
двигаться без смешивания unrelated правок.

**Verification:** `git diff -- konechno.md`.

## Wave 96 — bot → API auth/CSRF consistency

**Проблема:** `csrf_origin_check` требует `Origin` и
`X-Requested-With: XMLHttpRequest` для всех mutating requests. Bot callback
POST-запросы отправляют только internal-service headers или forged initData,
поэтому могут получать 403 до входа в route dependencies.

**Atomic fix:**

1. Вынести построение bot→API auth headers в общий `bot/api_client.py`.
2. Предпочитать `INTERNAL_SERVICE_TOKEN` для всех bot→API calls.
3. Для mutating bot calls добавлять CSRF-compatible headers.
4. Перевести callback/analytics handlers на общий helper.
5. Добавить unit/integration tests на headers и реальный POST через app
   без `_CSRFTestClient` shim.

**Verification:** релевантные bot/API tests + `ruff check` по изменённым
файлам.

## Wave 97 — AI cache/quota/audit order

**Проблема:** `/ai/negotiate` и `/ai/price-advice` делают rate-limit и
audit до cache lookup. В результате cache hits списывают quota и выглядят
как misses в audit trail. `/ai/analyze` и `/ai/listing-assistant` уже идут
по правильному порядку.

**Atomic fix:**

1. Для `/ai/negotiate` перенести cache lookup до `_check_rate_limit`.
2. Для `/ai/price-advice` сделать то же.
3. На cached hit писать audit с `cached=True`.
4. На miss оставлять rate-limit + обычный audit.
5. Добавить tests, которые доказывают, что cache hit не increment-ит quota.

**Verification:** AI tools tests.

## Wave 98 — production Redis limiter degradation policy

**Проблема:** SlowAPI limiter создаётся на import time и при недоступном
Redis падает в `memory://`. В multi-worker deployment лимиты становятся
per-process и фактически умножаются на число workers.

**Atomic fix:**

1. В production-like deployment не разрешать silent memory fallback.
2. Для local/dev сохранить graceful fallback.
3. Сделать поведение явно тестируемым.
4. Проверить, что logs/health/metrics не вводят ops в заблуждение.

**Verification:** limiter/config tests.

## Wave 99 — reminder datetime validation

**Проблема:** Reminder API принимает любой `remind_at`, включая прошлое
и потенциально бессмысленно далёкое будущее.

**Atomic fix:**

1. Валидировать `remind_at` в `ReminderCreate`.
2. Требовать timezone-aware datetime.
3. Требовать future time с небольшим skew allowance или строго `> now`.
4. Добавить разумный upper bound.
5. Добавить API/schema tests.

**Verification:** reminder tests.

## Wave 100 — query_snapshots cleanup index

**Проблема:** nightly cleanup удаляет `query_snapshots` по `snapshot_at`,
а актуальная модель имеет index на `query` и unique `(query, snapshot_at)`.
Predicate только по `snapshot_at` плохо использует composite index со
второй колонкой.

**Atomic fix:**

1. Добавить Alembic migration для index на `query_snapshots(snapshot_at)`.
2. Синхронизировать SQLAlchemy model metadata.
3. Проверить Alembic head/current или targeted migration tests.

**Verification:** Alembic checks / model metadata tests.

## Wave 101 — docs/config drift

**Проблемы:**

1. `.env.example` не совпадает с repo policy по local Redis `:6380`.
2. Nginx comments говорят про SlowAPI decorators на AI routes, хотя там
   используется custom `_check_rate_limit`.
3. Service worker cache version bump отделён от static `?v=` bump и легко
   забывается при frontend release.

**Atomic fix:**

1. Привести `.env.example` к documented local Redis policy.
2. Поправить nginx comments без изменения runtime behaviour.
3. Либо расширить `scripts/bump_static_version.sh` для SW version, либо
   добавить явный release-check guard.

**Verification:** docs/scripts tests или targeted shell checks.

## Deferred / не в первой серии волн

- Большой frontend maintainability split: `dom_helpers.js`,
  `api_listing_assistant.js`, `api_events.js`, крупные CSS parts.
- Полная централизация frontend constants через `window.APP_CONFIG`.
- Variable-height virtualization вместо fixed-height list math.
- Production decisions по off-host backups, monitoring provider, CD target,
  secret store / Docker secrets.
