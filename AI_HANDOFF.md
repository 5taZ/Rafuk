# AI Handoff: Kufar Analytics

## 1. Контекст

Этот файл нужен как handoff-документ для другого ИИ/разработчика. Он описывает:

- что просил пользователь;
- какие решения были приняты по ходу работы;
- текущую архитектуру проекта;
- что уже реализовано;
- что сознательно отложено;
- как запускать проект локально;
- какие есть важные нюансы.

Важно:

- пользователь работает в Беларуси;
- проект ориентирован на аналитику рынка `kufar.by`;
- Mini App живёт внутри Telegram;
- локальная разработка идёт через `cloudflared` и временный `trycloudflare.com` URL;
- токен бота раньше отправлялся в чат, его лучше перевыпустить через `@BotFather`.

---

## 2. Краткая история диалога

### Исходный запрос

Пользователь попросил:

- взять план/инструкцию для парсера Kufar;
- построить полноценный проект с хорошей архитектурой и иерархией;
- сделать рабочий backend, бота, scheduler и Mini App.

### Что обсуждалось и менялось дальше

1. Был собран полноценный проект:
   - FastAPI API;
   - aiogram bot;
   - scheduler;
   - frontend Mini App;
   - Docker и миграции;
   - тесты.

2. Пользователь спрашивал про зависание `uv sync`.
   - Выяснилось, что сеть и VPN не были постоянной проблемой.
   - `uv sync` позже успешно отработал.

3. Пользователь попросил добить проект до зелёного состояния.
   - Были исправлены API, сериализация и lint/test issues.
   - В результате тесты и `ruff` стали зелёными.

4. Пользователь попросил поднять бота.
   - Был настроен `.env`.
   - Были подняты API и бот.
   - Для Redis был добавлен fallback на in-memory cache.

5. Выяснилось, что бот и Mini App работают некорректно:
   - `/start` и `/app` ломались на локальном `http`;
   - `/price` и `/top` ломались на реальных данных Kufar.
   - Это было исправлено.

6. Пользователь указал, что статистика считается неверно.
   - Выяснилось, что раньше расчёт был по первой странице / первой пачке объявлений.
   - Затем логика была переведена на полный рынок через пагинацию Kufar API.

7. Пользователь показал внешние frontend-файлы от другого разработчика.
   - Они были частично оценены и внедрены выборочно.
   - Часть UI/UX решений была перенесена, а бизнес-логика была интегрирована только там, где валидна.

8. Пользователь попросил:
   - убрать `EUR`;
   - оставить `BYN` в приоритете;
   - дать возможность переключаться на `USD`;
   - показывать курс USD/BYN от НБРБ.
   - Это было реализовано.

9. Пользователь несколько раз сталкивался с проблемами локального запуска:
   - занятые порты `5432`, `5433`, `8080`;
   - dead Cloudflare Tunnel;
   - старый `MINI_APP_URL` в старых Telegram-сообщениях;
   - `502`, если frontend в Docker не мог достучаться до локального API.
   - Для этого был сформирован и автоматизирован локальный запуск.

10. Пользователь попросил сделать удобные локальные скрипты.
    - Были добавлены:
      - `start-local.sh`
      - `stop-local.sh`
      - `status-local.sh`

11. Пользователь попросил переработать компактность Mini App.
    - Была убрана длинная лента карточек.
    - Интерфейс был перестроен на компактную навигацию.

12. Пользователь попросил оценить функции уровней 2-4 и предложить roadmap.
    - Было принято решение:
      - делать дешёвые объявления;
      - делать price-drop alerts;
      - копить историю;
      - позже добавить динамику цен, сравнение и регионы;
      - не лезть пока в ML и тяжёлые функции.

13. После команды пользователя `Приступай к работе` были реализованы:
    - режим `Дёшево`;
    - хранение `query_snapshots`;
    - хранение `query_listing_states`;
    - price-drop foundation;
    - группировка tracker-проверок по уникальному запросу.

14. После следующей команды `Сделай` были реализованы:
    - отдельный раздел `Дёшево` в Mini App;
    - endpoint истории цен;
    - первый график динамики медианы;
    - улучшенные уведомления по трекерам;
    - накопление истории не только из scheduler, но и из обычного поиска.

15. Затем локальный старт упал на Alembic:
    - SQLite-база уже содержала таблицы, созданные через `create_all`;
    - Alembic пытался создать их повторно.
    - Это было исправлено идемпотентными миграциями.

