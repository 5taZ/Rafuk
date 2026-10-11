# Kufar Analytics

Kufar Analytics is a Telegram Mini App stack for analyzing listings from `kufar.by`.
The project uses the public site JSON endpoints behind the Kufar SPA and provides:

- FastAPI API for price statistics, listings, segments, and currency rates
- aiogram 3 bot for commands and tracker management
- APScheduler collector for listing alerts
- static frontend for Telegram WebApp
- Docker Compose deployment with PostgreSQL, Redis, and Nginx

## Project Layout

- `api/` application API, domain services, middleware, and routers
- `bot/` Telegram bot entrypoint, keyboards, and handlers
- `scheduler/` tracker polling jobs
- `frontend/` Telegram Mini App
- `nginx/templates/` nginx config template for the frontend container
- `migrations/` Alembic configuration and schema history
- `tests/` unit and smoke tests

## Configuration

Copy `.env.example` to `.env` and fill in at least `BOT_TOKEN`, `API_BASE_URL` and
`MINI_APP_URL`. One `.env` serves both run modes below:

- `DATABASE_URL` / `REDIS_URL` are used by processes on the host and point at the
  loopback ports of the compose containers (`localhost:5433`, `localhost:6380`) or at SQLite.
- Compose services ignore them and connect to `db:5432` / `redis:6379`, built from the
  `POSTGRES_*` values (override with `COMPOSE_DATABASE_URL` / `COMPOSE_REDIS_URL`).

The tables are created by Alembic migrations only, never at application start.

## Quick Start: Docker Compose

Everything in containers. Requires Docker with Compose v2.

```bash
cp .env.example .env            # then edit it
docker compose up -d --build
```

Compose starts Postgres and Redis, runs the one-shot `migrate` service
(`alembic -c migrations/alembic.ini upgrade head`), then starts `api`, `bot` and
`scheduler`, and finally the nginx `frontend` on <http://127.0.0.1:8081>, which proxies
`/api/` to the `api` service. Long-running services restart automatically
(`restart: unless-stopped`). After pulling new code, run the same command again: the
migrations are applied before the services start. nginx re-resolves the `api` service
name every 10 seconds (`resolve` in the upstream, `NGINX_API_UPSTREAM_PARAMS`) and compose
restarts the frontend whenever it recreates `api`, so a new `api` container is picked up
without a manual `docker compose restart frontend`.

The frontend port is published on `127.0.0.1:8081` only (`FRONTEND_BIND_ADDRESS`). nginx
trusts `X-Forwarded-For` from Docker's private addresses, which is safe only while every
client is a local process: expose the Mini App through a TLS reverse proxy or a tunnel
(Telegram requires HTTPS anyway), not by publishing 8081 on a public interface.

Useful commands:

```bash
docker compose ps
docker compose logs -f api bot scheduler
docker compose run --rm migrate          # apply migrations manually
docker compose down                      # stop (data stays in the pgdata volume)
```

Postgres (`5433`) and Redis (`6380`) are published on `127.0.0.1` only. Change
`POSTGRES_PASSWORD` before deploying anywhere reachable from the network. Compose inserts
`POSTGRES_USER` and `POSTGRES_PASSWORD` into its `DATABASE_URL` verbatim, so use URL-safe
characters only (letters, digits, `-`, `_`, `.`, `~`; e.g. `openssl rand -hex 24`). With
`@`, `:`, `/`, `%` and the like the URL breaks or decodes to a different password and the
`migrate` service cannot connect; if you need them, percent-encode the password in
`COMPOSE_DATABASE_URL` (and in the host-mode `DATABASE_URL`).

Redis runs without a password by default. Set `REDIS_PASSWORD` in `.env` (URL-safe
characters only, e.g. `openssl rand -hex 24`) to start it with `--requirepass`; the
compose services then get the password in their `REDIS_URL` automatically, and for host
mode change `REDIS_URL` to `redis://:<password>@localhost:6380/0`. Recreate the container
after changing it: `docker compose up -d redis`.

