from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

THROTTLED_TEXT = "Too many requests. Please wait a few seconds and try again."
USER_BUSY_TEXT = "Your previous request is still running. Please wait for the answer."
BUSY_TEXT = "The bot is busy with other requests right now. Please try again in a minute."

EventPredicate = Callable[[TelegramObject, dict[str, Any]], bool]


class ThrottlingMiddleware(BaseMiddleware):
    """Per-user rate limit plus a process-wide concurrency cap for expensive commands.

    Register it on a router's ``message`` observer so it only wraps that router's
    handlers. Events for which ``applies_to(event, data)`` is false (for example a command
    without a usable argument, which only gets a usage hint) pass straight through and use
    up neither the user's window nor a slot.

    For the other events:

    * a user may start one command every ``rate_seconds``; extra commands inside that window
      are dropped, and the user is told so once per window;
    * a user has at most one command in flight; another one is refused with a reply;
    * at most ``max_concurrent`` commands run at once process-wide and at most
      ``max_waiting`` more wait for a slot; a command beyond that is refused with a "busy"
      reply instead of joining an unbounded queue.
    """

    def __init__(
        self,
        *,
        rate_seconds: float = 3.0,
        max_concurrent: int | None = None,
        max_waiting: int = 0,
        applies_to: EventPredicate | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_seconds = rate_seconds
        self.max_concurrent = max_concurrent
        self.max_waiting = max(0, max_waiting)
        self._applies_to = applies_to
        self._clock = clock
        self._last_call: dict[int, float] = {}
        self._warned: set[int] = set()
        self._in_flight: set[int] = set()
        self._waiting = 0
        self._semaphore: asyncio.Semaphore | None = None

    def reset(self) -> None:
        """Forget every throttling window and the loop-bound semaphore (used by tests)."""
        self._last_call.clear()
        self._warned.clear()
        self._in_flight.clear()
        self._waiting = 0
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

    @staticmethod
    async def _reply(event: TelegramObject, text: str) -> None:
        answer = getattr(event, "answer", None)
        if callable(answer):
            await answer(text)

    def _within_window(self, user_id: int) -> bool:
        """Record a new command of the user; True when it falls into the current window."""
        now = self._clock()
        if len(self._last_call) > 1024:
            self._prune(now)
        last_call = self._last_call.get(user_id)
        if last_call is not None and now - last_call < self.rate_seconds:
            return True
        self._last_call[user_id] = now
        self._warned.discard(user_id)
        return False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self._applies_to is not None and not self._applies_to(event, data):
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        user_id: int | None = getattr(user, "id", None) if user is not None else None
        if user_id is not None:
            if self._within_window(user_id):
                if user_id not in self._warned:
                    self._warned.add(user_id)
                    await self._reply(event, THROTTLED_TEXT)
                return None
            if user_id in self._in_flight:
                await self._reply(event, USER_BUSY_TEXT)
                return None

        semaphore = self._get_semaphore()
        if semaphore is not None and semaphore.locked() and self._waiting >= self.max_waiting:
            await self._reply(event, BUSY_TEXT)
            return None

        if user_id is not None:
            self._in_flight.add(user_id)
        try:
            if semaphore is None:
                return await handler(event, data)
            self._waiting += 1
            try:
                await semaphore.acquire()
            finally:
                self._waiting -= 1
            try:
                return await handler(event, data)
            finally:
                semaphore.release()
        finally:
            if user_id is not None:
                self._in_flight.discard(user_id)
