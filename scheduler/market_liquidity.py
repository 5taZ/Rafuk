from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.models import MarketLiquidityObservation
from api.services.kufar_client import USER_AGENT
from api.services.market_liquidity import CHECK_GRACE, OBSERVATION_AGE

_PROBE_LIMIT = 16
_RECHECK_DELAY = timedelta(hours=1)


async def check_market_liquidity(
    session_factory: async_sessionmaker[AsyncSession], *, settings: Settings,
) -> None:
    async with httpx.AsyncClient(
        timeout=settings.kufar_timeout, headers={"User-Agent": USER_AGENT},
    ) as client:
        await check_due_observations(
            session_factory, client=client, now=datetime.now(UTC),
            request_delay=settings.kufar_request_delay,
        )


async def check_due_observations(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    client: httpx.AsyncClient,
    now: datetime,
    request_delay: float,
) -> None:
    observation = MarketLiquidityObservation
    due_at = now - OBSERVATION_AGE
    async with session_factory() as session:
        due = (await session.execute(
            select(observation.ad_id, observation.last_checked_at, observation.missing_since)
            .where(
                observation.available.is_(None),
                observation.first_seen_at > due_at - CHECK_GRACE,
                observation.first_seen_at <= due_at,
                or_(
                    observation.last_checked_at.is_(None),
                    observation.last_checked_at <= now - _RECHECK_DELAY,
                ),
            )
            .order_by(observation.first_seen_at, observation.ad_id)
            .limit(_PROBE_LIMIT)
        )).all()

    results = []
    for ad_id, checked_at, missing_since in due:
        if results:
            await asyncio.sleep(request_delay)
        try:
            response = await client.head(
                f"https://www.kufar.by/item/{ad_id}", follow_redirects=False,
            )
        except httpx.RequestError:
            break
        code = response.status_code
        if code in {401, 403, 408, 429} or code >= 500:
            break
        available = None
        if code == 200:
            available = True
            missing_since = None
        elif code in {404, 410}:
            # A listing can briefly disappear and return. Require a second probe an hour later.
            available = False if missing_since is not None else None
            missing_since = missing_since or now
        results.append((ad_id, checked_at, missing_since, available))

    if results:
        async with session_factory() as session:
            for ad_id, checked_at, missing_since, available in results:
                await session.execute(
                    update(observation).where(
                        observation.ad_id == ad_id,
                        observation.available.is_(None),
                        observation.last_checked_at.is_not_distinct_from(checked_at),
                    ).values(last_checked_at=now, missing_since=missing_since, available=available)
                )
            await session.commit()
