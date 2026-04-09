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
        "contact is updated (upsert by phone).  Returns **201 Created** for a "
        "brand-new contact or **200 OK** when an existing record was updated."
    ),
    responses={
        201: {"description": "New contact created successfully"},
        200: {"description": "Existing contact updated (upsert)"},
    },
)
@limiter.limit("20/minute")
async def create_contact(
    request: Request,
    payload: ContactCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
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
            return Response(
                content=ContactRead.model_validate(existing).model_dump_json(),
                status_code=status.HTTP_200_OK,
                media_type="application/json",
            )

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
            await session.flush()

        await session.refresh(contact)
        return Response(
            content=ContactRead.model_validate(contact).model_dump_json(),
            status_code=status.HTTP_201_CREATED,
            media_type="application/json",
        )


@router.get(
    "",
    response_model=list[ContactRead],
    summary="List all contacts for the authenticated user",
    description=(
        "Returns every contact owned by the current Telegram user, "
        "ordered by most recently saved first."
    ),
)
async def list_contacts(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[ContactRead]:
    """Return all contacts scoped to the authenticated user."""
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
    description=(
        "Permanently delete a contact by ID. "
        "Returns 404 if the contact does not belong to the user."
    ),
    responses={
        204: {"description": "Contact deleted successfully"},
        404: {"description": "Contact not found"},
    },
)
@limiter.limit("20/minute")
async def delete_contact(
    request: Request,
    contact_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    """Delete a contact owned by the authenticated user.

    Raises **404 Not Found** if the contact does not exist or does not belong
    to the current user.
    """
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
