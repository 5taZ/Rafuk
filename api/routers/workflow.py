from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.middleware.telegram_auth import TelegramInitData
from api.models import LeadItem, WatchlistItem
from api.schemas import (
    LeadCreate,
    LeadRead,
    LeadUpdate,
    WatchlistCreate,
    WatchlistRead,
    WatchlistRefreshResponse,
    WatchlistUpdate,
)
from api.services.aggregator import normalize_price_byn
from api.services.kufar_client import KufarClient
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.services.workflow_store import upsert_lead, upsert_watchlist

router = APIRouter(tags=["workflow"])


def _serialize_watchlist(item: WatchlistItem) -> WatchlistRead:
    current = item.current_price_byn
    initial = item.initial_price_byn
    delta_byn = None
    delta_percent = None
    if current is not None and initial is not None:
        delta_byn = round(current - initial, 2)
        if initial:
            delta_percent = round((delta_byn / initial) * 100.0, 2)
    return WatchlistRead(
        id=item.id,
        user_id=item.user_id,
        ad_id=item.ad_id,
        query=item.query,
        title=item.title,
        link=item.link,
        initial_price_byn=item.initial_price_byn,
        current_price_byn=item.current_price_byn,
        price_delta_byn=delta_byn,
        price_delta_percent=delta_percent,
        workflow_status=item.workflow_status,
        market_status=item.market_status,
        duplicate_count=item.duplicate_count,
        notes=item.notes,
        created_at=item.created_at,
        last_seen_at=item.last_seen_at,
        updated_at=item.updated_at,
    )


@router.get("/leads", response_model=list[LeadRead])
async def get_leads(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[LeadRead]:
    async with session_factory() as session:
        result = await session.execute(
            select(LeadItem)
            .where(LeadItem.user_id == telegram_user.user_id)
            .order_by(LeadItem.updated_at.desc(), LeadItem.id.desc())
        )
        return [LeadRead.model_validate(item) for item in result.scalars()]


@router.post("/leads", response_model=LeadRead, status_code=status.HTTP_201_CREATED)
async def create_lead(
    payload: LeadCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> LeadRead:
    async with session_factory() as session:
        lead = await upsert_lead(
            session,
            user_id=telegram_user.user_id,
            ad_id=payload.ad_id,
            query=payload.query,
            title=payload.title,
            link=payload.link,
            price_byn=payload.price_byn,
            target_resale_byn=payload.target_resale_byn,
            status=payload.status,
            source=payload.source,
            notes=payload.notes,
        )
        await session.commit()
        await session.refresh(lead)
        return LeadRead.model_validate(lead)


@router.patch("/leads/{lead_id}", response_model=LeadRead)
async def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> LeadRead:
    async with session_factory() as session:
        lead = await session.scalar(
            select(LeadItem).where(LeadItem.id == lead_id, LeadItem.user_id == telegram_user.user_id)
        )
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        if "status" in payload.model_fields_set:
            lead.status = payload.status
        if "target_resale_byn" in payload.model_fields_set:
            lead.target_resale_byn = payload.target_resale_byn
        if "notes" in payload.model_fields_set:
            lead.notes = payload.notes
        await session.commit()
        await session.refresh(lead)
        return LeadRead.model_validate(lead)


@router.get("/watchlist", response_model=list[WatchlistRead])
async def get_watchlist(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[WatchlistRead]:
    async with session_factory() as session:
        result = await session.execute(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == telegram_user.user_id)
            .order_by(WatchlistItem.updated_at.desc(), WatchlistItem.id.desc())
        )
        return [_serialize_watchlist(item) for item in result.scalars()]


@router.post("/watchlist", response_model=WatchlistRead, status_code=status.HTTP_201_CREATED)
async def create_watchlist_item(
    payload: WatchlistCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> WatchlistRead:
    async with session_factory() as session:
        item = await upsert_watchlist(
            session,
            user_id=telegram_user.user_id,
            ad_id=payload.ad_id,
            query=payload.query,
            title=payload.title,
            link=payload.link,
            price_byn=payload.price_byn,
            notes=payload.notes,
        )
        await session.commit()
        await session.refresh(item)
        return _serialize_watchlist(item)


@router.patch("/watchlist/{watchlist_id}", response_model=WatchlistRead)
async def update_watchlist_item(
    watchlist_id: int,
    payload: WatchlistUpdate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> WatchlistRead:
    async with session_factory() as session:
        item = await session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.id == watchlist_id,
                WatchlistItem.user_id == telegram_user.user_id,
            )
        )
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist item not found")
        if "workflow_status" in payload.model_fields_set:
            item.workflow_status = payload.workflow_status
        if "notes" in payload.model_fields_set:
            item.notes = payload.notes
        await session.commit()
        await session.refresh(item)
        return _serialize_watchlist(item)


@router.delete("/watchlist/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    watchlist_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        item = await session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.id == watchlist_id,
                WatchlistItem.user_id == telegram_user.user_id,
            )
        )
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist item not found")
        await session.delete(item)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/watchlist/refresh", response_model=WatchlistRefreshResponse)
async def refresh_watchlist(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    settings: Settings = Depends(get_settings_dependency),
) -> WatchlistRefreshResponse:
    async with session_factory() as session:
        result = await session.execute(
            select(WatchlistItem).where(WatchlistItem.user_id == telegram_user.user_id)
        )
        items = list(result.scalars())
        if not items:
            return WatchlistRefreshResponse(updated=0, missing=0, price_drops=0)

        grouped: dict[str, list[WatchlistItem]] = defaultdict(list)
        for item in items:
            grouped[item.query].append(item)

        updated = 0
        missing = 0
        price_drops = 0
        for query, query_items in grouped.items():
            dataset = await load_query_dataset(
                query=query,
                currency="BYN",
                strict_search=False,
                settings=settings,
                client_factory=KufarClient,
            )
            ads_by_id = {
                int(ad.get("ad_id", 0)): ad
                for ad in dataset.ads
                if int(ad.get("ad_id", 0)) > 0
            }
            duplicate_index = duplicate_counts(dataset.ads)

            for item in query_items:
                ad = ads_by_id.get(item.ad_id)
                if ad is None:
                    item.market_status = "missing"
                    missing += 1
                    continue
                price = normalize_price_byn(ad.get("price_byn"))
                previous = item.current_price_byn
                item.title = str(ad.get("subject", item.title))
                item.link = str(ad.get("ad_link", item.link))
                item.current_price_byn = price
                item.last_seen_at = datetime.now(UTC)
                item.duplicate_count = duplicate_index.get(item.ad_id, 0)
                item.market_status = "duplicate" if item.duplicate_count > 0 else "active"
                updated += 1
                if previous is not None and price is not None and price < previous:
                    item.market_status = "price_drop"
                    price_drops += 1

        await session.commit()
        return WatchlistRefreshResponse(updated=updated, missing=missing, price_drops=price_drops)
