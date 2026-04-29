from __future__ import annotations

import asyncio
import logging
from typing import Any

from api.config import Settings
from api.services.kufar_client import KufarClient

logger = logging.getLogger(__name__)

_EMPTY_RESPONSE: dict[str, Any] = {"ads": [], "pagination": {}, "total": 0}


async def parallel_search(
    client: KufarClient,
    tasks: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(settings.kufar_parallel_semaphore)

    async def bounded_search(task: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await client.search(**task)

    results = await asyncio.gather(
        *(bounded_search(task) for task in tasks),
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
    semaphore = asyncio.Semaphore(settings.kufar_parallel_semaphore)

    async def bounded_search(task: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await client.search_all_ads(**task)

    results = await asyncio.gather(
        *(bounded_search(task) for task in tasks),
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
