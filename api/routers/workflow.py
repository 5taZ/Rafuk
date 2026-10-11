from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
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
from api.rate_limit import limiter
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
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.services.tracker_service import MAX_DB_ID
from api.services.users import UserQuotaExceededError, ensure_init_data_user, lock_user
from api.services.workflow_store import gather_within_budget, upsert_lead, upsert_watchlist

if TYPE_CHECKING:
    from collections.abc import Sequence

    from api.services.query_pipeline import QueryDataset

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflow"])

# Pages fetched per watchlist query: lots are looked up among the newest listings only. A lot
# that is not found in a truncated result is skipped, never marked missing.
REFRESH_MAX_PAGES = 3


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
        await ensure_init_data_user(session, telegram_user)
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
        await session.refresh(lead)  # updated_at is server-generated and expired by UPDATE
        return LeadRead.model_validate(lead)


@router.patch("/leads/{lead_id}", response_model=LeadRead)
async def update_lead(
    payload: LeadUpdate,
    lead_id: int = Path(ge=1, le=MAX_DB_ID),
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> LeadRead:
    async with session_factory() as session:
        lead = await session.scalar(
            select(LeadItem).where(
                LeadItem.id == lead_id,
                LeadItem.user_id == telegram_user.user_id,
            )
        )
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Лид не найден")
        # status is NOT NULL: an explicit null means "leave unchanged".
        if "status" in payload.model_fields_set and payload.status is not None:
            lead.status = payload.status
        if "target_resale_byn" in payload.model_fields_set:
            lead.target_resale_byn = payload.target_resale_byn
        if "notes" in payload.model_fields_set:
            lead.notes = payload.notes
        await session.commit()
        await session.refresh(lead)  # Refresh to get server-generated updated_at
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
        await ensure_init_data_user(session, telegram_user)
        # Serialize one user's adds so parallel requests cannot pass the quota together.
        await lock_user(session, telegram_user.user_id)
        try:
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
        except UserQuotaExceededError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
        await session.commit()
        await session.refresh(item)  # updated_at is server-generated and expired by UPDATE
        return _serialize_watchlist(item)


@router.patch("/watchlist/{watchlist_id}", response_model=WatchlistRead)
async def update_watchlist_item(
    payload: WatchlistUpdate,
    watchlist_id: int = Path(ge=1, le=MAX_DB_ID),
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
                detail="Лот в избранном не найден",
            )
        # workflow_status is NOT NULL: an explicit null means "leave unchanged".
        if "workflow_status" in payload.model_fields_set and payload.workflow_status is not None:
            item.workflow_status = payload.workflow_status
        if "notes" in payload.model_fields_set:
            item.notes = payload.notes
        await session.commit()
        await session.refresh(item)  # Refresh to get server-generated updated_at
        return _serialize_watchlist(item)


@router.delete("/watchlist/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    watchlist_id: int = Path(ge=1, le=MAX_DB_ID),
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
                detail="Лот в избранном не найден",
            )
        await session.delete(item)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _dataset_truncated(dataset: QueryDataset) -> bool:
    """Whether the pagination stopped early (page limit, deadline or a failed later page)."""
    # QueryDataset.truncated arrives with the query pipeline change; the raw response has
    # carried the flag since the pagination limits were introduced.
    return bool(getattr(dataset, "truncated", False) or dataset.response.get("truncated"))


def _queries_by_staleness(rows: Sequence[tuple[str | None, datetime | None]]) -> list[str]:
    """Unique non-empty queries, the ones with the longest-unseen lots first.

    When the time budget runs out, repeated refreshes then reach the queries skipped before.
    """
    oldest: dict[str, datetime | None] = {}
    for raw_query, last_seen_at in rows:
        query = (raw_query or "").strip()
        if not query:
            continue  # empty queries would page through the whole Kufar catalogue
        if last_seen_at is not None and last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)  # SQLite drops the timezone
        if query not in oldest:
            oldest[query] = last_seen_at
            continue
        current = oldest[query]
        if current is not None and (last_seen_at is None or last_seen_at < current):
            oldest[query] = last_seen_at
    never_seen = datetime.min.replace(tzinfo=UTC)
    return sorted(oldest, key=lambda query: (oldest[query] or never_seen, query))