---

## 3. Текущая цель продукта

Проект решает первую практическую задачу:

- пользователь вводит товар;
- получает аналитику полного рынка Kufar;
- видит медиану, среднюю цену, диапазон, сегменты;
- может просматривать свежие объявления и дешёвые предложения;
- может управлять трекерами новых объявлений;
- позже будет получать ещё и сигналы по изменению цены и историю рынка.

---

## 4. Архитектура приложения

### Общая схема

Проект состоит из 4 основных частей:

1. `FastAPI backend`
2. `Telegram bot` на `aiogram`
3. `scheduler` для фоновых проверок
4. `Mini App frontend`

### Backend

Основные роли backend:

- ходить в Kufar API;
- считать статистику по рынку;
- сегментировать рынок;
- хранить трекеры и исторические снапшоты;
- отдавать данные Mini App и боту.

Ключевые файлы:

- [api/main.py](/home/staz/Downloads/myProjetctKufar/api/main.py)
- [api/config.py](/home/staz/Downloads/myProjetctKufar/api/config.py)
- [api/dependencies.py](/home/staz/Downloads/myProjetctKufar/api/dependencies.py)
- [api/models.py](/home/staz/Downloads/myProjetctKufar/api/models.py)
- [api/schemas.py](/home/staz/Downloads/myProjetctKufar/api/schemas.py)

### API routers

Роуты:

- [price_stats.py](/home/staz/Downloads/myProjetctKufar/api/routers/price_stats.py)
- [price_history.py](/home/staz/Downloads/myProjetctKufar/api/routers/price_history.py)
- [listings.py](/home/staz/Downloads/myProjetctKufar/api/routers/listings.py)
- [segments.py](/home/staz/Downloads/myProjetctKufar/api/routers/segments.py)
- [listing_detail.py](/home/staz/Downloads/myProjetctKufar/api/routers/listing_detail.py)
- [currency.py](/home/staz/Downloads/myProjetctKufar/api/routers/currency.py)
- [trackers.py](/home/staz/Downloads/myProjetctKufar/api/routers/trackers.py)

### Сервисы backend

Ключевые сервисы:

- [kufar_client.py](/home/staz/Downloads/myProjetctKufar/api/services/kufar_client.py)
  - полный поиск по рынку через пагинацию;
  - rate limit / retry / timeout.

- [aggregator.py](/home/staz/Downloads/myProjetctKufar/api/services/aggregator.py)
  - нормализация цен;
  - вычисление статистики;
  - фильтр `Дёшево`;
  - сортировки.

- [history_service.py](/home/staz/Downloads/myProjetctKufar/api/services/history_service.py)
  - bucket-снапшоты;
  - upsert `query_snapshots`;
  - sync `query_listing_states`;
  - чтение истории.

- [currency_service.py](/home/staz/Downloads/myProjetctKufar/api/services/currency_service.py)
  - курс НБРБ;
  - конвертация `BYN -> USD`.

- [cache.py](/home/staz/Downloads/myProjetctKufar/api/services/cache.py)
  - Redis cache;
  - fallback на `MemoryCache`.

- [listing_mapper.py](/home/staz/Downloads/myProjetctKufar/api/services/listing_mapper.py)
  - сбор полной карточки объявления.

### База данных

Используется SQLAlchemy + Alembic.

Основные модели:

- `Tracker`
- `QuerySnapshot`
- `QueryListingState`

Ключевой файл:

- [models.py](/home/staz/Downloads/myProjetctKufar/api/models.py)

### Scheduler

Scheduler:

- берёт активные трекеры;
- группирует их по уникальному запросу;
- не гоняет один и тот же запрос повторно для каждого пользователя;
- на основе одной выборки:
  - обновляет history snapshot;
  - фиксирует новые объявления;
  - фиксирует падение цены;
  - рассылает уведомления.

Ключевой файл:

- [collector.py](/home/staz/Downloads/myProjetctKufar/scheduler/collector.py)

### Telegram Bot

Роли бота:

- команды `/start`, `/help`, `/app`;
- быстрые команды аналитики;
- работа с трекерами;
- открытие Mini App.

Ключевые файлы:

- [bot/main.py](/home/staz/Downloads/myProjetctKufar/bot/main.py)
- [bot/handlers](/home/staz/Downloads/myProjetctKufar/bot/handlers)

### Mini App frontend

Mini App ориентирован на компактный сценарий Telegram.

