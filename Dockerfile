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

CMD ["sh", "-c", "case \"$SERVICE\" in api) uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 ;; bot) uv run python -m bot.main ;; scheduler) uv run python -m scheduler.collector ;; *) echo \"Unknown SERVICE=$SERVICE\"; exit 1 ;; esac"]
