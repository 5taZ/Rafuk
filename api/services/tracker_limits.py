from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AccountStatus, Tracker, User
from api.services.account_status import BARE_SEARCH_STATUS_CODE, get_effective_status


async def get_tracker_capacity(
    session: AsyncSession, user_id: int, *, exclude_tracker_id: int | None = None,
) -> tuple[int, int]:
    # BE-M16: serialize count + create/resume on the same user row so
    # concurrent requests cannot both claim the last available slot.
    user = (await session.execute(
        select(User).where(User.id == user_id).with_for_update()
    )).scalar_one()
    status = await get_effective_status(session, user)
    stmt = select(func.count(Tracker.id)).where(
        Tracker.user_id == user_id,
        Tracker.active.is_(True),
        Tracker.paused.is_(False),
    )
    if exclude_tracker_id is not None:
        stmt = stmt.where(Tracker.id != exclude_tracker_id)
    used = await session.scalar(stmt)
    return int(used or 0), int(status.max_trackers)


async def require_tracker_capacity(
    session: AsyncSession, user_id: int, *, exclude_tracker_id: int | None = None,
) -> None:
    used, limit = await get_tracker_capacity(
        session, user_id, exclude_tracker_id=exclude_tracker_id,
    )
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Лимит одновременно работающих автотрекеров на вашем тарифе: {limit}. "
            "Приостановите или удалите один из трекеров.",
        )


async def pause_excess_trackers(session: AsyncSession, *, user_id: int | None = None) -> int:
    """Keep the oldest running trackers when a role limit or subscription changes."""
    now = datetime.now(UTC)
    ranked = select(
        Tracker.id,
        AccountStatus.max_trackers,
        func.row_number().over(
            partition_by=Tracker.user_id, order_by=(Tracker.created_at, Tracker.id),
        ).label("position"),
    ).join(User, User.id == Tracker.user_id).join(
        AccountStatus,
        AccountStatus.code == case(
            (User.status_expires_at <= now, BARE_SEARCH_STATUS_CODE),
            else_=User.account_status_code,
        ),
    ).where(Tracker.active.is_(True), Tracker.paused.is_(False))
    if user_id is not None:
        ranked = ranked.where(Tracker.user_id == user_id)
    running = ranked.subquery()
    result = await session.execute(update(Tracker).where(
        Tracker.active.is_(True),
        Tracker.paused.is_(False),
        Tracker.id.in_(select(running.c.id).where(
            running.c.position > running.c.max_trackers,
        )),
    ).values(
        paused=True, paused_at=now, pause_reason="plan_limit", updated_at=now,
    ).execution_options(synchronize_session=False))
    return result.rowcount or 0
