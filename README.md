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
migrations are applied before the services start.

Useful commands:

```bash
docker compose ps
docker compose logs -f api bot scheduler
docker compose run --rm migrate          # apply migrations manually
docker compose down                      # stop (data stays in the pgdata volume)
```

Postgres (`5433`) and Redis (`6380`) are published on `127.0.0.1` only. Change
`POSTGRES_PASSWORD` before deploying anywhere reachable from the network.

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

1. stops the processes it started earlier (PID files in `.run/`) and refuses to start if
   port 8010 is taken by something else;
2. starts `db` and `redis` (`docker compose up -d --wait db redis`, skip with `--no-infra`
   when `DATABASE_URL` points at SQLite);
3. starts the `frontend` container with `NGINX_API_UPSTREAM=host.docker.internal:8010`;
4. with `--with-tunnel`, opens a Cloudflare quick tunnel to the frontend and writes its URL
   to `MINI_APP_URL` in `.env`;
5. runs `alembic -c migrations/alembic.ini upgrade head`;
6. starts the API (uvicorn with `--proxy-headers`), the bot and the scheduler; logs go to
   `.run/*.log`.

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
NGINX_API_UPSTREAM=host.docker.internal:8010 docker compose up -d --no-deps frontend
```

With native Docker on Linux, the frontend container reaches the host API only if uvicorn
listens on the `docker0` address (usually `172.17.0.1`) instead of `127.0.0.1`.

Without Docker, set `DATABASE_URL=sqlite+aiosqlite:///./bot.db`; if Redis is unreachable,
the API falls back to an in-memory cache.

## Tests and Lint

```bash
uv run pytest
uv run ruff check .
```

## Notes

- The client uses `api.kufar.by` instead of HTML scraping because Kufar is a SPA.
- Redis failures degrade to cache misses instead of crashing the request flow.
- Telegram Mini App requests should send `X-Telegram-Init-Data`.
- CORS is handled by the API only; nginx does not add `Access-Control-*` headers.
