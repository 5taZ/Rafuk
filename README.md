# Kufar Analytics

A Telegram Mini App + Telegram bot that scrapes the Belarusian
classifieds site **[Kufar.by](https://www.kufar.by)** for listings,
runs analytics and AI-assisted decisioning over them, and surfaces
"deal-finder" workflows to end users.

> **Status:** active development on the `bad-app` branch. 792 tests
> pass locally, with one skipped. See [`CHANGELOG.md`](./CHANGELOG.md)
> for the wave-by-wave security/performance hardening history.

## What it does

* **Scrapes Kufar listings** on a schedule and persists hourly snapshots
  per saved query (median price, q1/q3, count, free-vs-negotiable mix).
* **Tracks "leads"** the user is interested in — purchase price, target
  resale, sold price, deal-status pipeline, expense ledger.
* **Detects market signals** — price drops, new listings, trend
  reversals, low-supply windows, restock spikes.
* **AI analysis** of individual listings (condition, fit-for-purpose,
  resale potential, negotiation talking points) via a Together API-
  compatible Gemini model (`gemini-2.5-flash` by default).
* **Telegram bot** delivers notifications and analytics summaries.
* **Mini App** (vanilla JS, no bundler) shows charts, listing cards,
  watchlist filters, and the deal pipeline.

## Architecture

| Component | Folder | Notes |
|-----------|--------|-------|
| FastAPI backend | `api/` | Routers, services, middlewares |
| Telegram bot | `bot/` | Aiogram 3, long-polling |
| Scheduler | `scheduler/collector.py` | APScheduler — periodic scrape + alerts |
| Frontend | `frontend/` | Vanilla JS + plain CSS (no JSX, no bundler) |
| Migrations | `migrations/` | Alembic |
| Reverse proxy | `nginx/default.conf` | Static + CSP/headers |

Data plane: **PostgreSQL** (primary store, Numeric for money), **Redis 7**
(rate-limit, idempotency, cache, AI shadow store fallback). Public
ingress through Cloudflare Tunnel.

## Quick start (local dev)

You need:

* Python 3.12+ (managed via [`uv`](https://docs.astral.sh/uv/))
* PostgreSQL 16 OR `aiosqlite` for ephemeral dev
* Redis 7

```bash
# 1. install deps
uv sync --extra dev

# 2. copy env, fill in secrets
cp .env.example .env
$EDITOR .env       # BOT_TOKEN, DATABASE_URL, REDIS_URL at minimum

# 3. run migrations
uv run alembic -c migrations/alembic.ini upgrade head

# 4. boot the local stack (api + bot + scheduler)
./start-local.sh                # add --with-tunnel for cloudflared

# 5. or run components individually
uv run uvicorn api.main:app --reload --port 8010
uv run python -m bot.main
uv run python -m scheduler.collector
```

The Mini App frontend can be served with the dev proxy:

```bash
node proxy-server.mjs           # serves frontend/ on :8081, proxies /api/* to :8010
```

`./status-local.sh` shows what's running. `./stop-local.sh` stops the
local stack but preserves PostgreSQL data in the `myprojetctkufar_pgdata`
Docker volume, so restarts keep your saved searches, trackers, and deals.

## Tests

```bash
# all tests (uses aiosqlite locally)
uv run pytest --tb=short

# single file
uv run pytest tests/test_consent.py -x --tb=short

# linter (formatter not enforced — see AGENTS.md)
uv run ruff check .
```

CI runs the same matrix against PostgreSQL 16 — see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Production deployment

Four Docker services + Redis + cloudflared:

```bash
docker compose --profile tunnel up -d
```

See [`docker-compose.yml`](./docker-compose.yml). Resource limits are
tuned for a single-host dev box; scale up `WORKERS` and pool sizing
together if you raise the CPU budget.

### Observability boundaries

`/metrics` exposes Prometheus text. When the app is using Redis, counters
and summary totals are written to Redis hashes and the endpoint renders
cross-worker aggregates; if Redis is unavailable it falls back to the
current worker's in-memory counters. The
`kufar_metrics_backend_info{backend="..."}` series tells you which path
served the scrape, and `kufar_process_info{pid="..."}` identifies the
process that answered.

Dataset singleflight has two layers: an in-process future for same-worker
concurrency, and a Redis lock for cold fetches that cross worker
boundaries. If a sibling worker already owns a cold Kufar fetch, followers
poll the shared dataset cache and only fall back to a duplicate fetch
after a bounded timeout. Watch
`kufar_query_dataset_events_total{event="distributed_singleflight_timeout"}`
and `kufar_query_dataset_upstream_fetch_duration_seconds_count` to decide
whether the timeout/lock TTL needs tuning.

## Documentation

* [`AGENTS.md`](./AGENTS.md) — conventions, build/test commands,
  project map. Read this if you're working on the codebase (human or
  agent).
* [`docs/BACKUP_RUNBOOK.md`](./docs/BACKUP_RUNBOOK.md) — PostgreSQL
  backup, freshness-check, and restore-drill procedure.
* [`docs/MONITORING_RUNBOOK.md`](./docs/MONITORING_RUNBOOK.md) —
  health-check, metrics, and alerting checklist.
* [`CHANGELOG.md`](./CHANGELOG.md) — wave-by-wave fix log with audit
  IDs.
* [`docs/`](./docs/) — additional design notes and runbooks.

## Compliance

The consent and erasure flow is built around Belarus law **№99-З**
(personal data protection). See `api/routers/consent.py` and the
`UserConsent` / `ai_audit_log` tables.

## License

Private — not yet open-sourced.
