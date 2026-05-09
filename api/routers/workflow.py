from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_kufar_client,
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import DealExpense, LeadItem, LeadItemPriceSnapshot
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
from api.services.listing_mapper import first_image_url
from api.services.query_pipeline import load_query_dataset
from api.services.workflow_store import (
    ensure_user,
    load_last_snapshot_prices,
    make_price_snapshot,
    record_price_snapshot,
    resolve_user_id,
    upsert_lead,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflow"])

# Status used internally for watchlist items (merged-in from old watchlist_items).
WATCHING_STATUS = "watching"


def _serialize_watchlist(
    item: LeadItem,
    *,
    price_history: list[dict] | None = None,
) -> WatchlistRead:
    """Serialize a LeadItem with status='watching' as a WatchlistRead.

    Keeps backward-compatible API contract for the frontend after the
    watchlist_items table was merged into lead_items. Optionally
    inlines a small price-history series so the watchlist sparkline
    can render without a per-row round-trip.
    """
    current = item.price_byn
    initial = item.initial_price_byn
    delta_byn = None
    delta_percent = None
    if current is not None and initial is not None:
        delta_byn = round(float(current) - float(initial), 2)
        if initial:
            delta_percent = round((delta_byn / float(initial)) * 100.0, 2)
    return WatchlistRead(
        id=item.id,
        user_id=item.user_id,
        ad_id=item.ad_id,
        query=item.query,
        title=item.title,
        link=item.link,
        thumbnail=item.thumbnail,
        initial_price_byn=item.initial_price_byn,
        current_price_byn=item.price_byn,
        price_delta_byn=delta_byn,
        price_delta_percent=delta_percent,
        # workflow_status was a UI priority chip that the dropdown UI no
        # longer surfaces. Keep the field for API stability with constant
        # default value.
        workflow_status="default",
        market_status=item.market_status,
        market_median_byn=item.market_median_byn,
        notes=item.notes,
        created_at=item.created_at,
        last_seen_at=item.last_seen_at,
        missing_since_at=item.missing_since_at,
        updated_at=item.updated_at,
        price_history=price_history or [],
    )


@router.get("/leads", response_model=list[LeadRead])
@limiter.limit("30/minute")
async def get_leads(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[LeadRead]:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return []
        # Exclude `watching` items here — those are exposed via /watchlist
        # endpoints to keep the frontend contract unchanged.
        result = await session.execute(
            select(LeadItem)
            .where(LeadItem.user_id == user_id, LeadItem.status != WATCHING_STATUS)
            .order_by(LeadItem.updated_at.desc(), LeadItem.id.desc())
            .limit(limit)
            .offset(offset)
        )
        leads = list(result.scalars())

        # Fetch all expenses for these leads in a single query (efficient)
        lead_ids = [lead.id for lead in leads]
        if lead_ids:
            expense_result = await session.execute(
                select(DealExpense.lead_id, DealExpense.amount_byn).where(
                    DealExpense.lead_id.in_(lead_ids)
                )
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
                sold_price = float(lead.sold_price_byn)
                buy_price = float(lead.buy_price_byn) if lead.buy_price_byn is not None else 0.0
                total_cost = buy_price + total_expenses
                actual_profit = round(sold_price - total_cost, 2)
                if total_cost > 0:
                    roi_percent = round((actual_profit / total_cost) * 100, 2)

            lead_read = LeadRead.model_validate(lead)
            lead_read.total_expenses = total_expenses
            lead_read.actual_profit = actual_profit
            lead_read.roi_percent = roi_percent

            output.append(lead_read)

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
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        # Prevent silent overwrite — if an active lead already exists for
        # this ad, return 409 so the frontend can show a clear message.
        existing = await session.scalar(
            select(LeadItem).where(
                LeadItem.user_id == user_id,
                LeadItem.ad_id == payload.ad_id,
            ).with_for_update()
        )
        if existing is not None and existing.status not in {
            WATCHING_STATUS, "closed",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Этот лот уже в покупках",
            )
        lead = await upsert_lead(
            session,
            user_id=user_id,
            ad_id=payload.ad_id,
            query=payload.query,
            title=payload.title,
            link=payload.link,
            price_byn=payload.price_byn,
            thumbnail=payload.thumbnail,
            target_resale_byn=payload.target_resale_byn,
            status=payload.status.value,
            source=payload.source,
            market_median_byn=payload.market_median_byn,
            notes=payload.notes,
        )
        await session.commit()
        # Refresh to materialize server-generated columns (created_at,
        # updated_at, server_default columns) before pydantic walks the
        # ORM attributes — accessing expired attrs after commit would
        # otherwise trigger a sync lazy load and raise MissingGreenlet.
        await session.refresh(lead)
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
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        lead = await session.scalar(
            select(LeadItem)
            .where(LeadItem.id == lead_id, LeadItem.user_id == user_id)
            .with_for_update()
        )
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        if "status" in payload.model_fields_set:
            lead.status = payload.status.value
        if "target_resale_byn" in payload.model_fields_set:
            lead.target_resale_byn = payload.target_resale_byn
        if "buy_price_byn" in payload.model_fields_set:
            lead.buy_price_byn = payload.buy_price_byn
        if "sold_price_byn" in payload.model_fields_set:
            lead.sold_price_byn = payload.sold_price_byn
            if payload.sold_price_byn is not None and lead.status != "sold":
                lead.status = "sold"
                lead.sold_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(lead)  # Refresh to get server-generated updated_at
        return LeadRead.model_validate(lead)


@router.delete("/leads/all", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_all_leads(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is not None:
            # Keep closed deals for finance tracking and watching items
            # (they belong to the watchlist surface, not deals).
            preserved_statuses = ("closed", WATCHING_STATUS)
            active_lead_ids = select(LeadItem.id).where(
                LeadItem.user_id == user_id,
                LeadItem.status.notin_(preserved_statuses),
            )
            # Bulk DELETE bypasses ORM cascade — remove snapshots and
            # expenses explicitly before the parent rows disappear.
            await session.execute(
                delete(LeadItemPriceSnapshot).where(
                    LeadItemPriceSnapshot.lead_item_id.in_(active_lead_ids)
                )
            )
            await session.execute(
                delete(DealExpense).where(DealExpense.lead_id.in_(active_lead_ids))
            )
            # Delete only active leads (keep closed deals for finance tracking)
            await session.execute(
                delete(LeadItem).where(
                    LeadItem.user_id == user_id,
                    LeadItem.status.notin_(preserved_statuses),
                )
            )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def delete_lead(
    request: Request,
    lead_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        lead = await session.scalar(
            select(LeadItem)
            .where(LeadItem.id == lead_id, LeadItem.user_id == user_id)
            .with_for_update()
        )
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        # Explicit pre-delete for bulk-style safety (ORM cascade may
        # not fire for all relationship configurations).
        await session.execute(
            delete(LeadItemPriceSnapshot).where(
                LeadItemPriceSnapshot.lead_item_id == lead.id
            )
        )
        await session.execute(
            delete(DealExpense).where(DealExpense.lead_id == lead.id)
        )
        await session.delete(lead)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/watchlist", response_model=list[WatchlistRead])
@limiter.limit("30/minute")
async def get_watchlist(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[WatchlistRead]:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return []
        result = await session.execute(
            select(LeadItem)
            .where(LeadItem.user_id == user_id, LeadItem.status == WATCHING_STATUS)
            .order_by(LeadItem.updated_at.desc(), LeadItem.id.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(result.scalars())

        # Bulk-load the last 30-day price snapshots for every visible
        # row in a single query, then group by lead_item_id so the
        # serialiser can attach each row's slice without a per-row
        # round-trip. Empty lists fall through to "no movement yet".
        history_by_item: dict[int, list[dict]] = {}
        if items:
            cutoff = datetime.now(UTC) - timedelta(days=30)
            history_rows = await session.execute(
                select(
                    LeadItemPriceSnapshot.lead_item_id,
                    LeadItemPriceSnapshot.snapped_at,
                    LeadItemPriceSnapshot.price_byn,
                )
                .where(
                    LeadItemPriceSnapshot.lead_item_id.in_([i.id for i in items]),
                    LeadItemPriceSnapshot.snapped_at >= cutoff,
                )
                .order_by(LeadItemPriceSnapshot.snapped_at.asc())
            )
            for row in history_rows:
                history_by_item.setdefault(row.lead_item_id, []).append(
                    {"snapped_at": row.snapped_at, "price_byn": float(row.price_byn)}
                )

        return [
            _serialize_watchlist(item, price_history=history_by_item.get(item.id))
            for item in items
        ]


@router.post("/watchlist", response_model=WatchlistRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def create_watchlist_item(
    request: Request,
    payload: WatchlistCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> WatchlistRead:
    async with session_factory() as session:
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        # If the user already has an active lead for this ad, refuse the
        # "add to watchlist" with a 409 — the frontend treats it as
        # "уже в покупках" instead of silently no-op'ing in a confusing way.
        existing = await session.scalar(
            select(LeadItem).where(
                LeadItem.user_id == user_id,
                LeadItem.ad_id == payload.ad_id,
            )
        )
        if existing is not None and existing.status != WATCHING_STATUS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Этот лот уже в покупках",
            )
        item = await upsert_lead(
            session,
            user_id=user_id,
            ad_id=payload.ad_id,
            query=payload.query,
            title=payload.title,
            link=payload.link,
            price_byn=payload.price_byn,
            thumbnail=payload.thumbnail,
            status=WATCHING_STATUS,
            source="watchlist",
            market_median_byn=payload.market_median_byn,
            notes=payload.notes,
            track_initial_price=True,
            update_last_seen=True,
        )
        # Seed the price-history sparkline with an initial point so the
        # very first refresh isn't a single dot — flush() to assign an
        # id before the snapshot's FK validates.
        await session.flush()
        await record_price_snapshot(
            session, lead_item=item, price_byn=payload.price_byn
        )
        await session.commit()
        await session.refresh(item)
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
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        item = await session.scalar(
            select(LeadItem).where(
                LeadItem.id == watchlist_id,
                LeadItem.user_id == user_id,
                LeadItem.status == WATCHING_STATUS,
            )
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Watchlist item not found",
            )
        # `workflow_status` is intentionally a no-op now (priority chip removed
        # from the UI). Notes update is the only real mutation.
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
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is not None:
            watching_ids = select(LeadItem.id).where(
                LeadItem.user_id == user_id,
                LeadItem.status == WATCHING_STATUS,
            )
            # Bulk DELETE bypasses ORM cascade — remove snapshots and
            # expenses explicitly before the parent rows disappear.
            await session.execute(
                delete(LeadItemPriceSnapshot).where(
                    LeadItemPriceSnapshot.lead_item_id.in_(watching_ids)
                )
            )
            await session.execute(
                delete(DealExpense).where(DealExpense.lead_id.in_(watching_ids))
            )
            await session.execute(
                delete(LeadItem).where(
                    LeadItem.user_id == user_id,
                    LeadItem.status == WATCHING_STATUS,
                )
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
    """Remove a watchlist item.

    Idempotent: returns 204 even if the item was already removed or has
    been promoted to a non-watching lead status. This avoids confusing
    "Watchlist item not found" toasts when the user clicks Удалить on a
    stale UI card whose underlying row is no longer in the watchlist.
    """
    async with session_factory() as session:
        user_id = await ensure_user(
            session,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
        )
        item = await session.scalar(
            select(LeadItem).where(
                LeadItem.id == watchlist_id,
                LeadItem.user_id == user_id,
                LeadItem.status == WATCHING_STATUS,
            )
        )
        if item is not None:
            # Explicit pre-delete for cascade safety (matches delete_lead pattern).
            await session.execute(
                delete(LeadItemPriceSnapshot).where(
                    LeadItemPriceSnapshot.lead_item_id == item.id
                )
            )
            await session.execute(
                delete(DealExpense).where(DealExpense.lead_id == item.id)
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
    kufar_client: KufarClient = Depends(get_kufar_client),
) -> WatchlistRefreshResponse:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return WatchlistRefreshResponse(updated=0, missing=0, price_drops=0, auto_removed=0)
        result = await session.execute(
            select(LeadItem).where(
                LeadItem.user_id == user_id,
                LeadItem.status == WATCHING_STATUS,
            )
        )
        items = list(result.scalars())
        if not items:
            return WatchlistRefreshResponse(updated=0, missing=0, price_drops=0, auto_removed=0)

        grouped: dict[str, list[LeadItem]] = defaultdict(list)
        for item in items:
            grouped[item.query].append(item)

        # Bulk-load the last snapshot price for every watched item in
        # one query — avoids an N+1 inside record_price_snapshot below.
        # Items without a snapshot row aren't in the dict; we treat
        # those as "always record" by passing -1 as the sentinel.
        last_prices = await load_last_snapshot_prices(
            session, [item.id for item in items if item.id is not None]
        )

        # Batch Kufar queries in parallel instead of sequential N+1
        unique_queries = list(grouped.keys())
        datasets = await asyncio.gather(
            *[
                load_query_dataset(
                    query=q,
                    currency="BYN",
                    strict_search=False,
                    settings=settings,
                    client=kufar_client,
                )
                for q in unique_queries
            ],
            return_exceptions=True,
        )

        updated = 0
        missing = 0
        price_drops = 0
        snapshots_to_insert: list[dict[str, Any]] = []
        for query, dataset in zip(unique_queries, datasets, strict=True):
            if isinstance(dataset, Exception):
                logger.warning("Watchlist refresh: query %r failed: %s", query, dataset)
                continue
            query_items = grouped[query]
            ads_by_id = {
                int(ad.get("ad_id", 0)): ad for ad in dataset.ads if int(ad.get("ad_id", 0)) > 0
            }
            for item in query_items:
                ad = ads_by_id.get(item.ad_id)
                if ad is None:
                    item.market_status = "missing"
                    if item.missing_since_at is None:
                        item.missing_since_at = datetime.now(UTC)
                    missing += 1
                    continue
                price = normalize_price_byn(ad.get("price_byn"))
                previous = item.price_byn
                item.title = str(ad.get("subject", item.title))
                item.link = str(ad.get("ad_link", item.link))
                item.price_byn = price
                item.last_seen_at = datetime.now(UTC)
                item.market_status = "active"
                item.missing_since_at = None
                # Update thumbnail if Kufar returned a new one
                new_thumb = first_image_url(ad)
                if new_thumb:
                    item.thumbnail = new_thumb
                updated += 1
                if previous is not None and price is not None and price < float(previous):
                    item.market_status = "price_drop"
                    price_drops += 1
                # Build snapshot dicts in-memory and INSERT once after
                # the loop instead of session.add() per iteration.
                snapshot = make_price_snapshot(
                    lead_item=item,
                    price_byn=price,
                    last_known_price=last_prices.get(item.id, -1.0),
                )
                if snapshot:
                    snapshots_to_insert.append(snapshot)

        if snapshots_to_insert:
            await session.execute(insert(LeadItemPriceSnapshot).values(snapshots_to_insert))

        # Auto-remove watchlist items that have been missing too long
        auto_remove_cutoff = datetime.now(UTC) - timedelta(days=settings.auto_remove_missing_days)
        auto_removed = 0
        for item in items:
            if (
                item.market_status == "missing"
                and item.missing_since_at is not None
                and item.missing_since_at < auto_remove_cutoff
            ):
                await session.execute(
                    delete(LeadItemPriceSnapshot).where(
                        LeadItemPriceSnapshot.lead_item_id == item.id
                    )
                )
                await session.execute(
                    delete(DealExpense).where(DealExpense.lead_id == item.id)
                )
                await session.delete(item)
                auto_removed += 1

        await session.commit()
        return WatchlistRefreshResponse(
            updated=updated,
            missing=max(missing - auto_removed, 0),
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
    kufar_client: KufarClient = Depends(get_kufar_client),
) -> LeadsRefreshResponse:
    """Check which leads are still available on Kufar.

    Marks missing ones but does NOT auto-delete them.
    """
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return LeadsRefreshResponse(checked=0, active=0, missing=0)
        result = await session.execute(
            select(LeadItem).where(
                LeadItem.user_id == user_id,
                LeadItem.status.notin_(["sold", "skipped", WATCHING_STATUS]),
            )
        )
        leads = list(result.scalars())
        if not leads:
            return LeadsRefreshResponse(checked=0, active=0, missing=0)

        grouped: dict[str, list[LeadItem]] = defaultdict(list)
        for lead in leads:
            grouped[lead.query].append(lead)

        # Batch Kufar queries in parallel instead of sequential N+1
        unique_queries = list(grouped.keys())
        datasets = await asyncio.gather(
            *[
                load_query_dataset(
                    query=q,
                    currency="BYN",
                    strict_search=False,
                    settings=settings,
                    client=kufar_client,
                )
                for q in unique_queries
            ],
            return_exceptions=True,
        )

        checked = 0
        active_count = 0
        missing_count = 0
        for query, dataset in zip(unique_queries, datasets, strict=True):
            if isinstance(dataset, Exception):
                logger.warning("Leads refresh: query %r failed: %s", query, dataset)
                continue
            query_leads = grouped[query]
            ads_by_id = {
                int(ad.get("ad_id", 0)): ad for ad in dataset.ads if int(ad.get("ad_id", 0)) > 0
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
