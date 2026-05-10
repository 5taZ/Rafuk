from __future__ import annotations

import asyncio
import logging
from typing import Any

from api.config import Settings
from api.services.kufar_client import KufarClient

logger = logging.getLogger(__name__)

_EMPTY_RESPONSE: dict[str, Any] = {"ads": [], "pagination": {}, "total": 0}


# PERF-H7: do NOT wrap calls in another asyncio.Semaphore here.
#
# KufarClient already serialises through its own
# `self._semaphore = Semaphore(settings.kufar_parallel_semaphore)`. A
# second outer semaphore with the same bound merely double-counts the
# same parallelism budget — and historically the two semaphores combined
# with the per-request rate-limit lock to make the effective concurrency
# painfully low. We rely on the KufarClient-level limit alone now;
# `asyncio.gather` fans the work out and the client's semaphore caps it.


async def parallel_search(
    client: KufarClient,
    tasks: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    results = await asyncio.gather(
        *(client.search(**task) for task in tasks),
        return_exceptions=True,
    )
    out: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("parallel_search task failed: %s: %s", type(r).__name__, r)
            out.append(_EMPTY_RESPONSE)
        else:
            out.append(r)
    return out


async def parallel_search_all(
    client: KufarClient,
    tasks: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    results = await asyncio.gather(
        *(client.search_all_ads(**task) for task in tasks),
        return_exceptions=True,
    )
    out: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("parallel_search_all task failed: %s: %s", type(r).__name__, r)
            out.append(_EMPTY_RESPONSE)
        else:
            out.append(r)
    return out
