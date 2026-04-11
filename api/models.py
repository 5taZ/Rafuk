from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarative model."""


class User(Base):
    """Telegram users registered in the system."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_bot: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    trackers = relationship(
        "Tracker", back_populates="user", cascade="all, delete-orphan"
    )
    saved_searches = relationship(
        "SavedSearch", back_populates="user", cascade="all, delete-orphan"
    )
    tracker_events = relationship(
        "TrackerEvent", back_populates="user", cascade="all, delete-orphan"
    )
    lead_items = relationship(
        "LeadItem", back_populates="user", cascade="all, delete-orphan"
    )
    watchlist_items = relationship(
        "WatchlistItem", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_users_telegram_id", "telegram_user_id"),
    )


class UserIDMixin:
    """Mixin for models that have a user_id field with FK to users table."""

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class ActiveMixin:
    """Mixin for models that have an active flag."""

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TimestampMixin:
    """Mixin for models that have created_at timestamp."""

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class QueryTrackingMixin:
    """Mixin for models that track listings by query."""

    query: Mapped[str] = mapped_column(String(255), nullable=False)
    strict_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )


class TrackerFiltersMixin:
    """Mixin for tracker filtering configuration."""

    min_discount_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    seller_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_keyword: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exclude_duplicates: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )


class Tracker(
    Base,
    UserIDMixin,
    QueryTrackingMixin,
    TrackerFiltersMixin,
    ActiveMixin,
    TimestampMixin,
):
    __tablename__ = "trackers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interval_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=15,
        server_default="15",
    )
    last_seen_ad_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seen_price_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Pause support
    paused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    pause_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="trackers")
    events = relationship("TrackerEvent", back_populates="tracker", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_trackers_user", "user_id"),
        Index("idx_trackers_active", "active"),
        Index("idx_trackers_paused", "paused"),
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("interval_min", 15)
        kwargs.setdefault("active", True)
        kwargs.setdefault("strict_mode", False)
        kwargs.setdefault("paused", False)
        super().__init__(**kwargs)


class SavedSearch(
    Base,
    UserIDMixin,
    QueryTrackingMixin,
    TrackerFiltersMixin,
    ActiveMixin,
    TimestampMixin,
):
    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_discount_percent: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=10.0,
        server_default="10",
    )

    # Relationships
    user = relationship("User", back_populates="saved_searches")

    __table_args__ = (
        Index("idx_saved_searches_user", "user_id"),
        Index("idx_saved_searches_active", "active"),
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("strict_mode", False)
        kwargs.setdefault("target_discount_percent", 10.0)
        kwargs.setdefault("active", True)
        super().__init__(**kwargs)


class QuerySnapshot(Base, TimestampMixin):
    __tablename__ = "query_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_results: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mean_byn: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    median_byn: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    min_byn: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_byn: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("query", "snapshot_at", name="uq_query_snapshot_bucket"),
        Index("idx_query_snapshots_query", "query"),
    )


class QueryListingState(Base):
    __tablename__ = "query_listing_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str] = mapped_column(String(512), nullable=False)
    last_price_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    list_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("query", "ad_id", name="uq_query_listing_state"),
        Index("idx_query_listing_states_query", "query"),
        Index("idx_query_listing_states_active", "active"),
    )


class TrackerEvent(Base, UserIDMixin):
    __tablename__ = "tracker_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracker_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trackers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ad_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    strict_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str] = mapped_column(String(512), nullable=False)
    price_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Enriched metadata
    thumbnail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parameters: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)
    seller_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    user = relationship("User", back_populates="tracker_events")
    tracker = relationship("Tracker", back_populates="events")

    __table_args__ = (
        Index("idx_tracker_events_user", "user_id"),
        Index("idx_tracker_events_created", "created_at"),
    )


class LeadItem(Base, UserIDMixin, TimestampMixin):
    __tablename__ = "lead_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str] = mapped_column(String(512), nullable=False)
    price_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_price_byn: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    sold_price_byn: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    target_resale_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="new",
        server_default="new",
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",
        server_default="manual",
    )
    thumbnail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default="active",
    )
    missing_since_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    user = relationship("User", back_populates="lead_items")
    expenses = relationship("DealExpense", back_populates="lead", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "ad_id", name="uq_lead_items_user_ad"),
        Index("idx_lead_items_user", "user_id"),
        Index("idx_lead_items_status", "status"),
        Index("idx_lead_items_market_status", "market_status"),
    )


class WatchlistItem(Base, UserIDMixin, TimestampMixin):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    initial_price_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    workflow_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="default",
        server_default="default",
    )
    market_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default="active",
    )
    duplicate_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    market_median_byn: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missing_since_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    user = relationship("User", back_populates="watchlist_items")

    __table_args__ = (
        UniqueConstraint("user_id", "ad_id", name="uq_watchlist_items_user_ad"),
        Index("idx_watchlist_items_user", "user_id"),
        Index("idx_watchlist_items_market_status", "market_status"),
    )


class DealExpense(Base):
    """Expense tracking for deals (delivery, repair, etc.)."""

    __tablename__ = "deal_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("lead_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expense_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_byn: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expense_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    lead = relationship("LeadItem", back_populates="expenses")
    user = relationship("User")

    __table_args__ = (
        Index("idx_deal_expenses_lead", "lead_id"),
        Index("idx_deal_expenses_user", "user_id"),
    )


class Contact(Base):
    """Seller contacts extracted from listings."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kufar_profile: Mapped[str | None] = mapped_column(String(512), nullable=True)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("user_id", "phone", name="uq_contacts_user_phone"),
        Index("idx_contacts_user", "user_id"),
    )
