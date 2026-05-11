from __future__ import annotations

from sqlalchemy.sql.elements import TextClause

from api.models import Base


def test_tracker_table_exists() -> None:
    assert "trackers" in Base.metadata.tables
    assert "query_snapshots" in Base.metadata.tables
    assert "query_listing_states" in Base.metadata.tables
    assert "tracker_events" in Base.metadata.tables
    assert "lead_items" in Base.metadata.tables
    # `watchlist_items` was merged into `lead_items` (status='watching')
    # in migration 20260427_0001.
    assert "watchlist_items" not in Base.metadata.tables


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
    assert "idx_trackers_active_paused" in index_names


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
    # DB-M4: the standalone ``idx_query_listing_states_active`` was
    # dropped in migration 20260510_0006 (boolean-only index, Postgres
    # would seq scan anyway). The compound
    # ``idx_query_listing_states_query_active`` still covers every
    # query that filters on ``active``.
    assert "idx_query_listing_states_query_active" in state_indexes
    assert "idx_query_listing_states_active" not in state_indexes

    tracker_events = Base.metadata.tables["tracker_events"]
    tracker_event_indexes = {index.name for index in tracker_events.indexes}
    assert "idx_tracker_events_user" in tracker_event_indexes
    assert "idx_tracker_events_created" in tracker_event_indexes
    tracker_event_columns = {column.name for column in tracker_events.columns}
    assert "ad_id" in tracker_event_columns

    lead_items = Base.metadata.tables["lead_items"]
    lead_item_indexes = {index.name for index in lead_items.indexes}
    lead_item_constraints = {constraint.name for constraint in lead_items.constraints}
    assert "idx_lead_items_user" in lead_item_indexes
    # DB-M4: the single-column ``idx_lead_items_status`` was dropped in
    # migration 20260510_0006 as redundant with the compound
    # (user_id, status) index below. Assert the compound is still there
    # so accidental removal can't slip through on a future refactor.
    assert "idx_lead_items_user_status" in lead_item_indexes
    assert "idx_lead_items_status" not in lead_item_indexes
    assert "idx_lead_items_market_status" not in lead_item_indexes
    assert "uq_lead_items_user_ad" in lead_item_constraints
    # Watchlist-merged columns live on lead_items now.
    lead_item_columns = {column.name for column in lead_items.columns}
    for col in (
        "initial_price_byn",
        "market_median_byn",
        "duplicate_count",
        "last_seen_at",
        "notes",
        "version",
    ):
        assert col in lead_item_columns, f"missing watchlist-merged column: {col}"
    assert "chk_lead_items_version_positive" in lead_item_constraints


def test_user_consents_version_constraint() -> None:
    consents = Base.metadata.tables["user_consents"]
    constraint_names = {constraint.name for constraint in consents.constraints}
    assert "chk_user_consents_version_current" in constraint_names


def test_telegram_notification_dlq_table_shape() -> None:
    dlq = Base.metadata.tables["telegram_notification_dlq"]
    assert "telegram_user_id" in dlq.columns
    assert "source" in dlq.columns
    assert "message" in dlq.columns
    indexes = {index.name for index in dlq.indexes}
    assert "idx_telegram_notification_dlq_created" in indexes
    assert "idx_telegram_notification_dlq_user" in indexes


def test_model_indexes_do_not_use_text_clause_expressions() -> None:
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            assert not any(isinstance(expr, TextClause) for expr in index.expressions)
            for dialect_options in index.dialect_options.values():
                where = dialect_options.get("where")
                assert not isinstance(where, TextClause)


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
