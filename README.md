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
- `migrations/` Alembic configuration and schema history
- `tests/` unit and smoke tests

## Quick Start

1. Copy `.env.example` to `.env` and fill in the values.
2. Install dependencies with `uv sync --extra dev`.
3. Run the API with `uv run uvicorn api.main:app --reload`.
4. Run the bot with `uv run python -m bot.main`.
5. Run the scheduler with `uv run python -m scheduler.collector`.

## Notes

- The client uses `api.kufar.by` instead of HTML scraping because Kufar is a SPA.
- Redis failures degrade to cache misses instead of crashing the request flow.
- Telegram Mini App requests should send `X-Telegram-Init-Data`.
