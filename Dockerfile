FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY . .

# Change ownership of app directory to appuser
RUN chown -R appuser:appuser /app

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

CMD ["sh", "-c", "case \"$SERVICE\" in api) uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-4} --log-level ${LOG_LEVEL:-info} ;; bot) uv run python -m bot.main ;; scheduler) uv run python -m scheduler.collector ;; *) echo \"Unknown SERVICE=$SERVICE\"; exit 1 ;; esac"]
