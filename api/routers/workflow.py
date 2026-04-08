from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import DealExpense, LeadItem, WatchlistItem
from api.schemas import (
    LeadCreate,
    LeadRead,
    LeadsRefreshResponse,
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
        thumbnail=item.thumbnail,
        initial_price_byn=item.initial_price_byn,
        current_price_byn=item.current_price_byn,
        price_delta_byn=delta_byn,
        price_delta_percent=delta_percent,
        workflow_status=item.workflow_status,
        market_status=item.market_status,
        duplicate_count=item.duplicate_count,
        market_median_byn=item.market_median_byn,
        notes=item.notes,
        created_at=item.created_at,
        last_seen_at=item.last_seen_at,
        missing_since_at=item.missing_since_at,
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
        leads = list(result.scalars())

        # Fetch all expenses for these leads in a single query (efficient)
        lead_ids = [lead.id for lead in leads]
        if lead_ids:
            expense_result = await session.execute(
                select(DealExpense.lead_id, DealExpense.amount_byn)
                .where(DealExpense.lead_id.in_(lead_ids))
            )
            # Build a mapping: lead_id -> total_expenses
            expenses_by_lead: dict[int, float] = defaultdict(float)
            for lead_id, amount_byn in expense_result:
                expenses_by_lead[lead_id] += float(amount_byn)
        else:
            expenses_by_lead = {}

        # Build response with computed fields
        output: list[LeadRead] = []
        for lead in leads:
            total_expenses = expenses_by_lead.get(lead.id, 0.0)
            actual_profit: float | None = None
            roi_percent: float | None = None

            if lead.sold_price_byn is not None:
                total_cost = (lead.price_byn or 0.0) + total_expenses
                actual_profit = round(lead.sold_price_byn - total_cost, 2)
                if total_cost > 0:
                    roi_percent = round((actual_profit / total_cost) * 100, 2)

            lead_dict = lead.__dict__.copy()
            # Remove _sa_instance_state if present
            lead_dict.pop("_sa_instance_state", None)
            lead_dict["total_expenses"] = total_expenses
            lead_dict["actual_profit"] = actual_profit
            lead_dict["roi_percent"] = roi_percent

            output.append(LeadRead.model_validate(lead_dict))

        return output


@router.post("/leads", response_model=LeadRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def create_lead(
    request: Request,
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
            thumbnail=payload.thumbnail,
            target_resale_byn=payload.target_resale_byn,
            status=payload.status,
            source=payload.source,
            notes=payload.notes,
        )
        await session.commit()
        return LeadRead.model_validate(lead)


@router.patch("/leads/{lead_id}", response_model=LeadRead)
@limiter.limit("20/minute")
async def update_lead(
    request: Request,
    lead_id: int,
    payload: LeadUpdate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> LeadRead:
    async with session_factory() as session:
        lead = await session.scalar(
            select(LeadItem).where(
                LeadItem.id == lead_id, LeadItem.user_id == telegram_user.user_id
            )
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
        await session.refresh(lead)  # Refresh to get server-generated updated_at
        return LeadRead.model_validate(lead)


@router.delete("/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def delete_lead(
    request: Request,
    lead_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        lead = await session.scalar(
            select(LeadItem).where(
                LeadItem.id == lead_id, LeadItem.user_id == telegram_user.user_id
            )
        )
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        await session.delete(lead)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/leads/all", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_all_leads(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        await session.execute(
            delete(LeadItem).where(LeadItem.user_id == telegram_user.user_id)
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
@limiter.limit("20/minute")
async def create_watchlist_item(
    request: Request,
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
            thumbnail=payload.thumbnail,
            market_median_byn=payload.market_median_byn,
            notes=payload.notes,
        )
        await session.commit()
        return _serialize_watchlist(item)


@router.patch("/watchlist/{watchlist_id}", response_model=WatchlistRead)
@limiter.limit("20/minute")
async def update_watchlist_item(
    request: Request,
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Watchlist item not found",
            )
        if "workflow_status" in payload.model_fields_set:
            item.workflow_status = payload.workflow_status
        if "notes" in payload.model_fields_set:
            item.notes = payload.notes
        await session.commit()
        await session.refresh(item)  # Refresh to get server-generated updated_at
        return _serialize_watchlist(item)


@router.delete("/watchlist/all", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_all_watchlist_items(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        await session.execute(
            delete(WatchlistItem).where(WatchlistItem.user_id == telegram_user.user_id)
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/watchlist/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def delete_watchlist_item(
    request: Request,
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Watchlist item not found",
            )
        await session.delete(item)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/watchlist/refresh", response_model=WatchlistRefreshResponse)
@limiter.limit("20/minute")
async def refresh_watchlist(
    request: Request,
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
            return WatchlistRefreshResponse(updated=0, missing=0, price_drops=0, auto_removed=0)

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
                    if item.missing_since_at is None:
                        item.missing_since_at = datetime.now(UTC)
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
                item.missing_since_at = None
                updated += 1
                if previous is not None and price is not None and price < previous:
                    item.market_status = "price_drop"
                    price_drops += 1

        # Auto-remove watchlist items that have been missing too long
        auto_remove_cutoff = datetime.now(UTC) - timedelta(days=settings.auto_remove_missing_days)
        auto_removed = 0
        for item in items:
            if (
                item.market_status == "missing"
                and item.missing_since_at is not None
                and item.missing_since_at < auto_remove_cutoff
            ):
                await session.delete(item)
                auto_removed += 1

        await session.commit()
        return WatchlistRefreshResponse(
            updated=updated,
            missing=missing - auto_removed,
            price_drops=price_drops,
            auto_removed=auto_removed,
        )


@router.post("/leads/refresh", response_model=LeadsRefreshResponse)
@limiter.limit("20/minute")
async def refresh_leads(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    settings: Settings = Depends(get_settings_dependency),
) -> LeadsRefreshResponse:
    """Check which leads are still available on Kufar.

    Marks missing ones but does NOT auto-delete them.
    """
    async with session_factory() as session:
        result = await session.execute(
            select(LeadItem).where(
                LeadItem.user_id == telegram_user.user_id,
                LeadItem.status.notin_(["sold", "skipped"]),
            )
        )
        leads = list(result.scalars())
        if not leads:
            return LeadsRefreshResponse(checked=0, active=0, missing=0)

        grouped: dict[str, list[LeadItem]] = defaultdict(list)
        for lead in leads:
            grouped[lead.query].append(lead)

        checked = 0
        active_count = 0
        missing_count = 0
        for query, query_leads in grouped.items():
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

            for lead in query_leads:
                checked += 1
                ad = ads_by_id.get(lead.ad_id)
                if ad is None:
                    lead.market_status = "missing"
                    if lead.missing_since_at is None:
                        lead.missing_since_at = datetime.now(UTC)
                    missing_count += 1
                else:
                    lead.market_status = "active"
                    lead.missing_since_at = None
                    active_count += 1

        await session.commit()
        return LeadsRefreshResponse(checked=checked, active=active_count, missing=missing_count)
