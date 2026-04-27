from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import DealExpense, LeadItem
from api.schemas import DealExpenseCreate, DealExpenseRead, DealExpenseUpdate
from api.services.workflow_store import ensure_user, resolve_user_id

router = APIRouter(tags=["expenses"])


@router.post(
    "/leads/{lead_id}/expenses",
    response_model=DealExpenseRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def create_expense(
    request: Request,
    lead_id: int,
    payload: DealExpenseCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> DealExpenseRead:
    """Add an expense to a lead (delivery, repair, etc.)."""
    async with session_factory() as session:
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        # Verify lead exists and belongs to user
        lead = await session.get(LeadItem, lead_id)
        if lead is None or lead.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

        expense = DealExpense(
            lead_id=lead_id,
            user_id=user_id,
            expense_type=payload.expense_type.value,
            amount_byn=payload.amount_byn,
            notes=payload.notes,
            expense_date=payload.expense_date or datetime.now(UTC),
        )
        session.add(expense)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Failed to create expense",
            ) from exc
        return DealExpenseRead.model_validate(expense)


@router.get("/leads/{lead_id}/expenses", response_model=list[DealExpenseRead])
@limiter.limit("30/minute")
async def get_expenses(
    request: Request,
    lead_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[DealExpenseRead]:
    """List all expenses for a lead."""
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        # Verify lead exists and belongs to user
        lead = await session.get(LeadItem, lead_id)
        if lead is None or lead.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

        result = await session.execute(
            select(DealExpense)
            .where(DealExpense.lead_id == lead_id, DealExpense.user_id == user_id)
            .order_by(DealExpense.expense_date.desc(), DealExpense.id.desc())
        )
        return [DealExpenseRead.model_validate(e) for e in result.scalars()]


@router.patch("/leads/{lead_id}/expenses/{expense_id}", response_model=DealExpenseRead)
@limiter.limit("20/minute")
async def update_expense(
    request: Request,
    lead_id: int,
    expense_id: int,
    payload: DealExpenseUpdate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> DealExpenseRead:
    """Update an expense."""
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
        expense = await session.get(DealExpense, expense_id)
        if expense is None or expense.user_id != user_id or expense.lead_id != lead_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")

        if payload.expense_type is not None:
            expense.expense_type = payload.expense_type.value
        if payload.amount_byn is not None:
            expense.amount_byn = payload.amount_byn
        if payload.notes is not None:
            expense.notes = payload.notes
        if payload.expense_date is not None:
            expense.expense_date = payload.expense_date

        await session.commit()
        await session.refresh(expense)
        return DealExpenseRead.model_validate(expense)


@router.delete("/leads/{lead_id}/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def delete_expense(
    request: Request,
    lead_id: int,
    expense_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    """Delete an expense."""
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
        expense = await session.get(DealExpense, expense_id)
        if expense is None or expense.user_id != user_id or expense.lead_id != lead_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")

        await session.delete(expense)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