Текущая структура:

- поиск;
- BYN/USD toggle;
- курс НБРБ;
- вкладки:
  - `Обзор`
  - `Объявления`
  - `Дёшево`
  - `Трекеры`

Ключевые файлы:

- [frontend/index.html](/home/staz/Downloads/myProjetctKufar/frontend/index.html)
- [frontend/js/app.js](/home/staz/Downloads/myProjetctKufar/frontend/js/app.js)
- [frontend/css/style.css](/home/staz/Downloads/myProjetctKufar/frontend/css/style.css)

---

## 5. Что уже реализовано

### 5.1 Полный рынок, а не первая страница

Ключевое бизнес-исправление:

- статистика считается по полному рынку Kufar;
- больше нет логики "только первая страница".

Это относится к:

- `price-stats`
- `listings`
- `segments`

### 5.2 Статистика рынка

Реализовано:

- средняя цена;
- медиана;
- квартильные точки;
- минимум / максимум;
- охват:
  - `analyzed_count`
  - `total_results`

### 5.3 Сегменты рынка

Сейчас есть разрез:

- `new_private`
- `new_shop`
- `used_private`
- `used_shop`

### 5.4 Объявления

Реализовано:

- свежие объявления;
- сортировка по цене вверх/вниз;
- сортировка рядом с медианой;
- отдельный режим `Дёшево`.

### 5.5 Дешёвые объявления

Реализовано:

- фильтрация объявлений ниже медианы на `X%`;
- сейчас в UI есть пороги:
  - `-10%`
  - `-15%`
  - `-20%`

### 5.6 Полная карточка объявления

Внутри Mini App можно открыть карточку объявления и увидеть:

- фото;
- цену;
- параметры;
- продавца;
- описание, если оно есть;
- ссылку на Kufar.

### 5.7 История рынка

Реализовано:

- таблица `query_snapshots`;
- endpoint `/api/v1/price-history`;
- график `Динамика медианы` за 7 дней в Mini App.

История копится:

- из scheduler;
- из обычного пользовательского поиска.

### 5.8 Трекеры

Реализовано:

- создание трекера;
- список трекеров;
- удаление трекера;
- открытие запроса из трекера;
- уведомления о новых объявлениях;
- foundation для price-drop alerts.

### 5.9 Price-drop foundation

Реализовано на уровне данных:

- `last_seen_price_byn` у `Tracker`;
- `QueryListingState.last_price_byn`;
- фиксация случаев снижения цены в scheduler;
- улучшенное текстовое уведомление.

### 5.10 Валюты

По требованию пользователя:

- убран `EUR`;
- оставлены только:
  - `BYN`
  - `USD`
- приоритетная валюта: `BYN`;
- сверху в Mini App показывается:
  - `1 USD = ... BYN`

### 5.11 Локальный запуск

Реализованы скрипты:

- [start-local.sh](/home/staz/Downloads/myProjetctKufar/start-local.sh)
- [stop-local.sh](/home/staz/Downloads/myProjetctKufar/stop-local.sh)
- [status-local.sh](/home/staz/Downloads/myProjetctKufar/status-local.sh)

Что умеет `start-local.sh`:

- поднимать frontend;
- опционально поднимать `cloudflared`;
- обновлять `MINI_APP_URL` в `.env`;
- прогонять Alembic;
- запускать API;
- запускать бота.

### 5.12 Исправления миграций

После появления Alembic выяснилось, что локальная SQLite уже могла содержать таблицы, созданные через `create_all`.

Исправлено:

- миграции 0001 и 0002 стали идемпотентными для локальной среды;
- `start-local.sh` теперь не падает на уже существующей схеме.

---

## 6. Текущее состояние Mini App

### Навигация

Вкладки:

- `Обзор`
- `Объявления`
- `Дёшево`
- `Трекеры`

### Обзор

Показывает:

- summary strip;
- статистику рынка;
- распределение цен;
- график динамики медианы;
- сегменты.

### Объявления

Показывает:

- свежие объявления;
- сортировки;
- доступ к полной карточке;
- внешнюю ссылку на Kufar.

### Дёшево

Показывает:

- дешёвые объявления относительно медианы;
- пороги `-10% / -15% / -20%`.

### Трекеры

Позволяет:

- создать трекер по текущему запросу;
- обновить список;
- открыть запрос;
- удалить трекер.

---

## 7. Что уже проверено

Последнее проверенное состояние:

