"""
INF-H6: Lightweight HTTP health-check server for the bot and
scheduler workers.

Both services used to ship a `pgrep -f python` check in
docker-compose.yml, which only verifies that the interpreter is
alive — it can't tell the event loop is frozen, a deadlock is in
flight, or the Telegram/DB connection has drifted. This module
starts a minimal aiohttp server on a configurable port that serves:

    GET /health/live   → 200 if the asyncio loop is still turning
    GET /health/ready  → 200 if the user-supplied readiness check
                         (e.g. "can I SELECT 1 on the DB?") succeeds

Kept intentionally small: no middleware, no auth (listens on the
container's internal network only), no external deps beyond aiohttp
which is already a transitive aiogram dependency.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

from aiohttp import web

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[bool]]


async def _handle_live(request: web.Request) -> web.Response:
    """Liveness: we only need to prove the event loop is scheduling
    our coroutine. A trivial `await asyncio.sleep(0)` is enough."""
    del request
    start = time.monotonic()
    await asyncio.sleep(0)
    latency_ms = (time.monotonic() - start) * 1000
    return web.json_response(
        {"status": "alive", "loop_latency_ms": round(latency_ms, 3)}
    )


def _build_ready_handler(
    readiness: ReadinessCheck | None,
) -> Callable[[web.Request], Awaitable[web.Response]]:
    async def _handle_ready(request: web.Request) -> web.Response:
        del request
        if readiness is None:
            # No readiness probe configured → fall back to liveness so
            # Docker can still distinguish "process up" vs "process
            # missing". Services that want strict readiness must pass
            # a callable.
            return web.json_response({"status": "ready"})
        try:
            ok = await asyncio.wait_for(readiness(), timeout=5.0)
        except TimeoutError:
            logger.warning("Healthcheck readiness timed out")
            return web.json_response(
                {"status": "timeout"}, status=503
            )
        except Exception as exc:  # pragma: no cover — surfaced via log
            # H9: log full error internally but return generic message
            logger.warning("Healthcheck readiness raised: %s", exc)
            return web.json_response(
                {"status": "error"}, status=503
            )
        if not ok:
            return web.json_response({"status": "unready"}, status=503)
        return web.json_response({"status": "ready"})

    return _handle_ready


def build_health_app(readiness: ReadinessCheck | None = None) -> web.Application:
    """Build the tiny health-check aiohttp Application.

    Split out so tests can exercise the handlers without opening a
    real socket.
    """
    app = web.Application()
    app.router.add_get("/health/live", _handle_live)
    app.router.add_get("/health/ready", _build_ready_handler(readiness))
    return app


async def start_health_server(
    *,
    port: int,
    readiness: ReadinessCheck | None = None,
    host: str = "0.0.0.0",
) -> web.AppRunner:
    """Start the aiohttp health server and return the AppRunner.

    Callers must `await runner.cleanup()` during shutdown. The server
    is run as a background TCP site; failures on bind are logged and
    re-raised so the process can fail-fast if the port is in use (we
    want the container to restart rather than silently lose its
    healthcheck).
    """
    app = build_health_app(readiness)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Health server listening on %s:%d", host, port)
    return runner


async def stop_health_server(runner: web.AppRunner | None) -> None:
    """Gracefully shut the health server down. Safe to call with None."""
    if runner is None:
        return
    with suppress(Exception):
        await runner.cleanup()
