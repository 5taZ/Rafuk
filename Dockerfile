# RV-OPS-06: pinned uv release, the same one CI installs (UV_VERSION in
# .github/workflows/ci.yml). uv.lock was written by it: bump both together.
ARG UV_VERSION=0.12.19

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.12-slim AS base

# RV-OPS-01 / RV-OPS-13: no uv cache in the image layers, and bytecode compiled
# at build time: the services run on a read-only root filesystem, where neither
# a cache nor .pyc files can be written at start.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Create non-root user for security. RV-OPS-13: the code and the virtualenv
# stay root-owned (no chown -R layer), so the runtime user cannot rewrite them
# even when a container runs without read_only.
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# RV-OPS-13: dependency layer, rebuilt only when pyproject.toml or uv.lock
# change. RV-OPS-06: --locked fails the build when uv.lock is out of date with
# pyproject.toml instead of silently re-resolving it.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Application code; .dockerignore keeps secrets, tests, docs and the frontend out.
COPY . .
RUN uv sync --locked --no-dev

USER appuser

ENV PATH="/app/.venv/bin:${PATH}"
ENV SERVICE=api
# PERF-H1: spin multiple uvicorn worker processes so the API can use
# more than one CPU core. With WORKERS=1 the GIL turned every CPU-
# bound endpoint (deal_score, cluster stats, etc.) into a serialised
# queue. Default of 4 lines up with the docker-compose `cpus: 1.5`
# limit for the api service; override at deploy time if you raise the
# CPU budget. Workers share Redis cache and Postgres pool, so they
# coordinate state through those backends.
ENV WORKERS=4

EXPOSE 8000

# RV-OPS-01: binaries start from the virtualenv directly. `uv run` re-checks
# the environment on every start and may re-lock or re-sync it, which fails on
# the read-only root filesystem. `exec` makes the service PID 1, so it gets
# the SIGTERM of `docker stop`. SERVICE=migrate is the one-shot alembic run.
CMD ["sh", "-c", "case \"$SERVICE\" in api) exec /app/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers \"${WORKERS:-4}\" --log-level \"${LOG_LEVEL:-info}\" ;; bot) exec /app/.venv/bin/python -m bot.main ;; scheduler) exec /app/.venv/bin/python -m scheduler.collector ;; migrate) exec /app/.venv/bin/alembic -c migrations/alembic.ini upgrade head ;; *) echo \"Unknown SERVICE=$SERVICE\"; exit 1 ;; esac"]
