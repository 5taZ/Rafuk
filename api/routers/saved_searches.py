from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import SavedSearch, TrackerEvent
from api.schemas import (
    OpportunityBoardResponse,
    OpportunityItem,
    OpportunitySignal,
    SavedSearchCreate,
    SavedSearchRead,
)
from api.services.aggregator import filter_deal_ads
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_item
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import (
    analyze_query_text,
    default_config_keyword,
    default_saved_search_name,
    matches_tracker_filters,
)
from api.services.workflow_store import ensure_user, resolve_user_id

router = APIRouter(tags=["saved-searches"])


def _serialize_saved_search(saved_search: SavedSearch) -> SavedSearchRead:
    insights = analyze_query_text(saved_search.query)
    return SavedSearchRead(
        id=saved_search.id,
        user_id=saved_search.user_id,
        name=saved_search.name,
        group_name=saved_search.group_name,
        query=saved_search.query,
        normalized_query=insights.normalized_query,
        config_summary=insights.config_summary,
        storage_gb=insights.storage_gb,
        ram_gb=insights.ram_gb,
        strict_mode=saved_search.strict_mode,
        target_discount_percent=saved_search.target_discount_percent,
        max_price_byn=saved_search.max_price_byn,
        seller_type=saved_search.seller_type,
        condition=saved_search.condition,
        region_name=saved_search.region_name,
        config_keyword=saved_search.config_keyword,
        exclude_duplicates=saved_search.exclude_duplicates,
        active=saved_search.active,
        created_at=saved_search.created_at,
    )


