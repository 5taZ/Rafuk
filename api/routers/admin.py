from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from api.dependencies import (
    get_cache,
    get_session_factory_dependency,
    require_admin_user,
)
from api.middleware.telegram_auth import TelegramInitData
from api.models import AccountStatus, AdminAuditLog, User
from api.schemas import (
    AccountStatusRead,
    AdminStatusLimitUpdate,
    AdminUserRead,
    AdminUserStatusUpdate,
    ProfileLimitsRead,
    ProfileStatusRead,
)
from api.services.account_status import (
    BARE_SEARCH_STATUS_CODE,
    QUOTA_BUCKET_AI,
    QUOTA_BUCKET_ASSISTANT,
    effective_status_metadata,
    get_effective_status,
    get_or_create_profile_user,
    quota_snapshot,
)
from api.services.cache import CacheBackend
from api.services.client_ip import get_client_ip

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserRead])
async def list_admin_users(
    request: Request,
    # G-02 / SEC-NEW-6: cap admin search inputs and offset to prevent
    # unbounded LIKE patterns / pagination drift.
    query: str = Query(default="", max_length=128),
    status: str = Query(default="", max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    _admin: TelegramInitData = Depends(require_admin_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    cache: CacheBackend = Depends(get_cache),
) -> list[AdminUserRead]:
    del request, _admin
    async with session_factory() as session:
        stmt = (
            select(User)
            .options(joinedload(User.account_status))  # PERF-NEW-3: eager-load AccountStatus
            .order_by(User.created_at.desc(), User.id.desc())
            .limit(limit)
            .offset(offset)
        )
        needle = query.strip().lstrip("@")
        if needle:
            pattern = f"%{needle.lower()}%"
            conditions = [
                func.lower(User.username).like(pattern),
                func.lower(User.first_name).like(pattern),
            ]
            if needle.isdigit():
                conditions.append(cast(User.telegram_user_id, String).like(f"%{needle}%"))
            stmt = stmt.where(or_(*conditions))
        status_code = status.strip()
        if status_code:
            now = datetime.now(UTC)
            if status_code == BARE_SEARCH_STATUS_CODE:
                stmt = stmt.where(
                    or_(
                        User.account_status_code == BARE_SEARCH_STATUS_CODE,
                        User.status_expires_at <= now,
                    )
                )
            else:
                stmt = stmt.where(
                    User.account_status_code == status_code,
                    or_(User.status_expires_at.is_(None), User.status_expires_at > now),
                )
        users = (await session.execute(stmt)).scalars().unique().all()
        # PERF-NEW-3: parallelize quota_snapshot fan-out so /admin/users is
        # O(1) DB roundtrip + concurrent Redis GETs instead of N+1 sequential.
        return list(await asyncio.gather(*(
            _admin_user_read(session, cache, user) for user in users
        )))


@router.patch("/users/{telegram_user_id}/status", response_model=AdminUserRead)
async def update_admin_user_status(
    telegram_user_id: int,
    payload: AdminUserStatusUpdate,
    request: Request,
    admin_user: TelegramInitData = Depends(require_admin_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    cache: CacheBackend = Depends(get_cache),
) -> AdminUserRead:
    async with session_factory() as session:
        actor = await get_or_create_profile_user(session, admin_user)
        target = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        status = await session.get(AccountStatus, payload.status_code)
        if status is None:
            raise HTTPException(status_code=404, detail="Account status not found")

        old = _user_status_payload(target)
        now = datetime.now(UTC)
        target.account_status_code = status.code
        target.status_granted_at = now
        target.status_expires_at = _normalize_admin_datetime(payload.expires_at)
        target.status_note = payload.note
        new = _user_status_payload(target)
        session.add(AdminAuditLog(
            actor_user_id=actor.id,
            target_user_id=target.id,
            action="user_status_updated",
            payload={
                "target_telegram_user_id": target.telegram_user_id,
                "old": old,
                "new": new,
            },
            ip_address=get_client_ip(request),
        ))
        await session.commit()
        return await _admin_user_read(session, cache, target)


@router.get("/statuses", response_model=list[AccountStatusRead])
async def list_admin_statuses(
    _admin: TelegramInitData = Depends(require_admin_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[AccountStatus]:
    del _admin
    async with session_factory() as session:
        return (
            await session.execute(select(AccountStatus).order_by(AccountStatus.sort_order))
        ).scalars().all()


@router.patch("/statuses/{code}", response_model=AccountStatusRead)
async def update_admin_status_limits(
    code: str,
    payload: AdminStatusLimitUpdate,
    request: Request,
    admin_user: TelegramInitData = Depends(require_admin_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> AccountStatus:
    async with session_factory() as session:
        actor = await get_or_create_profile_user(session, admin_user)
        status = await session.get(AccountStatus, code)
        if status is None:
            raise HTTPException(status_code=404, detail="Account status not found")
        old = _status_limit_payload(status)
        status.ai_daily_limit = payload.ai_daily_limit
        status.assistant_daily_limit = payload.assistant_daily_limit
        new = _status_limit_payload(status)
        session.add(AdminAuditLog(
            actor_user_id=actor.id,
            target_user_id=None,
            action="status_limits_updated",
            payload={
                "status_code": status.code,
                "old": old,
                "new": new,
            },
            ip_address=get_client_ip(request),
        ))
        await session.commit()
        await session.refresh(status)
        return status


async def _admin_user_read(
    session: AsyncSession,
    cache: CacheBackend,
    user: User,
    *,
    status: AccountStatus | None = None,
) -> AdminUserRead:
    if status is not None:
        current_status = status
    elif user.account_status is not None:
        # PERF-NEW-3: use eager-loaded relationship; still check expiry.
        expires_at = user.status_expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                current_status = await session.get(AccountStatus, BARE_SEARCH_STATUS_CODE)
            else:
                current_status = user.account_status
        else:
            current_status = user.account_status
    else:
        current_status = await get_effective_status(session, user)
    granted_at, expires_at = effective_status_metadata(user, current_status)
    return AdminUserRead(
        telegram_user_id=user.telegram_user_id,
        first_name=user.first_name,
        username=user.username,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        status=ProfileStatusRead(
            code=current_status.code,
            display_name=current_status.display_name,
            tagline=current_status.tagline,
            accent=current_status.accent,
            granted_at=granted_at,
            expires_at=expires_at,
        ),
        limits=ProfileLimitsRead(
            ai=await quota_snapshot(
                cache,
                bucket=QUOTA_BUCKET_AI,
                telegram_user_id=user.telegram_user_id,
                limit=current_status.ai_daily_limit,
            ),
            assistant=await quota_snapshot(
                cache,
                bucket=QUOTA_BUCKET_ASSISTANT,
                telegram_user_id=user.telegram_user_id,
                limit=current_status.assistant_daily_limit,
            ),
        ),
        status_note=user.status_note,
    )


def _normalize_admin_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _user_status_payload(user: User) -> dict[str, object]:
    return {
        "status_code": user.account_status_code,
        "granted_at": _iso_or_none(user.status_granted_at),
        "expires_at": _iso_or_none(user.status_expires_at),
        "note": user.status_note,
    }


def _status_limit_payload(status: AccountStatus) -> dict[str, int]:
    return {
        "ai_daily_limit": int(status.ai_daily_limit),
        "assistant_daily_limit": int(status.assistant_daily_limit),
    }


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
