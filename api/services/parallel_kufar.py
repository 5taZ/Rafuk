from __future__ import annotations

import asyncio
from typing import Any

from api.config import Settings
from api.services.kufar_client import KufarClient


async def parallel_search_all(
    client: KufarClient,
    tasks: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Run several full paginations concurrently, at most KUFAR_PARALLEL_SEMAPHORE at once.

    Individual requests are additionally paced process-wide inside KufarClient.
    """
    semaphore = asyncio.Semaphore(max(1, settings.kufar_parallel_semaphore))

    async def bounded_search(task: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await client.search_all_ads(**task)

    return list(await asyncio.gather(*(bounded_search(task) for task in tasks)))
