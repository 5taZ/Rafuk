from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.middleware.telegram_auth import TelegramInitData
from api.models import Tracker, TrackerEvent
from api.schemas import TrackerCreate, TrackerEventRead, TrackerRead
from api.services import tracker_service
from api.services.tracker_service import (
    MAX_DB_ID,
    DuplicateTrackerError,
    TrackerQuotaExceededError,
    TrackerValidationError,
)
from api.services.users import init_data_username

router = APIRouter(tags=["trackers"])


def _serialize_tracker(tracker: Tracker) -> TrackerRead:
    """Show the interval the scheduler really checks the tracker at, not the stored one."""
    payload = TrackerRead.model_validate(tracker)
    return payload.model_copy(
        update={"interval_min": tracker_service.real_check_interval_min(tracker.interval_min)}
    )


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


@router.delete("/tracker-events", status_code=status.HTTP_204_NO_CONTENT)
async def clear_tracker_events(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        await session.execute(
            delete(TrackerEvent).where(TrackerEvent.user_id == telegram_user.user_id)
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/trackers", response_model=list[TrackerRead])
async def get_trackers(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[TrackerRead]:
    async with session_factory() as session:
        trackers = await tracker_service.list_active_trackers(session, telegram_user.user_id)
    return [_serialize_tracker(tracker) for tracker in trackers]


@router.post("/trackers", response_model=TrackerRead, status_code=status.HTTP_201_CREATED)
async def create_tracker(
    payload: TrackerCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> TrackerRead:
    async with session_factory() as session:
        try:
            tracker = await tracker_service.create_tracker(
                session,
                user_id=telegram_user.user_id,
                query=payload.query,
                strict_mode=payload.strict_mode,
                interval_min=payload.interval_min,
                min_discount_percent=payload.min_discount_percent,
                max_price_byn=payload.max_price_byn,
                seller_type=payload.seller_type,
                condition=payload.condition,
                region_name=payload.region_name,
                config_keyword=payload.config_keyword,
                exclude_duplicates=payload.exclude_duplicates,
                first_name=telegram_user.first_name,
                username=init_data_username(telegram_user),
            )
        except TrackerValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.message,
            ) from exc
        except (DuplicateTrackerError, TrackerQuotaExceededError) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
        await session.commit()
        await session.refresh(tracker)
        return _serialize_tracker(tracker)


@router.delete("/trackers/{tracker_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracker(
    tracker_id: int = Path(ge=1, le=MAX_DB_ID),
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        tracker = await tracker_service.deactivate_tracker(
            session,
            user_id=telegram_user.user_id,
            tracker_id=tracker_id,
        )
        if tracker is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Трекер не найден")
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
