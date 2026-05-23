from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, insert, select
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
    LeadStatusEnum,
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
    prune_price_snapshots,
    record_price_snapshot,
    resolve_user_id,
    upsert_lead,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflow"])

WATCHING_STATUS = "watching"

_REFRESH_SEMAPHORE = asyncio.Semaphore(3)


# LOGIC-HIGH (issues §11.4): explicit lead-status state machine.
#
# The previous update_lead handler accepted any LeadStatusEnum value and
# applied it directly to ``lead.status``, which let nonsense transitions
# slip through ("sold" → "new", "watching" → "sold" without an
# intermediate buying state, etc.). The map below codifies the
# transitions the product actually supports; everything not listed
# raises 422.
#
# Design notes:
#   * ``sold`` is reachable only via ``bought`` / ``negotiating`` /
#     ``in_progress`` / ``reviewing`` / ``new`` — i.e. states that
#     represent an active deal. The "un-sell" reversion (sold →
#     bought, triggered when payload.sold_price_byn is None) is
#     handled before this guard runs.
#   * ``watching`` (legacy watchlist surface) only graduates into the
#     deal-pipeline lanes; it can never jump straight to ``sold`` /
#     ``bought`` / ``closed``.
#   * Terminal-ish states (``closed``, ``abandoned``) accept a small
#     set of "reactivate" transitions so an accidental abandonment
#     can be undone.
_ANY_ACTIVE = {
    "new", "reviewing", "in_progress", "researching",
    "negotiating", "deferred",
}
# Active deal lanes (everything except the watchlist surface and the
# terminal/exit states) can transition to "bought" or "sold" — the
# user might record the buy and the resell in the same edit if it's
# a fast flip. The audit's concern was the broken paths "watching →
# sold" (which still requires graduating through new/reviewing first)
# and "sold → new" (which is blocked by the empty allowed-set for
# sold).
_LEAD_STATUS_TRANSITIONS: dict[str, set[str]] = {
    # E-FIND-06: watching→bought allowed — user can buy directly from watchlist.
    # Wave 6 already stamps bought_at on the transition.
    "watching": _ANY_ACTIVE | {"bought", "skipped", "abandoned"},
    "new": _ANY_ACTIVE | {"bought", "sold", "skipped", "abandoned", "closed"},
    "reviewing": _ANY_ACTIVE | {"bought", "sold", "skipped", "abandoned", "closed"},
    "in_progress": _ANY_ACTIVE | {"bought", "sold", "skipped", "abandoned", "closed"},
    "researching": _ANY_ACTIVE | {"bought", "sold", "skipped", "abandoned", "closed"},
    "negotiating": _ANY_ACTIVE | {"bought", "sold", "skipped", "abandoned", "closed"},
    "deferred": _ANY_ACTIVE | {"bought", "sold", "skipped", "abandoned", "closed"},
    "bought": {"sold", "abandoned", "closed", "deferred"},
    # ``sold`` was originally modelled as terminal but the deal flow
    # archives sold deals via ``closed`` for finance reporting (see
    # delete_all_leads which preserves both statuses), so allow that
    # one outbound edge. Reverting "sold" via the un-sell path
    # (clearing sold_price_byn) bypasses this map entirely.
    "sold": {"closed"},
    "skipped": {"new", "reviewing", "abandoned"},
    "abandoned": {"new", "reviewing"},
    "closed": {"reviewing"},  # reopen for review only
}


def _validate_lead_status_transition(current: str, target: str) -> None:
    """Reject a non-allowed lead-status transition with HTTP 422.

    No-op when ``current == target``. Unknown current statuses are
    treated as permissive (we don't want a stale enum to lock a user
    out of fixing their lead).
    """
    if current == target:
        return
    allowed = _LEAD_STATUS_TRANSITIONS.get(current)
    if allowed is None:
        return
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Lead status transition '{current}' → '{target}' is not "
                "allowed by the deal pipeline state machine"
            ),
        )


def _check_lead_version(
    item: LeadItem,
    expected: int | None,
) -> None:
    """Hard optimistic-locking check.

    OPUS-7: the older signature took ``allow_missing`` and skipped
    enforcement when the request had ``X-Requested-With:
    XMLHttpRequest`` — which the mini-app sets on every mutating
    call. That made the version check effectively optional for the
    primary client, so two open tabs could overwrite each other.
    Today both api_leads.js and app_actions.js resolve the version
    before every PATCH; missing version means a stale bundle that
    pre-dates that fix, and we'd rather surface the precondition
    error than silently let the client clobber data.
    """
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Lead version is required for updates",
        )
    if item.version != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Lead was updated elsewhere; reload and retry",
        )


