from __future__ import annotations

import logging
import os

from fastapi import Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings, get_settings
from api.database import get_engine
from api.database import get_session_factory as build_session_factory
from api.middleware.telegram_auth import TelegramInitData, verify_telegram_init_data
from api.models import User
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient

logger = logging.getLogger(__name__)


def get_settings_dependency() -> Settings:
    return get_settings()


def get_cache(request: Request) -> CacheBackend:
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        return cache
    raise RuntimeError(
        "Cache not initialized in app.state — lifespan must set it before serving requests"
    )


def get_currency_service(request: Request) -> CurrencyService:
    service = getattr(request.app.state, "currency_service", None)
    if service is not None:
        return service
    return CurrencyService(get_cache(request))


def get_kufar_client(request: Request) -> KufarClient:
    """Get the shared KufarClient from app state (created in lifespan).

    The previous implementation silently fabricated a fresh KufarClient
    when lifespan didn't run — and never closed it. Each fallback
    request leaked an httpx.AsyncClient + its connection pool, slowly
    exhausting file descriptors. Fail loud instead so a misconfigured
    deployment is caught immediately rather than degrading days later.
    Tests that need a different client should use FastAPI's
    ``dependency_overrides`` (every existing test already does).
    """
    client = getattr(request.app.state, "kufar_client", None)
    if client is None:
        raise RuntimeError(
            "KufarClient is not initialized in app.state. The lifespan "
            "context must run before serving requests, or the test "
            "must override `get_kufar_client` via `dependency_overrides`."
        )
    return client


def get_session_factory_dependency(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is not None:
        return factory
    logger.warning(
        "Falling back to creating a new DB engine/session_factory. "
        "Ensure lifespan-managed session_factory is available in production."
    )
    return build_session_factory(get_engine())


async def get_telegram_user(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> TelegramInitData:
    settings = get_settings()
    # Defensive: refuse any path that could enable auth bypass in
    # production. The Settings validator already rejects auth_bypass=True
    # on a non-local DB, but we double-check here so a misconfigured env
    # never silently authenticates strangers as user_id=0.
    if settings.auth_bypass and os.environ.get("ENV") == "production":
        raise RuntimeError("auth_bypass is not allowed in production")
    if not x_telegram_init_data:
        if settings.auth_bypass:
            logger.warning(
                "auth_bypass=True: allowing request without Telegram initData "
                "(user_id=0). This must never run in production.",
            )
            user = TelegramInitData(user_id=0, first_name="Debug", raw={})
            request.state.telegram_user = user
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram initData header",
        )
    try:
        bot_token = settings.bot_token.get_secret_value()
        user = verify_telegram_init_data(
            x_telegram_init_data,
            bot_token,
            max_age_seconds=settings.telegram_init_data_max_age,
        )
    except ValueError as exc:
        logger.warning("Telegram auth failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram authentication",
        ) from exc

    # SEC-H6: refuse blacklisted users. Lookup failures are fail-open
    # (logged) — a Redis blip must not nuke every authenticated session.
    cache = getattr(request.app.state, "cache", None)
    from api.services.session_security import (  # noqa: PLC0415 — avoid cycle
        is_user_blacklisted,
        track_init_data_use,
    )

    if await is_user_blacklisted(cache, user.user_id):
        logger.warning("Blocked blacklisted user_id=%s", user.user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is suspended",
        )

    # SEC-H1: track first-seen IP per initData and log on mismatch.
    # We can't outright reject replay (the Mini App genuinely reuses
    # one initData for many requests), but the log gives ops a signal
    # they can feed back into the blacklist.
    client_ip = request.client.host if request.client else None
    await track_init_data_use(
        cache, x_telegram_init_data, user_id=user.user_id, client_ip=client_ip,
    )

    request.state.telegram_user = user
    return user


async def ensure_user_exists(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_user_id: int,
    first_name: str = "",
) -> None:
    """Upsert a User row for the given telegram_user_id.

    Called after Telegram auth to prevent FK violations on first request.
    Best-effort: if the DB is unreachable the error will surface downstream.
    """
    if telegram_user_id == 0:
        return  # Debug mode — no real user to persist
    async with session_factory() as session:
        existing = await session.execute(
            select(User.id).where(User.telegram_user_id == telegram_user_id)
        )
        if existing.scalar_one_or_none() is None:
            try:
                session.add(
                    User(telegram_user_id=telegram_user_id, first_name=first_name[:128])
                )
                await session.commit()
            except IntegrityError:
                # Concurrent request already inserted this user — rollback and continue
                await session.rollback()
