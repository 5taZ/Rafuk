from __future__ import annotations

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
    assert "idx_query_listing_states_query" in state_indexes
    assert "idx_query_listing_states_active" in state_indexes

    tracker_events = Base.metadata.tables["tracker_events"]
    tracker_event_indexes = {index.name for index in tracker_events.indexes}
    assert "idx_tracker_events_user" in tracker_event_indexes
    assert "idx_tracker_events_created" in tracker_event_indexes
    tracker_event_columns = {column.name for column in tracker_events.columns}
    assert "ad_id" in tracker_event_columns

    saved_searches = Base.metadata.tables["saved_searches"]
    saved_search_indexes = {index.name for index in saved_searches.indexes}
    assert "idx_saved_searches_user" in saved_search_indexes
    assert "idx_saved_searches_active" in saved_search_indexes
    saved_search_columns = {column.name for column in saved_searches.columns}
    assert "group_name" in saved_search_columns
    assert "exclude_duplicates" in saved_search_columns

    lead_items = Base.metadata.tables["lead_items"]
    lead_item_indexes = {index.name for index in lead_items.indexes}
    lead_item_constraints = {constraint.name for constraint in lead_items.constraints}
    assert "idx_lead_items_user" in lead_item_indexes
    assert "idx_lead_items_status" in lead_item_indexes
    assert "uq_lead_items_user_ad" in lead_item_constraints

    watchlist_items = Base.metadata.tables["watchlist_items"]
    watchlist_item_indexes = {index.name for index in watchlist_items.indexes}
    watchlist_item_constraints = {constraint.name for constraint in watchlist_items.constraints}
    assert "idx_watchlist_items_user" in watchlist_item_indexes
    assert "idx_watchlist_items_market_status" in watchlist_item_indexes
    assert "uq_watchlist_items_user_ad" in watchlist_item_constraints


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
