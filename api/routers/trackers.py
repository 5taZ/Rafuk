from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.middleware.telegram_auth import TelegramInitData
from api.models import Tracker, TrackerEvent
from api.schemas import TrackerCreate, TrackerEventRead, TrackerRead
from api.services.reseller_tools import default_config_keyword

MAX_ACTIVE_TRACKERS = 10

router = APIRouter(tags=["trackers"])


@router.get("/tracker-events", response_model=list[TrackerEventRead])
async def get_tracker_events(
    limit: int = 20,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[TrackerEventRead]:
    bounded_limit = max(1, min(limit, 50))
    async with session_factory() as session:
        result = await session.execute(
            select(TrackerEvent)
            .where(TrackerEvent.user_id == telegram_user.user_id)
            .order_by(TrackerEvent.created_at.desc(), TrackerEvent.id.desc())
            .limit(bounded_limit)
        )
        return list(result.scalars())


@router.get("/trackers", response_model=list[TrackerRead])
async def get_trackers(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[TrackerRead]:
    async with session_factory() as session:
        result = await session.execute(
            select(Tracker)
            .where(Tracker.user_id == telegram_user.user_id, Tracker.active.is_(True))
            .order_by(Tracker.created_at.desc(), Tracker.id.desc())
        )
        return list(result.scalars())


@router.post("/trackers", response_model=TrackerRead, status_code=status.HTTP_201_CREATED)
async def create_tracker(
    payload: TrackerCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> TrackerRead:
    query = payload.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Query must not be empty",
        )

    async with session_factory() as session:
        count_result = await session.execute(
            select(func.count()).select_from(Tracker).where(
                Tracker.user_id == telegram_user.user_id,
                Tracker.active.is_(True),
            )
        )
        active_count = count_result.scalar_one()
        if active_count >= MAX_ACTIVE_TRACKERS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Maximum {MAX_ACTIVE_TRACKERS} active trackers allowed",
            )

        tracker = Tracker(
            user_id=telegram_user.user_id,
            query=query,
            strict_mode=payload.strict_mode,
            interval_min=payload.interval_min,
            min_discount_percent=payload.min_discount_percent,
            max_price_byn=payload.max_price_byn,
            seller_type=payload.seller_type,
            condition=payload.condition,
            region_name=payload.region_name,
            config_keyword=payload.config_keyword or default_config_keyword(query),
            exclude_duplicates=payload.exclude_duplicates,
        )
        session.add(tracker)
        await session.commit()
        await session.refresh(tracker)
        return TrackerRead.model_validate(tracker)


@router.delete("/trackers/{tracker_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracker(
    tracker_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        result = await session.execute(
            select(Tracker).where(
                Tracker.id == tracker_id,
                Tracker.user_id == telegram_user.user_id,
                Tracker.active.is_(True),
            )
        )
        tracker = result.scalar_one_or_none()
        if tracker is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracker not found")
        tracker.active = False
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
