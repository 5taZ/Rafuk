from __future__ import annotations

from api.models import Base


def test_tracker_table_exists() -> None:
    assert "trackers" in Base.metadata.tables


def test_tracker_columns_and_types() -> None:
    table = Base.metadata.tables["trackers"]
    columns = {column.name: column for column in table.columns}
    assert "id" in columns and columns["id"].primary_key
    assert "user_id" in columns and not columns["user_id"].nullable
    assert "query" in columns and columns["query"].type.length == 255
    assert "interval_min" in columns and columns["interval_min"].default.arg == 15
    assert "last_seen_ad_id" in columns and columns["last_seen_ad_id"].nullable
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
    assert tracker.last_seen_ad_id is None


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