- `uv run pytest -q` -> `64 passed`
- `uv run ruff check .` -> `All checks passed!`
- `node --check frontend/js/app.js` -> OK
- `uv run alembic -c migrations/alembic.ini upgrade head` -> OK

---

## 8. Что решено делать дальше

Это не просто идеи, а согласованный roadmap после обсуждения пользы, нагрузки и будущего накопления данных.

### Делать следующим приоритетом

1. Полноценные `price-drop alerts`
2. Более явный UI для изменений цены внутри Mini App
3. Мультисравнение товаров
4. Карта/разрез по регионам

### Уже начали копить фундамент

Накопление данных уже есть для:

- динамики цен;
- price-drop alerts;
- будущей оценки исчезновения объявлений;
- будущего индекса ходовости.

### Сознательно отложено

Не в ближайший приоритет:

- `скорость продажи`
- `индекс ходовости`
- `сезонность`
- `рейтинг продавцов`
- `разбивка по категориям`

Причина:

- сначала нужна накопленная история и более стабильный фундамент.

### Пока не делать

Согласованно не берём в ближнюю итерацию:

- `прогноз цены (ML)`
- `кластеризация`
- `детектор аномалий` в ML-виде
- `кросс-платформенное сравнение`

Причина:

- высокая сложность;
- спорный ROI;
- ненужная нагрузка на ранней стадии.

---

## 9. Практический roadmap

### Этап 1: уже сделано

- полный рынок;
- статистика;
- сегменты;
- дешёвые объявления;
- трекеры;
- история снапшотов;
- график динамики;
- полная карточка объявления;
- удобный локальный запуск.

### Этап 2: следующий

- сделать price-drop alerts полноценной user-facing функцией;
- показать в Mini App, какие объявления подешевели;
- улучшить хранение истории по конкретным объявлениям;
- доделать UX вокруг trackers/history.

### Этап 3: после накопления истории

- скорость продажи;
- индекс ходовости;
- сезонность;
- сравнительные дашборды.

---

## 10. Важные технические решения

### 10.1 Почему не парсим HTML как основную стратегию

Основная ставка сделана на JSON API Kufar, а не на HTML scraping.

Причины:

- меньше нагрузки;
- стабильнее;
- быстрее;
- проще кэшировать;
- проще тестировать.

### 10.2 Почему история копится через запросы пользователей

Чтобы не перегружать сервер:

- не сканируем весь Kufar в фоне;
- копим историю только по активным пользовательским запросам;
- переиспользуем уже сделанные полные поиски.

### 10.3 Почему BYN и USD

Это было прямым пользовательским требованием.

Оставлены:

- `BYN`
- `USD`

Убран:

- `EUR`

### 10.4 Почему отдельный раздел `Дёшево`

Польза этой функции достаточно высокая, чтобы не прятать её просто как один sort.

Отдельный view делает сценарий понятнее:

- пользователь ищет не просто все объявления;
- пользователь ищет выгодные относительно рынка предложения.

---

## 11. Локальный запуск

### Базовый запуск

```bash
cd ~/Downloads/myProjetctKufar
./start-local.sh --with-tunnel
```

### Остановка

```bash
./stop-local.sh
```

### Статус

```bash
./status-local.sh
```

### Что делает `--with-tunnel`

- запускает `cloudflared`;
- получает новый `https://...trycloudflare.com`;
- пишет его в `.env` как `MINI_APP_URL`;
- запускает проект.

### Важный нюанс про Telegram Mini App

Старые сообщения бота содержат старый `web_app` URL.

Если tunnel сменился:

1. нужно перезапустить бота;
2. нужно отправить новый `/app`;
3. нужно открывать Mini App из нового сообщения;
4. старые кнопки будут вести на мёртвый URL.

---

## 12. Известные нюансы и caveats

### Redis

Если локальный Redis не поднят:

- API может логировать ошибку ping;
- затем переключается на `MemoryCache`.

Это допустимо для локальной разработки.

### 502 в Mini App

Если frontend работает в Docker, а API локально:

- API должен слушать `0.0.0.0:8010`, а не только `127.0.0.1:8010`.

### Cloudflare Tunnel

`trycloudflare.com` URL временный.

При каждом новом запуске:

- URL меняется;
- бот должен быть перезапущен;
- Mini App нужно открывать заново через новый `/app`.

### История цены

Сразу после нового запроса график может быть пустым или коротким.

Это нормально:

