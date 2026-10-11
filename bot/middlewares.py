from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

THROTTLED_TEXT = "Too many requests. Please wait a few seconds and try again."


class ThrottlingMiddleware(BaseMiddleware):
    """Per-user rate limit plus a process-wide concurrency cap for expensive commands.

    Register it on a router's ``message`` observer so it only wraps that router's
    handlers. A user may run one wrapped command every ``rate_seconds``; extra
    commands inside that window are dropped, and the user is told so once per window.
    """

    def __init__(
        self,
        *,
        rate_seconds: float = 3.0,
        max_concurrent: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_seconds = rate_seconds
        self.max_concurrent = max_concurrent
        self._clock = clock
        self._last_call: dict[int, float] = {}
        self._warned: set[int] = set()
        self._semaphore: asyncio.Semaphore | None = None

    def reset(self) -> None:
        """Forget every throttling window and the loop-bound semaphore (used by tests)."""
        self._last_call.clear()
        self._warned.clear()
        self._semaphore = None

    def _get_semaphore(self) -> asyncio.Semaphore | None:
        if self.max_concurrent is None:
            return None
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    def _prune(self, now: float) -> None:
        expired = [
            user_id
            for user_id, last_call in self._last_call.items()
            if now - last_call >= self.rate_seconds
        ]
        for user_id in expired:
            self._last_call.pop(user_id, None)
            self._warned.discard(user_id)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None:
            now = self._clock()
            if len(self._last_call) > 1024:
                self._prune(now)
            last_call = self._last_call.get(user.id)
            if last_call is not None and now - last_call < self.rate_seconds:
                answer = getattr(event, "answer", None)
                if user.id not in self._warned and callable(answer):
                    self._warned.add(user.id)
                    await answer(THROTTLED_TEXT)
                return None
            self._last_call[user.id] = now
            self._warned.discard(user.id)

        semaphore = self._get_semaphore()
        if semaphore is None:
            return await handler(event, data)
        async with semaphore:
            return await handler(event, data)