@router.get("/saved-searches", response_model=list[SavedSearchRead])
async def get_saved_searches(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> list[SavedSearchRead]:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            return []
        result = await session.execute(
            select(SavedSearch)
            .where(SavedSearch.user_id == user_id, SavedSearch.active.is_(True))
            .order_by(SavedSearch.created_at.desc(), SavedSearch.id.desc())
        )
        saved_searches = list(result.scalars())
    return [_serialize_saved_search(item) for item in saved_searches]


@router.post(
    "/saved-searches",
    response_model=SavedSearchRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def create_saved_search(
    request: Request,
    payload: SavedSearchCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> SavedSearchRead:
    query = payload.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Query must not be empty",
        )

    name = (payload.name or "").strip() or default_saved_search_name(query)
    async with session_factory() as session:
        saved_search = SavedSearch(
            user_id=await ensure_user(
                session,
                telegram_user_id=telegram_user.user_id,
                first_name=telegram_user.first_name,
            ),
            name=name,
            group_name=(payload.group_name or "").strip() or None,
            query=query,
            strict_mode=payload.strict_mode,
            target_discount_percent=abs(payload.target_discount_percent),
            max_price_byn=payload.max_price_byn,
            seller_type=payload.seller_type,
            condition=payload.condition,
            region_name=payload.region_name,
            config_keyword=payload.config_keyword or default_config_keyword(query),
            exclude_duplicates=payload.exclude_duplicates,
        )
        session.add(saved_search)
        await session.commit()
    return _serialize_saved_search(saved_search)


@router.delete("/saved-searches/{saved_search_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def delete_saved_search(
    request: Request,
    saved_search_id: int,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved search not found",
            )
        result = await session.execute(
            select(SavedSearch).where(
                SavedSearch.id == saved_search_id,
                SavedSearch.user_id == user_id,
                SavedSearch.active.is_(True),
            )
        )
        saved_search = result.scalar_one_or_none()
        if saved_search is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved search not found",
            )
        saved_search.active = False
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _load_saved_search_opportunities(
    saved_search: SavedSearch,
    *,
    currency: str,
    settings: Settings,
    currency_service: CurrencyService,
) -> list[OpportunityItem]:
    dataset = await load_query_dataset(
        query=saved_search.query,
        currency=currency,
        strict_search=saved_search.strict_mode,
        settings=settings,
        client_factory=KufarClient,
    )
    duplicate_index = duplicate_counts(dataset.ads)
    candidate_ads = filter_deal_ads(
        dataset.ads,
        dataset.price_stats.median,
        saved_search.target_discount_percent,
    )
    candidate_ads = [
        ad
        for ad in candidate_ads
        if matches_tracker_filters(
            ad,
            market_stats=dataset.price_stats,
            duplicate_count=duplicate_index.get(int(ad.get("ad_id", 0)), 0),
            min_discount_percent=saved_search.target_discount_percent,
            max_price_byn=saved_search.max_price_byn,
            seller_type=saved_search.seller_type,
            condition=saved_search.condition,
            region_name=saved_search.region_name,
            config_keyword=saved_search.config_keyword,
            exclude_duplicates=saved_search.exclude_duplicates,
        )
    ]
    if not candidate_ads:
        return []

    rates_payload = await currency_service.get_rates()
    items = [
        build_listing_item(
            ad,
            query=saved_search.query,
            currency=currency,
            rates=rates_payload["rates"],
            currency_service=currency_service,
            median_byn=dataset.price_stats.median,
            market_stats=dataset.price_stats,
            duplicate_count=duplicate_index.get(int(ad.get("ad_id", 0)), 0),
        )
        for ad in candidate_ads[:20]
    ]
    items.sort(key=lambda item: (-float(item.deal_score or 0.0), float(item.price or 0.0)))
    insights = analyze_query_text(saved_search.query)
    return [
        OpportunityItem(
            saved_search_id=saved_search.id,
            saved_search_name=saved_search.name,
            query=saved_search.query,
            normalized_query=insights.normalized_query,
            config_summary=insights.config_summary,
            strict_mode=saved_search.strict_mode,
            target_discount_percent=saved_search.target_discount_percent,
            max_price_byn=saved_search.max_price_byn,
            seller_type=saved_search.seller_type,
            condition=saved_search.condition,
            region_name=saved_search.region_name,
            config_keyword=saved_search.config_keyword,
            exclude_duplicates=saved_search.exclude_duplicates,
            signal_label="Редкий оффер" if dataset.total_results <= 5 else None,
            listing=item,
        )
        for item in items[:3]
    ]


async def _load_price_drop_signals(
    *,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[OpportunitySignal]:
    async with session_factory() as session:
        result = await session.execute(
            select(TrackerEvent)
            .where(
                TrackerEvent.user_id == user_id,
                TrackerEvent.event_type == "price_drop",
            )
            .order_by(TrackerEvent.created_at.desc())
            .limit(6)
        )
        events = list(result.scalars())
    return [
        OpportunitySignal(
            title=event.title,
            subtitle=event.query,
            query=event.query,
            metric=f"-{round(float(event.delta_byn or 0.0))} BYN",
        )
        for event in events
        if event.delta_byn
    ]


@router.get("/opportunity-board", response_model=OpportunityBoardResponse)
async def get_opportunity_board(
    currency: str = "BYN",
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    settings: Settings = Depends(get_settings_dependency),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> OpportunityBoardResponse:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            return OpportunityBoardResponse(
                currency=currency,
                items=[],
                top_price_drops=[],
                rare_opportunities=[],
                market_signals=[],
            )
        result = await session.execute(
            select(SavedSearch)
            .where(SavedSearch.user_id == user_id, SavedSearch.active.is_(True))
            .order_by(SavedSearch.created_at.desc(), SavedSearch.id.desc())
            .limit(8)
        )
        saved_searches = list(result.scalars())

    if not saved_searches:
        return OpportunityBoardResponse(
            currency=currency,
            items=[],
            top_price_drops=[],
            rare_opportunities=[],
            market_signals=[],
        )

    batches = await asyncio.gather(
        *[
            _load_saved_search_opportunities(
                saved_search,
                currency=currency,
                settings=settings,
                currency_service=currency_service,
            )
            for saved_search in saved_searches
        ]
    )
    items = [item for batch in batches for item in batch]
    rare_opportunities = [
        item
        for item in items
        if item.signal_label == "Редкий оффер"
    ]
    items.sort(
        key=lambda item: (
            -float(item.listing.deal_score or 0.0),
            float(item.listing.price or 0.0),
        )
    )
    top_price_drops = await _load_price_drop_signals(
        user_id=user_id,
        session_factory=session_factory,
    )
    market_signals = []
    for batch, saved_search in zip(batches, saved_searches, strict=True):
        if not batch:
            continue
        best_item = batch[0]
        if len(batch) <= 1:
            market_signals.append(
                OpportunitySignal(
                    title=saved_search.name,
                    subtitle="рынок тонкий",
                    query=saved_search.query,
                    metric="мало предложений",
                )
            )
        elif float(best_item.listing.deal_score or 0.0) >= 80:
            market_signals.append(
                OpportunitySignal(
                    title=saved_search.name,
                    subtitle="хорошая маржа прямо сейчас",
                    query=saved_search.query,
                    metric=f"{round(best_item.listing.deal_score)} score",
                )
            )
    return OpportunityBoardResponse(
        currency=currency,
        items=items[:12],
        top_price_drops=top_price_drops[:4],
        rare_opportunities=rare_opportunities[:4],
        market_signals=market_signals[:4],
    )
