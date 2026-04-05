from __future__ import annotations

from fastapi import Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings, get_settings
from api.database import get_engine
from api.database import get_session_factory as build_session_factory
from api.middleware.telegram_auth import TelegramInitData, verify_telegram_init_data
from api.services.cache import CacheBackend, RedisCache
from api.services.currency_service import CurrencyService


def get_settings_dependency() -> Settings:
    return get_settings()


def get_cache(request: Request) -> CacheBackend:
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        return cache
    return RedisCache.from_url(get_settings().redis_url)


def get_currency_service(request: Request) -> CurrencyService:
    service = getattr(request.app.state, "currency_service", None)
    if service is not None:
        return service
    return CurrencyService(get_cache(request))


def get_session_factory_dependency(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is not None:
        return factory
    return build_session_factory(get_engine())


def get_telegram_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> TelegramInitData:
    if not x_telegram_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram initData header",
        )
    try:
        return verify_telegram_init_data(x_telegram_init_data, get_settings().bot_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
