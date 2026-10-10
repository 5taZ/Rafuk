# Pinned uv release: bump deliberately, the lockfile format depends on it.
ARG UV_VERSION=0.9.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Non-root runtime user. The code and the virtualenv stay root-owned and read-only for it.
RUN groupadd --system appuser \
    && useradd --system --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer: rebuilt only when pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code (.dockerignore keeps .env, databases, .git and tests out of the image).
COPY . .
RUN uv sync --frozen --no-dev

USER appuser

ENV PATH="/app/.venv/bin:${PATH}"
ENV SERVICE=api
# Proxies whose X-Forwarded-For uvicorn trusts (nginx on the compose network).
ENV FORWARDED_ALLOW_IPS="127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

EXPOSE 8000

# Binaries are started from the virtualenv directly ("uv run" would re-check the
# environment on every start). Migrations run in the one-shot "migrate" compose service.
CMD ["sh", "-c", "case \"$SERVICE\" in api) exec /app/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\" ;; bot) exec /app/.venv/bin/python -m bot.main ;; scheduler) exec /app/.venv/bin/python -m scheduler.collector ;; migrate) exec /app/.venv/bin/alembic -c migrations/alembic.ini upgrade head ;; *) echo \"Unknown SERVICE=$SERVICE\"; exit 1 ;; esac"]
