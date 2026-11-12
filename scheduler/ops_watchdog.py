"""Telegram alerts to the admins when the deployment degrades.

PRJ-REVIEW-1: ``/metrics`` exists but nothing scrapes it, so a stalled
tracker tick, a growing notification backlog, Kufar refusing requests or
Redis evicting keys went unnoticed until users complained. Without adding
an external monitoring service, the scheduler checks these every few
minutes and messages ``ADMIN_TELEGRAM_USER_IDS``, at most once per alert
per ``REPEAT_AFTER_SECONDS``. It covers the scheduler and the shared stores,
not the API process (a dead API fails its Docker healthcheck and restarts).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.models import TelegramNotificationDLQ
from api.services.account_status import admin_telegram_user_ids

logger = logging.getLogger(__name__)

TICK_STALE_SECONDS = 15 * 60
DLQ_BACKLOG_LIMIT = 200
REDIS_MEMORY_RATIO = 0.9
# Watchdog runs in a row that found the Kufar breaker open (one run = 5 min);
# a single open window is a 30 s blip, three in a row is an outage or a block.
KUFAR_OPEN_RUNS = 3
REPEAT_AFTER_SECONDS = 6 * 3600


@dataclass(slots=True)
class WatchdogState:
    started_at: float = field(default_factory=time.monotonic)
    last_tick_at: float | None = None
    evicted_keys: int | None = None
    kufar_open_runs: int = 0
    sent_at: dict[str, float] = field(default_factory=dict)


STATE = WatchdogState()


def mark_tick_completed() -> None:
    STATE.last_tick_at = time.monotonic()


def _tick_problem(now: float) -> str | None:
    since = now - (STATE.last_tick_at or STATE.started_at)
    if since < TICK_STALE_SECONDS:
        return None
    return (
        f"проверка трекеров не завершалась {int(since // 60)} мин — тик падает или зависает, "
        "либо его выполняет другой процесс планировщика"
    )


async def _redis_problems(cache: Any) -> list[tuple[str, str]]:
    client = getattr(cache, "_client", None)
    if client is None:
        return []
    try:
        info = await client.info()
    except Exception:
        return [("redis-down", "Redis не отвечает планировщику")]
    problems = []
    evicted = int(info.get("evicted_keys", 0))
    if STATE.evicted_keys is not None and evicted > STATE.evicted_keys:
        problems.append((
            "redis-evicted",
            f"Redis вытеснил {evicted - STATE.evicted_keys} ключ(ей) из-за нехватки памяти. "
            "Вместе с кэшем могут пропасть AI-квоты, баны и метки удалённых аккаунтов.",
        ))
    STATE.evicted_keys = evicted
    used, limit = int(info.get("used_memory", 0)), int(info.get("maxmemory", 0))
    if limit and used >= limit * REDIS_MEMORY_RATIO:
        problems.append((
            "redis-memory",
            f"Redis занял {used * 100 // limit}% лимита памяти "
            f"({used // 2**20} из {limit // 2**20} МБ)",
        ))
    return problems


async def _dlq_problem(session_factory: async_sessionmaker[AsyncSession]) -> str | None:
    async with session_factory() as session:
        backlog = (await session.execute(
            select(func.count()).select_from(TelegramNotificationDLQ)
        )).scalar_one()
    if backlog < DLQ_BACKLOG_LIMIT:
        return None
    return f"в очереди недоставленных уведомлений {backlog} сообщений"


def _kufar_problem(kufar_open: bool) -> str | None:
    STATE.kufar_open_runs = STATE.kufar_open_runs + 1 if kufar_open else 0
    if STATE.kufar_open_runs < KUFAR_OPEN_RUNS:
        return None
    return (
        "Kufar не отвечает планировщику уже несколько проверок подряд (circuit breaker открыт) — "
        "сбой Kufar или блокировка IP сервера"
    )


async def run_ops_watchdog(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cache: Any = None,
    kufar_open: bool = False,
) -> list[str]:
    """Check once, alert the admins, and return the alert keys found."""
    now = time.monotonic()
    problems: list[tuple[str, str]] = await _redis_problems(cache)
    for key, text in (
        ("tracker-tick", _tick_problem(now)),
        ("dlq-backlog", await _dlq_problem(session_factory)),
        ("kufar-down", _kufar_problem(kufar_open)),
    ):
        if text:
            problems.append((key, text))
    admins = sorted(admin_telegram_user_ids(settings))
    for key, text in problems:
        logger.error("Ops watchdog: %s", text)
        if not admins or now - STATE.sent_at.get(key, float("-inf")) < REPEAT_AFTER_SECONDS:
            continue
        STATE.sent_at[key] = now
        for admin_id in admins:
            try:
                await bot.send_message(admin_id, f"⚠️ Rafuk: {text}")
            except Exception:
                logger.warning("Ops watchdog: alert to admin %s failed", admin_id, exc_info=True)
    return [key for key, _ in problems]
