from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.models import LeadItem, WatchlistItem
from api.services.users import (
    MAX_WATCHLIST_ITEMS_PER_USER,
    UserQuotaExceededError,
    count_user_rows,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_LEAD_STATUS = "new"
DEFAULT_LEAD_SOURCE = "manual"
# A repeated "add" (Mini App button, alert button) may move a lead out of these early statuses
# only; it must never downgrade a lead that is already in work, bought or sold.
LEAD_STATUSES_OPEN_TO_UPSERT = frozenset({"new", "reviewing", "deferred", "skipped"})
KUFAR_ITEM_URL = "https://www.kufar.by/item/{ad_id}"


def kufar_item_link(ad_id: int) -> str:
    """Public Kufar page of an ad; used when the client sends no link."""
    return KUFAR_ITEM_URL.format(ad_id=int(ad_id))


def _resolve_link(link: str | None, ad_id: int, current: str | None = None) -> str:
    link = (link or "").strip()
    return link or (current or "").strip() or kufar_item_link(ad_id)


async def _get_lead(session: AsyncSession, user_id: int, ad_id: int) -> LeadItem | None:
    return await session.scalar(
        select(LeadItem).where(LeadItem.user_id == user_id, LeadItem.ad_id == ad_id)
    )


async def _get_watchlist_item(
    session: AsyncSession,
    user_id: int,
    ad_id: int,
) -> WatchlistItem | None:
    return await session.scalar(
        select(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.ad_id == ad_id)
    )


async def upsert_lead(
    session: AsyncSession,
    *,
    user_id: int,
    ad_id: int,
    query: str,
    title: str,
    link: str,
    price_byn: float | None,
    target_resale_byn: float | None = None,
    status: str | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> LeadItem:
    """Create a lead or refresh an existing one (no commit).

    A new lead gets ``status`` (default "new") and ``source`` (default "manual"). For an
    existing lead the listing data is refreshed, but status and source change only when an
    explicit non-default status is given and the lead is still in an early status
    (``LEAD_STATUSES_OPEN_TO_UPSERT``). What the user entered (target resale price, notes) is
    only filled in when still empty: the Mini App sends its market estimate as
    ``target_resale_byn`` on every "add". An empty ``link`` becomes the ad's Kufar page.
    """
    existing = await _get_lead(session, user_id, ad_id)
    if existing is None:
        lead = LeadItem(
            user_id=user_id,
            ad_id=ad_id,
            query=query,
            title=title,
            link=_resolve_link(link, ad_id),
            price_byn=price_byn,
            target_resale_byn=target_resale_byn,
            status=status or DEFAULT_LEAD_STATUS,
            source=source or DEFAULT_LEAD_SOURCE,
            notes=notes,
        )
        try:
            async with session.begin_nested():
                session.add(lead)
            return lead
        except IntegrityError:
            # A concurrent request inserted the same (user_id, ad_id): update that row.
            existing = await _get_lead(session, user_id, ad_id)
            if existing is None:
                raise

    existing.query = query
    existing.title = title
    existing.link = _resolve_link(link, ad_id, existing.link)
    existing.price_byn = price_byn
    if target_resale_byn is not None and existing.target_resale_byn is None:
        existing.target_resale_byn = target_resale_byn
    if notes and not existing.notes:
        existing.notes = notes
    explicit_status = status if status and status != DEFAULT_LEAD_STATUS else None
    if (
        explicit_status is not None
        and existing.status in LEAD_STATUSES_OPEN_TO_UPSERT
        and existing.status != explicit_status
    ):
        existing.status = explicit_status
        if source:
            existing.source = source
    return existing


async def upsert_watchlist(
    session: AsyncSession,
    *,
    user_id: int,
    ad_id: int,
    query: str,
    title: str,
    link: str,
    price_byn: float | None,
    notes: str | None = None,
    max_items: int | None = None,
) -> WatchlistItem:
    """Create or refresh a watchlist item (no commit).

    Raises ``UserQuotaExceededError`` when a new item would exceed ``max_items``
    (default ``MAX_WATCHLIST_ITEMS_PER_USER``). An empty ``link`` becomes the ad's Kufar page.
    """
    existing = await _get_watchlist_item(session, user_id, ad_id)
    if existing is None:
        limit = MAX_WATCHLIST_ITEMS_PER_USER if max_items is None else max_items
        if await count_user_rows(session, WatchlistItem, user_id) >= limit:
            raise UserQuotaExceededError(
                f"В избранном уже {limit} лотов. Удалите ненужные, чтобы добавить новый.",
                limit=limit,
            )
        item = WatchlistItem(
            user_id=user_id,
            ad_id=ad_id,
            query=query,
            title=title,
            link=_resolve_link(link, ad_id),
            initial_price_byn=price_byn,
            current_price_byn=price_byn,
            notes=notes,
            last_seen_at=datetime.now(UTC),
        )
        try:
            async with session.begin_nested():
                session.add(item)
            return item
        except IntegrityError:
            # A concurrent request inserted the same (user_id, ad_id): update that row.
            existing = await _get_watchlist_item(session, user_id, ad_id)
            if existing is None:
                raise

    existing.query = query
    existing.title = title
    existing.link = _resolve_link(link, ad_id, existing.link)
    existing.current_price_byn = price_byn
    existing.last_seen_at = datetime.now(UTC)
    # Notes the user already wrote are never replaced by a repeated "add".
    if notes and not existing.notes:
        existing.notes = notes
    if existing.initial_price_byn is None:
        existing.initial_price_byn = price_byn
    return existing


# --- Time-budgeted loading ----------------------------------------------------------------------

# One budget for every Kufar search of a multi-search request (watchlist refresh, opportunity
# board). Kufar requests share one process-wide pacer, so the time grows with the number of
# searches; the budget keeps the response well below nginx's 60 s proxy timeout.
MULTI_SEARCH_BUDGET_SECONDS = 45.0
# A search is not started with less time than this left: it could not fetch a page.
MIN_SEARCH_BUDGET_SECONDS = 3.0
# Extra wait for a pagination that stops on its own deadline to hand back its partial result.
SEARCH_DEADLINE_GRACE_SECONDS = 2.0


@dataclass(slots=True)
class BudgetedResults[KeyT: Hashable, ResultT]:
    """Outcome of ``gather_within_budget``; every key lands in exactly one bucket."""

    results: dict[KeyT, ResultT] = field(default_factory=dict)
    # Not started, or not finished, within the budget.
    skipped: list[KeyT] = field(default_factory=list)
    failed: list[tuple[KeyT, Exception]] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.skipped or self.failed)


async def gather_within_budget[KeyT: Hashable, ResultT](
    keys: Sequence[KeyT],
    load: Callable[[KeyT, float], Awaitable[ResultT]],
    *,
    concurrency: int,
    budget_seconds: float | None = None,
    min_seconds: float | None = None,
    grace_seconds: float | None = None,
) -> BudgetedResults[KeyT, ResultT]:
    """Run ``load(key, remaining_seconds)`` for every key under one shared time budget.

    Keys start in order with at most ``concurrency`` loads at once. ``load`` gets the time
    left in the budget (pass it on as the pagination deadline); a key that would start with
    less than ``min_seconds`` left, or that runs past the budget, is skipped. Exceptions of
    one key are collected, not raised. The whole call takes at most about
    ``budget_seconds + grace_seconds``. Unset limits use the module constants.
    """
    budget_seconds = MULTI_SEARCH_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    min_seconds = MIN_SEARCH_BUDGET_SECONDS if min_seconds is None else min_seconds
    grace_seconds = SEARCH_DEADLINE_GRACE_SECONDS if grace_seconds is None else grace_seconds
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, budget_seconds)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    outcome: BudgetedResults[KeyT, ResultT] = BudgetedResults()
    over_budget = object()

    async def run(key: KeyT) -> Any:
        async with semaphore:
            remaining = deadline - loop.time()
            if remaining < min_seconds:
                return over_budget
            try:
                return await asyncio.wait_for(load(key, remaining), remaining + grace_seconds)
            except TimeoutError:
                return over_budget

    results = await asyncio.gather(*(run(key) for key in keys), return_exceptions=True)
    for key, result in zip(keys, results, strict=True):
        if result is over_budget:
            outcome.skipped.append(key)
        elif isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result
            outcome.failed.append((key, result))
        else:
            outcome.results[key] = result
    return outcome
