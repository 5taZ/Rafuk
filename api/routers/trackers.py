from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import get_settings
from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import Tracker, TrackerEvent, User
from api.schemas import TrackerCreate, TrackerEventRead, TrackerRead, TrackerUpdate
from api.services.reseller_tools import default_config_keyword
from api.services.workflow_store import ensure_user, resolve_user_id

router = APIRouter(tags=["trackers"])


@router.get("/tracker-events", response_model=list[TrackerEventRead])
@limiter.limit("30/minute")
async def get_tracker_events(
    request: Request,
    limit: int = 20,
    tracker_id: int | None = None,
    event_type: str | None = None,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[TrackerEventRead]:
    bounded_limit = max(1, min(limit, 50))
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return []
        stmt = (
            select(TrackerEvent)
            .where(TrackerEvent.user_id == user_id)
            .order_by(TrackerEvent.created_at.desc(), TrackerEvent.id.desc())
            .limit(bounded_limit)
        )
        if tracker_id is not None:
            stmt = stmt.where(TrackerEvent.tracker_id == tracker_id)
        if event_type is not None:
            stmt = stmt.where(TrackerEvent.event_type == event_type)
        result = await session.execute(stmt)
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
            await session.execute(delete(TrackerEvent).where(TrackerEvent.user_id == user_id))
            await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/trackers", response_model=list[TrackerRead])
@limiter.limit("30/minute")
async def get_trackers(
    request: Request,
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

        if not trackers:
            return []

        # Single GROUP BY query for all tracker event stats (fixes N+1)
        tracker_ids = [t.id for t in trackers]
        stats_rows = await session.execute(
            select(
                TrackerEvent.tracker_id,
                func.count(TrackerEvent.id).label("total_events"),
                func.count(TrackerEvent.id)
                .filter(TrackerEvent.event_type == "new_listing")
                .label("new_listings"),
                func.count(TrackerEvent.id)
                .filter(TrackerEvent.event_type == "price_drop")
                .label("price_drops"),
                func.max(TrackerEvent.created_at).label("last_event_at"),
            )
            .where(TrackerEvent.tracker_id.in_(tracker_ids))
            .group_by(TrackerEvent.tracker_id)
        )
        stats_by_tracker: dict[int, object] = {row.tracker_id: row for row in stats_rows}

        enriched_trackers = []
        now = datetime.now(UTC)
        for tracker in trackers:
            stats = stats_by_tracker.get(tracker.id)
            total_events = stats.total_events if stats else 0
            new_listings = stats.new_listings if stats else 0
            price_drops = stats.price_drops if stats else 0
            last_event_at = stats.last_event_at if stats else None

            days_active = 1
            if tracker.created_at:
                created = (
                    tracker.created_at.replace(tzinfo=UTC)
                    if tracker.created_at.tzinfo is None
                    else tracker.created_at
                )
                days_active = max(1, (now - created).total_seconds() / 86400)

            avg_events_per_day = round(total_events / days_active, 2) if total_events > 0 else 0.0

            tracker_dict = TrackerRead.model_validate(tracker)
            tracker_dict = tracker_dict.model_copy(
                update={
                    "event_count": total_events,
                    "new_listings_count": new_listings,
                    "price_drops_count": price_drops,
                    "last_event_at": last_event_at,
                    "avg_events_per_day": avg_events_per_day,
                }
            )
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
        # Per-user tracker limit to prevent amplification attacks
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )

        # BE-M16: serialise concurrent tracker-create for the same user
        # by taking a row-level FOR UPDATE lock on the User row before
        # we count + insert. Without it, two simultaneous requests could
        # both observe ``existing_count == max - 1``, both pass the
        # check, and both insert — leaving the user one tracker over
        # the configured limit. ``with_for_update`` is a no-op on
        # SQLite (no row-level locking) so the test-suite still works;
        # on Postgres the second concurrent request blocks here until
        # the first commits, then sees the updated count.
        await session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )

        max_trackers_per_user = get_settings().max_trackers_per_user
        existing_count = await session.scalar(
            select(func.count(Tracker.id)).where(
                Tracker.user_id == user_id,
                Tracker.active.is_(True),
            )
        )
        if (existing_count or 0) >= max_trackers_per_user:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Maximum {max_trackers_per_user} trackers per user",
            )

        tracker = Tracker(
            user_id=user_id,
            query=query,
            strict_mode=payload.strict_mode,
            interval_min=payload.interval_min,
            category_id=payload.category_id,
            category_label=payload.category_label if payload.category_id is not None else None,
            min_discount_percent=payload.min_discount_percent,
            max_price_byn=payload.max_price_byn,
            seller_type=payload.seller_type,
            condition=payload.condition,
            region_name=payload.region_name,
            config_keyword=payload.config_keyword or default_config_keyword(query),
            alert_price_threshold=payload.alert_price_threshold,
            alert_discount_percent=payload.alert_discount_percent,
        )
        session.add(tracker)
        # BE-M16: previously this lived inside ``session.begin_nested()``
        # and called ``session.rollback()`` from within the savepoint on
        # IntegrityError, which actually rolled back the *outer*
        # transaction (the savepoint context manager auto-rolls back on
        # exception, so the manual rollback was both redundant and
        # incorrect). Plain ``session.commit()`` here under the FOR
        # UPDATE lock is enough; any IntegrityError from the DB will
        # leave the session in a rolled-back state and we surface it
        # as a 409.
        try:
            await session.commit()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A tracker with this configuration already exists",
            ) from exc
        await session.refresh(tracker)
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
        if "category_id" in update_data and update_data["category_id"] is None:
            update_data["category_label"] = None
        nullable_fields = {
            "category_id",
            "category_label",
            "min_discount_percent",
            "max_price_byn",
            "seller_type",
            "condition",
            "region_name",
            "config_keyword",
            "alert_price_threshold",
            "alert_discount_percent",
        }
        for field, value in update_data.items():
            if value is None and field not in nullable_fields:
                continue
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
