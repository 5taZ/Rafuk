from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.middleware.telegram_auth import TelegramInitData
from api.models import SavedSearch, TrackerEvent
from api.rate_limit import limiter
from api.schemas import (
    OpportunityBoardResponse,
    OpportunityItem,
    OpportunitySignal,
    SavedSearchCreate,
    SavedSearchRead,
)
from api.services.aggregator import filter_deal_ads
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.listing_mapper import build_listing_item
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import (
    analyze_query_text,
    default_saved_search_name,
    matches_tracker_filters,
)
from api.services.tracker_service import MAX_DB_ID
from api.services.users import (
    MAX_ACTIVE_SAVED_SEARCHES_PER_USER,
    count_user_rows,
    ensure_init_data_user,
    lock_user,
)
from api.services.workflow_store import gather_within_budget

if TYPE_CHECKING:
    from api.services.query_pipeline import QueryDataset

logger = logging.getLogger(__name__)

router = APIRouter(tags=["saved-searches"])

BOARD_MAX_SAVED_SEARCHES = 8
# Pages fetched per saved search: deals are looked for among the newest listings. All saved
# searches share one time budget (see gather_within_budget).
BOARD_MAX_PAGES = 5
# A deal in a market this small is shown once, under "rare offers".
RARE_OFFER_MAX_RESULTS = 5
# "Thin market" is about the size of the market, not about how many deals were found.
THIN_MARKET_MAX_RESULTS = 10
RARE_OFFER_LABEL = "Редкий оффер"


@dataclass(slots=True)
class _SavedSearchBoard:
    saved_search: SavedSearch
    total_results: int
    items: list[OpportunityItem]
    # Only part of the market was fetched: total_results may be a lower bound, so the board
    # never calls such a market rare or thin.
    truncated: bool = False


@dataclass(slots=True)
class _BoardsOutcome:
    boards: list[_SavedSearchBoard]
    # Some saved searches failed or did not fit in the time budget.
    partial: bool = False


def _dataset_truncated(dataset: QueryDataset) -> bool:
    # QueryDataset.truncated arrives with the query pipeline change; the raw response has
    # carried the flag since the pagination limits were introduced.
    return bool(getattr(dataset, "truncated", False) or dataset.response.get("truncated"))


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
        result = await session.execute(
            select(SavedSearch)
            .where(SavedSearch.user_id == telegram_user.user_id, SavedSearch.active.is_(True))
            .order_by(SavedSearch.created_at.desc(), SavedSearch.id.desc())
        )
        saved_searches = list(result.scalars())
    return [_serialize_saved_search(item) for item in saved_searches]


@router.post(
    "/saved-searches",
    response_model=SavedSearchRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_saved_search(
    payload: SavedSearchCreate,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> SavedSearchRead:
    query = payload.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Запрос не должен быть пустым.",
        )

    name = (payload.name or "").strip() or default_saved_search_name(query)
    async with session_factory() as session:
        # Serialize one user's creations so parallel requests cannot pass the quota together.
        await ensure_init_data_user(session, telegram_user)
        await lock_user(session, telegram_user.user_id)
        active_count = await count_user_rows(
            session,
            SavedSearch,
            telegram_user.user_id,
            SavedSearch.active.is_(True),
        )
        if active_count >= MAX_ACTIVE_SAVED_SEARCHES_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Достигнут лимит сохранённых поисков "
                    f"({MAX_ACTIVE_SAVED_SEARCHES_PER_USER}). Удалите ненужные поиски."
                ),
            )
        saved_search = SavedSearch(
            user_id=telegram_user.user_id,
            name=name,
            group_name=(payload.group_name or "").strip() or None,
            query=query,
            strict_mode=payload.strict_mode,
            target_discount_percent=abs(payload.target_discount_percent),
            max_price_byn=payload.max_price_byn,
            seller_type=payload.seller_type,
            condition=payload.condition,
            region_name=payload.region_name,
            # No keyword filter unless the user asked for one: the query is not a keyword.
            config_keyword=(payload.config_keyword or "").strip() or None,
            exclude_duplicates=payload.exclude_duplicates,
        )
        session.add(saved_search)
        await session.commit()
        await session.refresh(saved_search)
    return _serialize_saved_search(saved_search)


@router.delete("/saved-searches/{saved_search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_search(
    saved_search_id: int = Path(ge=1, le=MAX_DB_ID),
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    async with session_factory() as session:
        result = await session.execute(
            select(SavedSearch).where(
                SavedSearch.id == saved_search_id,
                SavedSearch.user_id == telegram_user.user_id,
                SavedSearch.active.is_(True),
            )
        )
        saved_search = result.scalar_one_or_none()
        if saved_search is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Сохранённый поиск не найден",
            )
        saved_search.active = False
        saved_search.deleted_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _load_saved_search_opportunities(
    saved_search: SavedSearch,
    *,
    currency: str,
    settings: Settings,
    currency_service: CurrencyService,
    search_kwargs: dict[str, Any] | None = None,
) -> _SavedSearchBoard:
    dataset = await load_query_dataset(
        query=saved_search.query,
        currency=currency,
        strict_search=saved_search.strict_mode,
        settings=settings,
        client_factory=KufarClient,
        search_kwargs=search_kwargs,
    )
    total_results = dataset.total_results
    truncated = _dataset_truncated(dataset)
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
        return _SavedSearchBoard(
            saved_search=saved_search,
            total_results=total_results,
            items=[],
            truncated=truncated,
        )

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
    is_rare = not truncated and total_results <= RARE_OFFER_MAX_RESULTS
    signal_label = RARE_OFFER_LABEL if is_rare else None
    opportunities = [
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
            signal_label=signal_label,
            listing=item,
        )
        for item in items[:3]
    ]
    return _SavedSearchBoard(
        saved_search=saved_search,
        total_results=total_results,
        items=opportunities,
        truncated=truncated,
    )


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
            .order_by(TrackerEvent.created_at.desc(), TrackerEvent.id.desc())
            .limit(6)
        )
        events = list(result.scalars())
    return [
        OpportunitySignal(
            title=event.title,
            subtitle=event.query,
            query=event.query,
            metric=f"-{round(abs(float(event.delta_byn or 0.0)))} BYN",
            strict_mode=bool(event.strict_mode),
        )
        for event in events
        if event.delta_byn
    ]


