from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from api.models import Base


def test_tracker_table_exists() -> None:
    assert "trackers" in Base.metadata.tables
    assert "query_snapshots" in Base.metadata.tables
    assert "query_listing_states" in Base.metadata.tables
    assert "tracker_events" in Base.metadata.tables
    assert "saved_searches" in Base.metadata.tables
    assert "lead_items" in Base.metadata.tables
    assert "watchlist_items" in Base.metadata.tables


def test_tracker_columns_and_types() -> None:
    table = Base.metadata.tables["trackers"]
    columns = {column.name: column for column in table.columns}
    assert "id" in columns and columns["id"].primary_key
    assert "user_id" in columns and not columns["user_id"].nullable
    assert "query" in columns and columns["query"].type.length == 255
    assert "strict_mode" in columns and columns["strict_mode"].default.arg is False
    assert "interval_min" in columns and columns["interval_min"].default.arg == 15
    assert "min_discount_percent" in columns
    assert "max_price_byn" in columns
    assert "seller_type" in columns
    assert "condition" in columns
    assert "region_name" in columns
    assert "config_keyword" in columns
    assert "exclude_duplicates" in columns
    assert "last_seen_ad_id" in columns and columns["last_seen_ad_id"].nullable
    assert "last_seen_price_byn" in columns and columns["last_seen_price_byn"].nullable
    assert "last_checked_at" in columns
    assert "active" in columns and columns["active"].default.arg is True
    assert "created_at" in columns


def test_tracker_indexes() -> None:
    table = Base.metadata.tables["trackers"]
    index_names = {index.name for index in table.indexes}
    assert "idx_trackers_user" in index_names
    assert "idx_trackers_active" in index_names


def test_tracker_model_defaults() -> None:
    from api.models import Tracker

    tracker = Tracker(user_id=123, query="iPhone 15")
    assert tracker.interval_min == 15
    assert tracker.active is True
    assert tracker.strict_mode is False
    assert tracker.last_seen_ad_id is None
    assert tracker.last_seen_price_byn is None
    assert tracker.last_checked_at is None


def test_history_tables_have_indexes() -> None:
    snapshots = Base.metadata.tables["query_snapshots"]
    snapshot_constraints = {constraint.name for constraint in snapshots.constraints}
    assert "uq_query_snapshot_bucket" in snapshot_constraints

    states = Base.metadata.tables["query_listing_states"]
    state_constraints = {constraint.name for constraint in states.constraints}
    state_indexes = {index.name for index in states.indexes}
    assert "uq_query_listing_state" in state_constraints
    assert "idx_query_listing_states_active" in state_indexes

    tracker_events = Base.metadata.tables["tracker_events"]
    tracker_event_indexes = {index.name for index in tracker_events.indexes}
    assert "idx_tracker_events_user_created" in tracker_event_indexes
    assert "idx_tracker_events_created" in tracker_event_indexes
    assert "idx_tracker_events_tracker_id" in tracker_event_indexes
    tracker_event_columns = {column.name for column in tracker_events.columns}
    assert "ad_id" in tracker_event_columns

    saved_searches = Base.metadata.tables["saved_searches"]
    saved_search_indexes = {index.name for index in saved_searches.indexes}
    assert "idx_saved_searches_user" in saved_search_indexes
    assert "idx_saved_searches_active" in saved_search_indexes
    assert "idx_saved_searches_user_active" in saved_search_indexes
    saved_search_columns = {column.name for column in saved_searches.columns}
    assert "group_name" in saved_search_columns
    assert "exclude_duplicates" in saved_search_columns
    assert "min_discount_percent" in saved_search_columns

    lead_items = Base.metadata.tables["lead_items"]
    lead_item_indexes = {index.name for index in lead_items.indexes}
    lead_item_constraints = {constraint.name for constraint in lead_items.constraints}
    assert "idx_lead_items_user_status" in lead_item_indexes
    assert "idx_lead_items_status" in lead_item_indexes
    assert "uq_lead_items_user_ad" in lead_item_constraints

    watchlist_items = Base.metadata.tables["watchlist_items"]
    watchlist_item_indexes = {index.name for index in watchlist_items.indexes}
    watchlist_item_constraints = {constraint.name for constraint in watchlist_items.constraints}
    assert "idx_watchlist_items_user_market" in watchlist_item_indexes
    assert "idx_watchlist_items_market_status" in watchlist_item_indexes
    assert "uq_watchlist_items_user_ad" in watchlist_item_constraints


