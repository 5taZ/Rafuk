from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import LeadItem, LeadReminder
from api.schemas import ReminderCreate, ReminderRead
from api.services.workflow_store import ensure_user, resolve_user_id

router = APIRouter(tags=["reminders"])


@router.post(
    "/leads/{lead_id}/reminders",
    response_model=ReminderRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def create_reminder(
    request: Request,
    lead_id: int,
    payload: ReminderCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency,
    ),
) -> ReminderRead:
    """Add a reminder to a lead."""
    async with session_factory() as session:
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        # Verify lead exists and belongs to user
        lead = await session.get(LeadItem, lead_id)
        if lead is None or lead.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found",
            )

        reminder = LeadReminder(
            lead_id=lead_id,
            user_id=user_id,
            remind_at=payload.remind_at,
            message=payload.message,
        )
        session.add(reminder)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Failed to create reminder",
            ) from exc
        await session.refresh(reminder)
        return ReminderRead.model_validate(reminder)


@router.get(
    "/leads/{lead_id}/reminders",
    response_model=list[ReminderRead],
)
@limiter.limit("30/minute")
async def get_reminders(
    request: Request,
    lead_id: int,
    # PR-09: cap and paginate. Previously this returned the full
    # reminder list unbounded; a power user with thousands of
    # reminders on a single lead would burn a multi-MB response per
    # call. Defaults mirror the expenses endpoint (BE-M2 pattern).
    limit: int = Query(default=100, ge=1, le=500, description="Max reminders to return"),
    # G-02: cap offset to bound pagination drift.
    offset: int = Query(default=0, ge=0, le=10_000, description="Number of reminders to skip"),
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency,
    ),
) -> list[ReminderRead]:
    """List reminders for a lead (paginated, soonest first)."""
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found",
            )
        # Verify lead exists and belongs to user
        lead = await session.get(LeadItem, lead_id)
        if lead is None or lead.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found",
            )

        result = await session.execute(
            select(LeadReminder)
            .where(LeadReminder.lead_id == lead_id, LeadReminder.user_id == user_id)
            .order_by(LeadReminder.remind_at.asc(), LeadReminder.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return [ReminderRead.model_validate(r) for r in result.scalars()]


@router.delete(
    "/leads/{lead_id}/reminders/{reminder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("20/minute")
async def delete_reminder(
    request: Request,
    lead_id: int,
    reminder_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory_dependency,
    ),
) -> Response:
    """Delete a reminder."""
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reminder not found",
            )
        reminder = await session.get(LeadReminder, reminder_id)
        if (
            reminder is None
            or reminder.user_id != user_id
            or reminder.lead_id != lead_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reminder not found",
            )

        await session.delete(reminder)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