def _bump_lead_version(item: LeadItem) -> None:
    item.version = int(item.version or 1) + 1


def _compute_hold_time_days(lead: LeadItem) -> int | None:
    """E-FIND-02: integer days the lead has been (or was) held.

    Returns ``None`` for leads that never went through the explicit
    ``* → bought`` transition (rows pre-dating the migration, or
    leads still in watching/reviewing). For sold leads we measure
    bought→sold; for active bought leads we measure bought→now.
    """
    if lead.bought_at is None:
        return None
    # SQLite returns DateTime(timezone=True) as naive — coerce to UTC
    # so the subtraction below doesn't raise. Postgres returns aware
    # datetimes already; this is a no-op there.
    bought_at = lead.bought_at
    if bought_at.tzinfo is None:
        bought_at = bought_at.replace(tzinfo=UTC)
    end = lead.sold_at if lead.sold_at is not None else datetime.now(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    delta = end - bought_at
    total_seconds = delta.total_seconds()
    # M14: instant flip (bought_at == sold_at, same second) returns None
    # to signal "instant flip" rather than the confusing "0 days held".
    # Only applies when sold_at is set — active bought leads still show 0.
    if lead.sold_at is not None and total_seconds < 1.0:
        return None
    return max(0, int(total_seconds // 86400))


def _serialize_lead_read(
    lead: LeadItem,
    *,
    total_expenses: float = 0.0,
    actual_profit: float | None = None,
    roi_percent: float | None = None,
) -> LeadRead:
    """Single seam for building LeadRead so computed fields stay
    consistent across list/create/update handlers."""
    out = LeadRead.model_validate(lead)
    out.total_expenses = total_expenses
    out.actual_profit = actual_profit
    out.roi_percent = roi_percent
    out.hold_time_days = _compute_hold_time_days(lead)
    out.is_sold = lead.sold_price_byn is not None or lead.status == "sold"
    # E-FIND-09: flag incomplete cost basis when sold without buy_price.
    if lead.sold_price_byn is not None and lead.buy_price_byn is None:
        out.incomplete_cost_basis = True
        out.actual_profit = None
        out.roi_percent = None
    # E-FIND-01: projected profit for unsold leads with target_resale_byn.
    if lead.sold_price_byn is None and lead.target_resale_byn is not None:
        # LOGIC-NEW-8: refuse to project profit when the buy-price basis is unknown.
        if lead.buy_price_byn is None:
            out.projected_profit_byn = None
            out.incomplete_projection = True
        else:
            basis = float(lead.buy_price_byn) + total_expenses
            out.projected_profit_byn = round(float(lead.target_resale_byn) - basis, 2)
    return out


async def _limited_query(coro):
    async with _REFRESH_SEMAPHORE:
        return await coro


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
            # L7: cap extreme delta_percent from tiny initial prices so
            # the frontend doesn't render absurd values like +500000%.
            if delta_percent is not None and abs(delta_percent) > 10000:
                delta_percent = 9999.0 if delta_percent > 0 else -9999.0
    # A-4: dropped user_id= kwarg — WatchlistRead has no such field;
    # Pydantic v2 silently ignores extras but would break under extra='forbid'.
    return WatchlistRead(
        id=item.id,
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
        version=item.version,
        price_history=price_history or [],
    )


@router.get("/leads", response_model=list[LeadRead])
@limiter.limit("30/minute")
async def get_leads(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    limit: int = Query(default=50, ge=1, le=200),
    # BE-MEDIUM (issues §2.2): cap to avoid full-table scans on absurd offsets.
    offset: int = Query(default=0, ge=0, le=10_000),
) -> list[LeadRead]:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return []
        # BE-H8: single LEFT JOIN with sum(expenses) replaces the prior
        # two-query pattern (leads then expenses with `IN (lead_ids)`).
        # The old approach forced a second round-trip for every page
        # AND fanned the IN-list to ~50 ids on every request — Postgres
        # planning that turned into a hash join under load. Pushing the
        # SUM into the same SELECT cuts the call count to one and lets
        # the planner use the existing index on (lead_id) directly.
        expense_totals = (
            select(
                DealExpense.lead_id.label("lead_id"),
                func.coalesce(func.sum(DealExpense.amount_byn), 0).label("total"),
            )
            .group_by(DealExpense.lead_id)
            .subquery()
        )
        # Exclude `watching` items here — those are exposed via /watchlist
        # endpoints to keep the frontend contract unchanged.
        result = await session.execute(
            select(
                LeadItem,
                func.coalesce(expense_totals.c.total, 0).label("total_expenses"),
            )
            .outerjoin(expense_totals, LeadItem.id == expense_totals.c.lead_id)
            .where(LeadItem.user_id == user_id, LeadItem.status != WATCHING_STATUS)
            .order_by(LeadItem.updated_at.desc(), LeadItem.id.desc())
            .limit(limit)
            .offset(offset)
        )

        # Build response with computed fields
        output: list[LeadRead] = []
        for lead, total_expenses_raw in result.all():
            total_expenses = float(total_expenses_raw or 0.0)
            actual_profit: float | None = None
            roi_percent: float | None = None

            if lead.sold_price_byn is not None:
                sold_price = float(lead.sold_price_byn)
                # E-FIND-09: only compute profit/ROI when buy_price is known.
                if lead.buy_price_byn is not None:
                    buy_price = float(lead.buy_price_byn)
                    total_cost = buy_price + total_expenses
                    actual_profit = round(sold_price - total_cost, 2)
                    if total_cost > 0:
                        roi_percent = round((actual_profit / total_cost) * 100, 2)

            lead_read = _serialize_lead_read(
                lead,
                total_expenses=total_expenses,
                actual_profit=actual_profit,
                roi_percent=roi_percent,
            )

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
        # this ad, return 409 so the frontend can refresh button state.
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
        return _serialize_lead_read(lead)


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
        _check_lead_version(lead, payload.version)
        # BE-DEEP-10: explicit ``status: null`` would crash on .value;
        # treat null as no-op for the status field.
        if "status" in payload.model_fields_set and payload.status is not None:
            _validate_lead_status_transition(lead.status, payload.status.value)
            lead.status = payload.status.value
            # E-FIND-02: stamp bought_at on the first transition into
            # ``bought``. We never overwrite an existing value — the
            # user might have manually corrected the date elsewhere
            # in the future, and round-tripping through bought →
            # bought (idempotent) shouldn't reset the clock.
            if lead.status == "bought" and lead.bought_at is None:
                lead.bought_at = datetime.now(UTC)
        if "target_resale_byn" in payload.model_fields_set:
            lead.target_resale_byn = payload.target_resale_byn
        if "buy_price_byn" in payload.model_fields_set:
            lead.buy_price_byn = payload.buy_price_byn
        if "notes" in payload.model_fields_set:
            # A-1: schema declared ``notes`` as updatable but the
            # handler used to silently drop it. PATCH /leads/{id}
            # now mirrors PATCH /watchlist/{id} for the notes field.
            lead.notes = payload.notes
        if "sold_price_byn" in payload.model_fields_set:
            # LOGIC-MEDIUM (issues §11.4): once a lead is sold, refuse
            # to overwrite ``sold_price_byn`` with a different value —
            # the old impl let the field be re-set repeatedly, silently
            # corrupting the profit calculation. Setting None still
            # works (it triggers the "un-sell" reversion below) and
            # the same value is idempotent.
            if (
                lead.status == "sold"
                and payload.sold_price_byn is not None
                and lead.sold_price_byn is not None
                and float(payload.sold_price_byn) != float(lead.sold_price_byn)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Lead is already sold; clear sold_price_byn to "
                        "first revert to 'bought' before recording a "
                        "different sale price."
                    ),
                )
            lead.sold_price_byn = payload.sold_price_byn
            if payload.sold_price_byn is not None and lead.status != "sold":
                _validate_lead_status_transition(lead.status, "sold")
                lead.status = "sold"
                lead.sold_at = datetime.now(UTC)
                # E-FIND-02: if the lead transitions straight from
                # an active status into ``sold`` without ever passing
                # through ``bought``, retroactively stamp bought_at
                # at the same moment so hold_time_days renders as 0
                # rather than NULL. Skipping the bought stage is
                # rare but legitimate (e.g. quick flip).
                if lead.bought_at is None:
                    lead.bought_at = lead.sold_at
            elif payload.sold_price_byn is None and lead.status == "sold":
                # BE-M10: revert un-sold deal to a real LeadStatusEnum value.
                # The previous "active" string was not in the enum and would
                # fail any downstream consumer that round-trips through the
                # Pydantic schema. The user has bought the item but no longer
                # has a sale recorded, so "bought" is the correct pre-sold
                # state — frontend render_card_builders.js treats new/bought
                # symmetrically when computing deal potential.
                lead.status = LeadStatusEnum.bought.value
                lead.sold_at = None
                # E-FIND-02: rollback path — if the lead never had
                # bought_at set (e.g. it went watching → reviewing →
                # sold without an explicit bought transition), stamp
                # it now so hold_time is at least computable from
                # this point forward.
                if lead.bought_at is None:
                    lead.bought_at = datetime.now(UTC)
        _bump_lead_version(lead)
        await session.commit()
        await session.refresh(lead)  # Refresh to get server-generated updated_at
        return _serialize_lead_read(lead)


@router.delete("/leads/all", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_all_leads(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    # BE-DEEP-9: bulk-delete race window is bounded by DB CASCADE +
    # UNIQUE constraints; document as accept-by-design.
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
    # BE-MEDIUM (issues §2.2): cap to avoid full-table scans on absurd offsets.
    offset: int = Query(default=0, ge=0, le=10_000),
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
        # "add to watchlist" with a 409 so the frontend can refresh
        # button state instead of silently no-op'ing.
        # BE-DEEP-8: serialize concurrent POSTs for the same (user_id, ad_id)
        # so the 409 contract holds.
        existing = await session.scalar(
            select(LeadItem).where(
                LeadItem.user_id == user_id,
                LeadItem.ad_id == payload.ad_id,
            ).with_for_update()
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
        # G-01: row lock to enforce optimistic locking against concurrent
        # PATCHes that share the same version.
        item = await session.scalar(
            select(LeadItem).where(
                LeadItem.id == watchlist_id,
                LeadItem.user_id == user_id,
                LeadItem.status == WATCHING_STATUS,
            ).with_for_update()
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Watchlist item not found",
            )
        _check_lead_version(item, payload.version)
        # `workflow_status` is intentionally a no-op now (priority chip removed
        # from the UI). Notes update is the only real mutation.
        if "notes" in payload.model_fields_set:
            item.notes = payload.notes
            _bump_lead_version(item)
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
    # BE-DEEP-9: bulk-delete race window is bounded by DB CASCADE +
    # UNIQUE constraints; document as accept-by-design.
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

        item_ids = [item.id for item in items if item.id is not None]
        unique_queries = list(dict.fromkeys(item.query for item in items))

        # Bulk-load the last snapshot price for every watched item in
        # one query — avoids an N+1 inside record_price_snapshot below.
        # Items without a snapshot row aren't in the dict; we treat
        # those as "always record" by passing -1 as the sentinel.
        last_prices = await load_last_snapshot_prices(session, item_ids)

    # Batch Kufar queries outside any DB transaction/connection hold.
    datasets = await asyncio.gather(
        *[_limited_query(
            load_query_dataset(
                query=q,
                currency="BYN",
                strict_search=False,
                settings=settings,
                client=kufar_client,
            )
        ) for q in unique_queries],
        return_exceptions=True,
    )
    datasets_by_query = dict(zip(unique_queries, datasets, strict=True))

    async with session_factory() as session, session.begin():
        result = await session.execute(
            select(LeadItem).where(
                LeadItem.id.in_(item_ids),
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

        updated = 0
        missing = 0
        price_drops = 0
        snapshots_to_insert: list[dict[str, Any]] = []
        for query, query_items in grouped.items():
            dataset = datasets_by_query.get(query)
            if isinstance(dataset, Exception):
                logger.warning("Watchlist refresh: query %r failed: %s", query, dataset)
                continue
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
                if item.version is not None:
                    _bump_lead_version(item)

        if snapshots_to_insert:
            await session.execute(insert(LeadItemPriceSnapshot).values(snapshots_to_insert))
        await prune_price_snapshots(
            session, [item.id for item in items if item.id is not None]
        )

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

        lead_ids = [lead.id for lead in leads if lead.id is not None]
        unique_queries = list(dict.fromkeys(lead.query for lead in leads))

    # Batch Kufar queries outside any DB transaction/connection hold.
    datasets = await asyncio.gather(
        *[_limited_query(
            load_query_dataset(
                query=q,
                currency="BYN",
                strict_search=False,
                settings=settings,
                client=kufar_client,
            )
        ) for q in unique_queries],
        return_exceptions=True,
    )
    datasets_by_query = dict(zip(unique_queries, datasets, strict=True))

    async with session_factory() as session, session.begin():
        result = await session.execute(
            select(LeadItem).where(
                LeadItem.id.in_(lead_ids),
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

        checked = 0
        active_count = 0
        missing_count = 0
        for query, query_leads in grouped.items():
            dataset = datasets_by_query.get(query)
            if isinstance(dataset, Exception):
                logger.warning("Leads refresh: query %r failed: %s", query, dataset)
                continue
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

        return LeadsRefreshResponse(checked=checked, active=active_count, missing=missing_count)