def test_no_redundant_indexes() -> None:
    # Each index must not be a duplicate or a column prefix of a unique constraint or of
    # another full (non-partial) index on the same table.
    for table in Base.metadata.tables.values():
        covering: list[tuple[str | None, list[str]]] = [
            (constraint.name, [column.name for column in constraint.columns])
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        covering += [
            (index.name, [column.name for column in index.columns])
            for index in table.indexes
            if index.dialect_options["postgresql"].get("where") is None
        ]
        for index in table.indexes:
            columns = [column.name for column in index.columns]
            for name, candidate in covering:
                if name == index.name:
                    continue
                assert candidate[: len(columns)] != columns, (
                    f"{table.name}.{index.name} is covered by {name}"
                )


def test_users_have_a_single_unique_telegram_id_index() -> None:
    users = Base.metadata.tables["users"]
    indexes = [index for index in users.indexes if "telegram_user_id" in index.columns]
    assert [(index.name, index.unique) for index in indexes] == [
        ("ix_users_telegram_user_id", True)
    ]


@pytest.mark.parametrize(
    "table_name",
    ["trackers", "saved_searches", "tracker_events", "lead_items", "watchlist_items"],
)
def test_user_id_references_telegram_user_id(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    foreign_keys = list(table.columns["user_id"].foreign_keys)
    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    assert foreign_key.target_fullname == "users.telegram_user_id"
    assert foreign_key.ondelete == "CASCADE"
    assert foreign_key.constraint.name == f"fk_{table_name}_user_id"
    # Plain index=True would add an ix_* duplicate of the named indexes.
    assert not table.columns["user_id"].index


def test_user_model_has_no_orm_relationships() -> None:
    from api.models import User

    assert list(sa_inspect(User).relationships) == []


def test_status_check_constraints_use_canonical_lists() -> None:
    from api.models import LEAD_STATUSES, MARKET_STATUSES, WATCHLIST_STATUSES

    assert LEAD_STATUSES == (
        "new",
        "reviewing",
        "in_progress",
        "negotiating",
        "deferred",
        "bought",
        "reselling",
        "sold",
        "skipped",
    )
    assert WATCHLIST_STATUSES == ("watching", "reviewing", "in_progress", "deferred", "skipped")
    assert MARKET_STATUSES == ("active", "missing", "price_drop", "duplicate")

    def checks(table_name: str) -> dict[str, str]:
        return {
            constraint.name: str(constraint.sqltext)
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint)
        }

    lead_checks = checks("lead_items")
    assert lead_checks["chk_lead_items_status"] == (
        "status IN (" + ", ".join(f"'{value}'" for value in LEAD_STATUSES) + ")"
    )
    watchlist_checks = checks("watchlist_items")
    assert "'skipped'" in watchlist_checks["chk_watchlist_items_workflow_status"]
    assert "'lead'" not in watchlist_checks["chk_watchlist_items_workflow_status"]
    assert "'duplicate'" in watchlist_checks["chk_watchlist_items_market_status"]
    assert "chk_tracker_events_event_type" in checks("tracker_events")


async def test_status_check_rejects_legacy_values() -> None:
    from api.database import get_engine, get_session_factory
    from api.models import LeadItem, WatchlistItem
    from api.services.users import ensure_user

    engine = get_engine()
    session_factory = get_session_factory(engine)
    try:
        async with session_factory() as session:
            await ensure_user(session, 1)
            session.add(
                LeadItem(user_id=1, ad_id=1, query="q", title="t", link="l", status="bought")
            )
            session.add(
                WatchlistItem(
                    user_id=1,
                    ad_id=1,
                    query="q",
                    title="t",
                    link="l",
                    workflow_status="in_progress",
                )
            )
            await session.commit()

        async with session_factory() as session:
            session.add(
                LeadItem(user_id=1, ad_id=2, query="q", title="t", link="l", status="closed")
            )
            with pytest.raises(IntegrityError, match="CHECK constraint failed"):
                await session.commit()
    finally:
        await engine.dispose()


async def test_sqlite_engine_enforces_foreign_keys() -> None:
    from sqlalchemy import text

    from api.database import get_engine, get_session_factory
    from api.models import Tracker, TrackerEvent, User

    engine = get_engine()
    session_factory = get_session_factory(engine)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1

        async with session_factory() as session:
            session.add(Tracker(user_id=404, query="iphone"))
            with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
                await session.commit()

        # ON DELETE CASCADE works too: deleting the user removes its trackers and events.
        async with session_factory() as session:
            session.add(User(telegram_user_id=7, first_name="Test"))
            await session.flush()
            tracker = Tracker(user_id=7, query="iphone")
            session.add(tracker)
            await session.flush()
            session.add(
                TrackerEvent(
                    tracker_id=tracker.id,
                    user_id=7,
                    ad_id=1,
                    query="iphone",
                    strict_mode=False,
                    event_type="new_listing",
                    title="iPhone",
                    link="https://www.kufar.by/item/1",
                    price_byn=100,
                )
            )
            await session.commit()
            await session.execute(text("DELETE FROM users WHERE telegram_user_id = 7"))
            await session.commit()
            trackers = await session.scalar(text("SELECT count(*) FROM trackers"))
            events = await session.scalar(text("SELECT count(*) FROM tracker_events"))
            assert (trackers, events) == (0, 0)
    finally:
        await engine.dispose()


def test_get_engine_returns_async_engine() -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from api.database import get_engine

    engine = get_engine("sqlite+aiosqlite:///test.db")
    assert isinstance(engine, AsyncEngine)


def test_get_session_factory_returns_async_sessionmaker() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.database import get_engine, get_session_factory

    engine = get_engine("sqlite+aiosqlite:///test.db")
    assert isinstance(get_session_factory(engine), async_sessionmaker)
