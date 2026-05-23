"""
INF-H9: Structured logging baseline for the API, bot, and scheduler.

Why this module exists
----------------------
Before this change each entry-point configured logging
independently:

* `api/main.py` relied on uvicorn's default formatter, so `logger.info`
  calls from our own modules came out mixed with access-log text.
* `scheduler/collector.py` called `logging.basicConfig(format=...)`
  with a plain text format.
* `bot/main.py` called `logging.basicConfig(level=INFO)` with the
  stdlib default formatter.

That made cross-service greps painful (three different formats) and
left us without request-ID correlation — a single user flow hits the
API and the bot, and there was no way to tie the two together.

This module:

1. Emits JSON Lines by default (one log record per line), with a
   `timestamp` / `level` / `logger` / `message` skeleton plus any
   extra kwargs the caller passed to `logger.info(..., extra={...})`.
2. Respects `LOG_LEVEL` env var (INFO default).
3. Respects `LOG_FORMAT=text` so local `docker compose logs` stays
   readable when a human is driving.
4. Exposes a `request_id_ctxvar` that the FastAPI middleware in
   `api/main.py` populates per request; the formatter pulls it in
   automatically so every log line produced while serving a request
   carries the same ID.

Keep this module dependency-free — it's imported by the scheduler
before FastAPI starts, and pulling in a heavy logging lib would slow
the cold start noticeably.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
from typing import Any

# Context var set by the API request middleware — None outside HTTP
# request scope (bot callbacks, scheduler jobs, …).
request_id_ctxvar: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class _JsonFormatter(logging.Formatter):
    """Minimal stdlib-only JSON formatter.

    We don't pull in `python-json-logger` — this class is ~30 lines
    and has no external deps.

    Fields:
      * ``ts``: ISO-8601 UTC timestamp with millisecond precision.
      * ``level``: WARNING / ERROR / …
      * ``logger``: the stdlib logger name (usually ``__name__``).
      * ``msg``: pre-formatted message (``%`` args already expanded).
      * ``service``: which entry-point emitted the log — filled from
        the ``SERVICE`` env var so API / bot / scheduler are easy to
        split in log aggregators.
      * ``request_id``: correlation ID when we're inside a request.
      * ``exc``: type + message when an exception is attached.

    Any ``extra={}`` kwargs the caller passes through to
    ``logger.info("...", extra={"user_id": 123})`` are merged into the
    top-level object so downstream search tools can filter on them.
    """

    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }

    def __init__(self, *, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self._format_ts(record.created),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "msg": record.getMessage(),
        }

        req_id = request_id_ctxvar.get()
        if req_id is not None:
            payload["request_id"] = req_id

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Merge any extra={} the caller attached. Skip the stdlib's
        # reserved names so we don't double-emit level/logger/etc.
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            try:
                json.dumps(value)  # probe serialisability
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _format_ts(created: float) -> str:
        """ISO-8601 UTC with millisecond precision."""
        # time.gmtime avoids TZ-aware datetime overhead; we want UTC.
        tm = time.gmtime(created)
        ms = int((created - int(created)) * 1000)
        return (
            f"{tm.tm_year:04d}-{tm.tm_mon:02d}-{tm.tm_mday:02d}T"
            f"{tm.tm_hour:02d}:{tm.tm_min:02d}:{tm.tm_sec:02d}.{ms:03d}Z"
        )


def configure_logging(service: str) -> None:
    """Install the structured logging config.

    ``service`` is stamped into every log line so API/bot/scheduler
    can be split by a single field in log aggregators. Call once per
    process, as early as possible in startup.

    Env vars:
      * ``LOG_LEVEL`` — defaults to INFO.
      * ``LOG_FORMAT`` — ``json`` (default) or ``text`` for a
        human-readable single-line fallback.

    Idempotent: calling configure_logging twice replaces the handlers
    so tests and reloads don't stack formatters.
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = os.environ.get("LOG_FORMAT", "json").lower()
    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt == "text":
        handler.setFormatter(
            logging.Formatter(
                fmt=(
                    "%(asctime)s [%(levelname)s] %(name)s "
                    f"[{service}]: %(message)s"
                ),
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    else:
        handler.setFormatter(_JsonFormatter(service=service))

    root = logging.getLogger()
    # Replace rather than .addHandler — we don't want stacked output
    # when configure_logging runs twice (e.g. --reload, tests).
    root.handlers = [handler]
    root.setLevel(level)

    # Silence noisy libraries that aren't useful at INFO.
    for noisy in ("urllib3", "httpx", "httpcore", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