- история только начинает копиться;
- данные будут появляться после поисков и фоновых проверок.

---

## 13. Что стоит проверить следующему ИИ в первую очередь

Если работу подхватывает другой ИИ, ему стоит начать с этого:

1. Проверить локальный запуск:
   - `./start-local.sh --with-tunnel`

2. Проверить статус:
   - `./status-local.sh`

3. Пройти быстрый smoke:
   - `/start`
   - `/app`
   - поиск в Mini App
   - `Объявления`
   - `Дёшево`
   - `Трекеры`

4. Проверить backend:
   - `GET /api/v1/price-stats`
   - `GET /api/v1/price-history`
   - `GET /api/v1/listings`
   - `GET /api/v1/segments`
   - `GET /api/v1/trackers`

5. Проверить тесты:
   - `uv run pytest -q`
   - `uv run ruff check .`

---

## 14. Файлы, которые наиболее важны для продолжения работы

Backend:

- [api/main.py](/home/staz/Downloads/myProjetctKufar/api/main.py)
- [api/models.py](/home/staz/Downloads/myProjetctKufar/api/models.py)
- [api/schemas.py](/home/staz/Downloads/myProjetctKufar/api/schemas.py)
- [api/routers/price_stats.py](/home/staz/Downloads/myProjetctKufar/api/routers/price_stats.py)
- [api/routers/price_history.py](/home/staz/Downloads/myProjetctKufar/api/routers/price_history.py)
- [api/routers/listings.py](/home/staz/Downloads/myProjetctKufar/api/routers/listings.py)
- [api/routers/trackers.py](/home/staz/Downloads/myProjetctKufar/api/routers/trackers.py)
- [api/services/kufar_client.py](/home/staz/Downloads/myProjetctKufar/api/services/kufar_client.py)
- [api/services/aggregator.py](/home/staz/Downloads/myProjetctKufar/api/services/aggregator.py)
- [api/services/history_service.py](/home/staz/Downloads/myProjetctKufar/api/services/history_service.py)

Scheduler:

- [scheduler/collector.py](/home/staz/Downloads/myProjetctKufar/scheduler/collector.py)

Frontend:

- [frontend/index.html](/home/staz/Downloads/myProjetctKufar/frontend/index.html)
- [frontend/js/app.js](/home/staz/Downloads/myProjetctKufar/frontend/js/app.js)
- [frontend/css/style.css](/home/staz/Downloads/myProjetctKufar/frontend/css/style.css)

Infra / local dev:

- [start-local.sh](/home/staz/Downloads/myProjetctKufar/start-local.sh)
- [stop-local.sh](/home/staz/Downloads/myProjetctKufar/stop-local.sh)
- [status-local.sh](/home/staz/Downloads/myProjetctKufar/status-local.sh)
- [docker-compose.yml](/home/staz/Downloads/myProjetctKufar/docker-compose.yml)
- [nginx/default.conf](/home/staz/Downloads/myProjetctKufar/nginx/default.conf)

Migrations:

- [20260405_0001_create_trackers.py](/home/staz/Downloads/myProjetctKufar/migrations/versions/20260405_0001_create_trackers.py)
- [20260406_0002_history_and_price_tracking.py](/home/staz/Downloads/myProjetctKufar/migrations/versions/20260406_0002_history_and_price_tracking.py)

---

## 15. Рекомендуемая следующая задача

Если продолжать разработку логично и без лишней нагрузки, следующий самый сильный шаг:

- сделать полноценный пользовательский сценарий `падение цены`.

То есть:

- не только фиксировать price-drop в scheduler;
- но и показывать его в Mini App;
- хранить историю изменения цены по объявлениям;
- возможно добавить отдельный список/вид `Подешевело`.

Это даст:

- практическую пользу уже сейчас;
- прямое продолжение уже готовой архитектуры;
- минимальный перерасход по серверу.

---

## 16. Итог

Проект уже не является прототипом "на коленке". Сейчас это рабочая база со следующими сильными сторонами:

- расчёт по полному рынку;
- хороший фундамент API;
- Telegram bot + Mini App;
- история рынка;
- трекеры;
- дешёвые объявления;
- полная карточка объявления;
- локальный запуск одной командой;
- тесты и линт в зелёном состоянии.

Следующая разработка должна идти не в сторону сложного ML, а в сторону:

- падения цены;
- сравнений;
- регионов;
- накопления истории;
- дальнейшей монетизируемой пользовательской аналитики.
