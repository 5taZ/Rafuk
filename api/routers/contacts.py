from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import Contact
from api.schemas import ContactCreate, ContactRead
from api.services.workflow_store import ensure_user, resolve_user_id

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post(
    "",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create or update a contact",
    description=(
        "Create a new contact for the authenticated user. "
        "If a contact with the same phone number already exists, the existing "
        "contact is updated (upsert by phone)."
    ),
)
@limiter.limit("20/minute")
async def create_contact(
    request: Request,
    payload: ContactCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> ContactRead:
    """Create a new contact (upsert by phone or name)."""
    if not payload.phone and not payload.seller_name:
        raise HTTPException(status_code=400, detail="Phone or name is required")

    async with session_factory() as session:
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        # Try to find an existing contact by phone (if provided) or by name
        existing = None
        if payload.phone:
            existing = await session.scalar(
                select(Contact).where(
                    Contact.user_id == user_id,
                    Contact.phone == payload.phone,
                )
            )
        if existing is None and payload.seller_name:
            existing = await session.scalar(
                select(Contact).where(
                    Contact.user_id == user_id,
                    Contact.seller_name == payload.seller_name,
                )
            )

        if existing is not None:
            if payload.seller_name is not None:
                existing.seller_name = payload.seller_name
            if payload.kufar_profile is not None:
                existing.kufar_profile = payload.kufar_profile
            if payload.phone is not None:
                existing.phone = payload.phone
            await session.commit()
            await session.refresh(existing)
            return ContactRead.model_validate(existing)

        contact = Contact(
            user_id=user_id,
            phone=payload.phone,
            seller_name=payload.seller_name,
            kufar_profile=payload.kufar_profile,
        )
        session.add(contact)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Contact already exists",
            ) from None
        await session.refresh(contact)
        return ContactRead.model_validate(contact)


@router.get(
    "",
    response_model=list[ContactRead],
    summary="List all contacts for the authenticated user",
)
async def list_contacts(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[ContactRead]:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            return []
        result = await session.execute(
            select(Contact)
            .where(Contact.user_id == user_id)
            .order_by(Contact.saved_at.desc(), Contact.id.desc())
        )
        return [ContactRead.model_validate(item) for item in result.scalars()]


@router.delete(
    "/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a contact",
)
@limiter.limit("20/minute")
async def delete_contact(
    request: Request,
    contact_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found",
            )
        contact = await session.scalar(
            select(Contact).where(
                Contact.id == contact_id,
                Contact.user_id == user_id,
            )
        )
        if contact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found",
            )
        await session.delete(contact)
        await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
