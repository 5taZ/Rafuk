from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import Tracker, TrackerEvent
from api.schemas import TrackerCreate, TrackerEventRead, TrackerRead, TrackerUpdate
from api.services.reseller_tools import default_config_keyword
from api.services.workflow_store import ensure_user, resolve_user_id

router = APIRouter(tags=["trackers"])


@router.get("/tracker-events", response_model=list[TrackerEventRead])
async def get_tracker_events(
    limit: int = 20,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[TrackerEventRead]:
    bounded_limit = max(1, min(limit, 50))
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return []
        result = await session.execute(
            select(TrackerEvent)
            .where(TrackerEvent.user_id == user_id)
            .order_by(TrackerEvent.created_at.desc(), TrackerEvent.id.desc())
            .limit(bounded_limit)
        )
        return list(result.scalars())


@router.delete("/tracker-events", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def clear_tracker_events(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is not None:
            await session.execute(
                delete(TrackerEvent).where(TrackerEvent.user_id == user_id)
            )
            await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/trackers", response_model=list[TrackerRead])
async def get_trackers(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[TrackerRead]:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return []
        result = await session.execute(
            select(Tracker)
            .where(Tracker.user_id == user_id, Tracker.active.is_(True))
            .order_by(Tracker.created_at.desc(), Tracker.id.desc())
        )
        trackers = list(result.scalars())
        
        # Enrich trackers with event statistics
        enriched_trackers = []
        for tracker in trackers:
            # Get event counts and last event time
            stats_result = await session.execute(
                select(
                    func.count(TrackerEvent.id).label('total_events'),
                    func.count(TrackerEvent.id).filter(TrackerEvent.event_type == 'new_listing').label('new_listings'),
                    func.count(TrackerEvent.id).filter(TrackerEvent.event_type == 'price_drop').label('price_drops'),
                    func.max(TrackerEvent.created_at).label('last_event_at')
                ).where(TrackerEvent.tracker_id == tracker.id)
            )
            stats = stats_result.one()
            
            # Calculate average events per day
            now = datetime.now(UTC)
            days_active = 1
            if tracker.created_at:
                delta = now - tracker.created_at.replace(tzinfo=UTC) if tracker.created_at.tzinfo is None else now - tracker.created_at
                days_active = max(1, delta.total_seconds() / 86400)
            
            avg_events_per_day = round(stats.total_events / days_active, 2) if stats.total_events > 0 else 0.0
            
            # Create enriched response
            tracker_dict = TrackerRead.model_validate(tracker)
            tracker_dict.event_count = stats.total_events
            tracker_dict.new_listings_count = stats.new_listings
            tracker_dict.price_drops_count = stats.price_drops
            tracker_dict.last_event_at = stats.last_event_at
            tracker_dict.avg_events_per_day = avg_events_per_day
            enriched_trackers.append(tracker_dict)
        
        return enriched_trackers


@router.post("/trackers", response_model=TrackerRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def create_tracker(
    request: Request,
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
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        tracker = Tracker(
            user_id=user_id,
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
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A tracker with this configuration already exists",
            ) from exc
        return TrackerRead.model_validate(tracker)


@router.delete("/trackers/{tracker_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def delete_tracker(
    request: Request,
    tracker_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        result = await session.execute(
            select(Tracker).where(
                Tracker.id == tracker_id,
                Tracker.user_id == user_id,
                Tracker.active.is_(True),
            )
        )
        tracker = result.scalar_one_or_none()
        if tracker is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracker not found")
        tracker.active = False
        tracker.deleted_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/trackers/{tracker_id}", response_model=TrackerRead)
@limiter.limit("20/minute")
async def update_tracker(
    request: Request,
    tracker_id: int,
    payload: TrackerUpdate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> TrackerRead:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        result = await session.execute(
            select(Tracker).where(
                Tracker.id == tracker_id,
                Tracker.user_id == user_id,
                Tracker.active.is_(True),
            )
        )
        tracker = result.scalar_one_or_none()
        if tracker is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracker not found")
        
        # Update only provided fields
        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(tracker, field, value)
        
        await session.commit()
        await session.refresh(tracker)
        return TrackerRead.model_validate(tracker)


@router.post("/trackers/{tracker_id}/pause", response_model=TrackerRead)
@limiter.limit("10/minute")
async def pause_tracker(
    request: Request,
    tracker_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> TrackerRead:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        result = await session.execute(
            select(Tracker).where(
                Tracker.id == tracker_id,
                Tracker.user_id == user_id,
                Tracker.active.is_(True),
            )
        )
        tracker = result.scalar_one_or_none()
        if tracker is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracker not found")
        
        tracker.paused = True
        tracker.paused_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(tracker)
        return TrackerRead.model_validate(tracker)


@router.post("/trackers/{tracker_id}/resume", response_model=TrackerRead)
@limiter.limit("10/minute")
async def resume_tracker(
    request: Request,
    tracker_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> TrackerRead:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        result = await session.execute(
            select(Tracker).where(
                Tracker.id == tracker_id,
                Tracker.user_id == user_id,
                Tracker.active.is_(True),
            )
        )
        tracker = result.scalar_one_or_none()
        if tracker is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracker not found")
        
        tracker.paused = False
        tracker.paused_at = None
        tracker.pause_reason = None
        await session.commit()
        await session.refresh(tracker)
        return TrackerRead.model_validate(tracker)
