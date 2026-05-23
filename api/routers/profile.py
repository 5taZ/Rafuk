from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.schemas import ProfileRead
from api.services.account_status import build_profile
from api.services.cache import CacheBackend

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=ProfileRead)
async def get_my_profile(
    _user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    cache: CacheBackend = Depends(get_cache),
    settings: Settings = Depends(get_settings_dependency),
) -> ProfileRead:
    async with session_factory() as session:
        profile = await build_profile(
            session=session,
            cache=cache,
            telegram_user=_user,
            settings=settings,
        )
        await session.commit()
        return profile