The containerized API serves no `/docs`, `/redoc` or `/openapi.json`
(`API_DOCS_ENABLED=false`; set `COMPOSE_API_DOCS_ENABLED=true` to turn them on). Its
healthcheck calls `GET /healthz`, which answers `{"status": "ok"}` without touching the
database, Redis or Kufar and is not rate limited.

## Quick Start: Host Mode (development)

The API, bot and scheduler run on the host through uv; Postgres, Redis and the nginx
frontend run in Docker. Requires [uv](https://docs.astral.sh/uv/), Docker and bash
(Linux, macOS, WSL or Git Bash).

```bash
cp .env.example .env            # host-mode URLs: localhost:5433 / localhost:6380
uv sync --extra dev
./start-local.sh                # or: ./start-local.sh --with-tunnel
./status-local.sh
./stop-local.sh                 # add --all to stop the Postgres and Redis containers too
```

`start-local.sh`:

1. stops the processes it started earlier (PID files in `.run/`) and the `api`, `bot` and
   `scheduler` containers of a full compose stack (two bots with one token conflict, two
   schedulers send duplicate alerts; `docker compose up -d` starts them again), and
   refuses to start if port 8010 is taken by something else;
2. starts `db` and `redis` (`docker compose up -d --wait db redis`, skip with `--no-infra`
   when `DATABASE_URL` points at SQLite);
3. starts the `frontend` container with `NGINX_API_UPSTREAM=host.docker.internal:8010`
   and an empty `NGINX_API_UPSTREAM_PARAMS` (`host.docker.internal` comes from the
   container's `/etc/hosts`, which nginx's runtime resolver does not read);
4. with `--with-tunnel`, opens a Cloudflare quick tunnel to the frontend and writes its URL
   to `MINI_APP_URL` in `.env`;
5. runs `alembic -c migrations/alembic.ini upgrade head`;
6. starts the API (uvicorn with `--proxy-headers`), waits for `GET /healthz` to answer
   200, then starts the bot and the scheduler; logs go to `.run/*.log`.

A host-mode API serves `/docs` unless `API_DOCS_ENABLED=false` is set in `.env`.

The API binds to the address Docker uses for `host.docker.internal` (the host loopback
with Docker Desktop, the `docker0` bridge address on native Linux), not to all interfaces.
Override with environment variables: `LOCAL_API_HOST=127.0.0.1 LOCAL_API_PORT=8011
./start-local.sh`.

Running the pieces by hand instead of the script:

```bash
docker compose up -d --wait db redis
uv run alembic -c migrations/alembic.ini upgrade head
uv run uvicorn api.main:app --reload --host 127.0.0.1 --port 8010
uv run python -m bot.main
uv run python -m scheduler.collector
NGINX_API_UPSTREAM=host.docker.internal:8010 NGINX_API_UPSTREAM_PARAMS= \
    docker compose up -d --no-deps frontend
```

With native Docker on Linux, the frontend container reaches the host API only if uvicorn
listens on the `docker0` address (usually `172.17.0.1`) instead of `127.0.0.1`.

Without Docker, set `DATABASE_URL=sqlite+aiosqlite:///./bot.db`; if Redis is unreachable,
the API falls back to an in-memory cache.

## Tests and Lint

```bash
uv run pytest
uv run ruff check .
uv lock --check                 # CI and the Docker build use uv sync --locked
```

## Notes

- The client uses `api.kufar.by` instead of HTML scraping because Kufar is a SPA.
- Redis failures degrade to cache misses instead of crashing the request flow.
- Telegram Mini App requests should send `X-Telegram-Init-Data`.
- CORS is handled by the API only; nginx does not add `Access-Control-*` headers.
- The scheduler checks trackers every `ALERT_CHECK_INTERVAL` minutes. A tracker's
  `interval_min` is stored as at least that value, and a query is skipped in a run only
  when none of its trackers is due yet.
- When a user blocks the bot, the scheduler pauses their trackers; `/start` resumes them.