async def _load_boards_isolated(
    saved_searches: list[SavedSearch],
    *,
    currency: str,
    settings: Settings,
    currency_service: CurrencyService,
) -> _BoardsOutcome:
    """Load the saved searches within one time budget; failed or late searches are skipped.

    Raises ``KufarAPIError`` when no saved search loaded and at least one failed, so the
    client sees an error instead of an empty board.
    """
    by_id = {saved_search.id: saved_search for saved_search in saved_searches}

    async def load(saved_search_id: int, remaining_seconds: float) -> _SavedSearchBoard:
        return await _load_saved_search_opportunities(
            by_id[saved_search_id],
            currency=currency,
            settings=settings,
            currency_service=currency_service,
            search_kwargs={
                "max_pages": BOARD_MAX_PAGES,
                "deadline_seconds": remaining_seconds,
            },
        )

    ids = list(by_id)
    outcome = await gather_within_budget(
        ids,
        load,
        concurrency=settings.kufar_parallel_semaphore,
    )
    for saved_search_id, error in outcome.failed:
        logger.warning(
            "Opportunity board: saved search %s (%r) failed: %s",
            saved_search_id,
            by_id[saved_search_id].query,
            error,
        )
    if outcome.skipped:
        logger.warning(
            "Opportunity board: %s of %s saved searches skipped (time budget)",
            len(outcome.skipped),
            len(ids),
        )
    if ids and not outcome.results and outcome.failed:
        first_error = outcome.failed[0][1]
        if isinstance(first_error, KufarAPIError):
            raise first_error
        raise KufarAPIError(f"Opportunity board failed: {first_error}") from first_error
    boards = [outcome.results[key] for key in ids if key in outcome.results]
    return _BoardsOutcome(boards=boards, partial=outcome.partial)


def _market_signal(board: _SavedSearchBoard) -> OpportunitySignal | None:
    saved_search = board.saved_search
    if not board.truncated and 0 < board.total_results <= THIN_MARKET_MAX_RESULTS:
        return OpportunitySignal(
            title=saved_search.name,
            subtitle="рынок тонкий",
            query=saved_search.query,
            metric=f"на рынке {board.total_results} шт.",
            strict_mode=saved_search.strict_mode,
        )
    if board.items and float(board.items[0].listing.deal_score or 0.0) >= 80:
        return OpportunitySignal(
            title=saved_search.name,
            subtitle="хорошая маржа прямо сейчас",
            query=saved_search.query,
            metric=f"{round(board.items[0].listing.deal_score)} score",
            strict_mode=saved_search.strict_mode,
        )
    return None


@router.get("/opportunity-board", response_model=OpportunityBoardResponse)
@limiter.limit("10/minute")
async def get_opportunity_board(
    request: Request,
    currency: Literal["BYN", "USD"] = "BYN",
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    settings: Settings = Depends(get_settings_dependency),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> OpportunityBoardResponse:
    async with session_factory() as session:
        result = await session.execute(
            select(SavedSearch)
            .where(SavedSearch.user_id == telegram_user.user_id, SavedSearch.active.is_(True))
            .order_by(SavedSearch.created_at.desc(), SavedSearch.id.desc())
            .limit(BOARD_MAX_SAVED_SEARCHES)
        )
        saved_searches = list(result.scalars())

    # Price drops come from tracker events and do not depend on saved searches.
    top_price_drops = await _load_price_drop_signals(
        user_id=telegram_user.user_id,
        session_factory=session_factory,
    )
    loaded = (
        await _load_boards_isolated(
            saved_searches,
            currency=currency,
            settings=settings,
            currency_service=currency_service,
        )
        if saved_searches
        else _BoardsOutcome(boards=[])
    )
    boards = loaded.boards

    items: list[OpportunityItem] = []
    rare_opportunities: list[OpportunityItem] = []
    for board in boards:
        for item in board.items:
            if item.signal_label == RARE_OFFER_LABEL:
                rare_opportunities.append(item)
            else:
                items.append(item)

    def deal_order(item: OpportunityItem) -> tuple[float, float]:
        return -float(item.listing.deal_score or 0.0), float(item.listing.price or 0.0)

    items.sort(key=deal_order)
    rare_opportunities.sort(key=deal_order)
    market_signals = [signal for board in boards if (signal := _market_signal(board))]
    return OpportunityBoardResponse(
        currency=currency,
        items=items[:12],
        top_price_drops=top_price_drops[:4],
        rare_opportunities=rare_opportunities[:4],
        market_signals=market_signals[:4],
        partial=loaded.partial,
    )