async def _load_watchlist_datasets(
    queries: list[str],
    *,
    settings: Settings,
) -> dict[str, QueryDataset]:
    """Fetch one dataset per query within one time budget; failed or late queries are skipped.

    Every query fetches at most ``REFRESH_MAX_PAGES`` pages. Raises ``KufarAPIError`` only
    when no query succeeded and at least one failed, so the client sees an error instead of
    a misleading "nothing changed".
    """

    async def load(query: str, remaining_seconds: float) -> QueryDataset:
        return await load_query_dataset(
            query=query,
            currency="BYN",
            strict_search=False,
            settings=settings,
            client_factory=KufarClient,
            search_kwargs={
                "max_pages": REFRESH_MAX_PAGES,
                "deadline_seconds": remaining_seconds,
            },
        )

    outcome = await gather_within_budget(
        queries,
        load,
        concurrency=settings.kufar_parallel_semaphore,
    )
    for query, error in outcome.failed:
        logger.warning("Watchlist refresh: query %r failed: %s", query, error)
    if outcome.skipped:
        logger.warning(
            "Watchlist refresh: %s of %s queries skipped (time budget)",
            len(outcome.skipped),
            len(queries),
        )
    if queries and not outcome.results and outcome.failed:
        first_error = outcome.failed[0][1]
        if isinstance(first_error, KufarAPIError):
            raise first_error
        raise KufarAPIError(f"Watchlist refresh failed: {first_error}") from first_error
    return outcome.results


@router.post("/watchlist/refresh", response_model=WatchlistRefreshResponse)
@limiter.limit("4/minute")
async def refresh_watchlist(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    settings: Settings = Depends(get_settings_dependency),
) -> WatchlistRefreshResponse:
    """Re-check the user's watchlist lots on Kufar.

    ``skipped`` counts lots this run could not check: their search failed or did not fit in
    the time budget, or the lot was not among the fetched (truncated) results. Their state
    is kept; such a lot is never marked missing.
    """
    # 1. Read what to refresh and release the connection before any network call.
    async with session_factory() as session:
        result = await session.execute(
            select(WatchlistItem.query, WatchlistItem.last_seen_at).where(
                WatchlistItem.user_id == telegram_user.user_id
            )
        )
        rows = [(query, last_seen_at) for query, last_seen_at in result.all()]
    if not rows:
        return WatchlistRefreshResponse(updated=0, missing=0, price_drops=0, skipped=0)

    # 2. Fetch market data outside any transaction.
    queries = _queries_by_staleness(rows)
    datasets = await _load_watchlist_datasets(queries, settings=settings)

    # 3. Apply the results in one short transaction.
    updated = 0
    missing = 0
    price_drops = 0
    skipped = 0
    async with session_factory() as session:
        result = await session.execute(
            select(WatchlistItem).where(WatchlistItem.user_id == telegram_user.user_id)
        )
        items = list(result.scalars())
        indexes: dict[str, tuple[dict[int, dict], dict[int, int]]] = {}
        for item in items:
            query = (item.query or "").strip()
            if not query:
                continue  # legacy row without a query: it can never be checked
            dataset = datasets.get(query)
            if dataset is None:
                skipped += 1  # Kufar failed or the budget ran out: keep the previous state
                continue
            if query not in indexes:
                ads_by_id = {
                    int(ad.get("ad_id", 0)): ad
                    for ad in dataset.ads
                    if int(ad.get("ad_id", 0)) > 0
                }
                indexes[query] = (ads_by_id, duplicate_counts(dataset.ads))
            ads_by_id, duplicate_index = indexes[query]

            ad = ads_by_id.get(item.ad_id)
            if ad is None:
                if _dataset_truncated(dataset):
                    # Only part of the market was fetched: the lot may simply be further down.
                    skipped += 1
                    continue
                item.market_status = "missing"
                missing += 1
                continue
            price = normalize_price_byn(ad.get("price_byn"))
            item.title = str(ad.get("subject") or item.title)
            item.link = str(ad.get("ad_link") or item.link)
            item.current_price_byn = price
            item.last_seen_at = datetime.now(UTC)
            item.duplicate_count = duplicate_index.get(item.ad_id, 0)
            updated += 1
            # The drop is measured against the price at the time the item was added, so the
            # signal stays on for as long as the price is below it, not just for one refresh.
            initial = item.initial_price_byn
            if price is not None and initial is not None and price < initial:
                item.market_status = "price_drop"
                price_drops += 1
            else:
                item.market_status = "duplicate" if item.duplicate_count > 0 else "active"

        await session.commit()
    return WatchlistRefreshResponse(
        updated=updated,
        missing=missing,
        price_drops=price_drops,
        skipped=skipped,
    )
