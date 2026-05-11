from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarative model."""


class User(Base):
    """Telegram users registered in the system."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
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
    trackers = relationship("Tracker", back_populates="user", cascade="all, delete-orphan")
    tracker_events = relationship(
        "TrackerEvent", back_populates="user", cascade="all, delete-orphan"
    )
    lead_items = relationship("LeadItem", back_populates="user", cascade="all, delete-orphan")
    consents = relationship("UserConsent", back_populates="user", cascade="all, delete-orphan")
    reminders = relationship("LeadReminder", back_populates="user", cascade="all, delete-orphan")
    ai_audit_logs = relationship("AIAuditLog", back_populates="user", cascade="all, delete-orphan")
    saved_searches = relationship("SavedSearch", back_populates="user", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_users_telegram_id", "telegram_user_id"),)


class UserIDMixin:
    """Mixin for models that have a user_id field with FK to users table."""

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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

    min_discount_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    max_price_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
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
    alert_price_threshold: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    alert_discount_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)


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
    # NOTE: onupdate only fires on ORM-level attribute changes.
    # Bulk updates via session.execute(update(...)) will NOT trigger this.
    # For bulk updates, set updated_at=datetime.now(UTC) explicitly.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    interval_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=15,
        server_default="15",
    )
    last_seen_ad_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seen_price_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
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
        Index("idx_trackers_paused", "paused"),
        Index("idx_trackers_user_active", "user_id", "active"),
        Index("idx_trackers_user_active_partial", "user_id", "active", postgresql_where=text("active = true")),
        Index("idx_trackers_last_checked", "last_checked_at"),
        Index("idx_trackers_active_paused", "active", "paused"),
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("interval_min", 15)
        kwargs.setdefault("active", True)
        kwargs.setdefault("strict_mode", False)
        kwargs.setdefault("paused", False)
        super().__init__(**kwargs)


class QuerySnapshot(Base, TimestampMixin):
    __tablename__ = "query_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_results: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mean_byn: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0.0)
    median_byn: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0.0)
    min_byn: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0.0)
    max_byn: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0.0)

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
    # DB-M9: widened from 512 → 2048 in migration 20260510_0006. Kufar
    # sometimes appends recommender/tracking params to the canonical
    # ad URL which can push the full link past 512 bytes.
    link: Mapped[str] = mapped_column(String(2048), nullable=False)
    last_price_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("query", "ad_id", name="uq_query_listing_state"),
        Index("idx_query_listing_states_query", "query"),
        # DB-M4: idx_query_listing_states_active dropped in migration
        # 20260510_0006 — standalone boolean index was wasteful, the
        # compound idx_query_listing_states_query_active below covers
        # every query that actually filters by ``active``.
        Index("idx_query_listing_states_query_active", "query", "active"),
        # BE-06 / Wave 29: partial index for the nightly cleanup
        # ``WHERE active=false AND last_seen_at < cutoff``. Created in
        # migration 20260511_0007 as a partial index keyed on
        # last_seen_at with ``WHERE active = false`` — the model-level
        # declaration here uses the same ``postgresql_where`` pattern
        # as ``idx_trackers_user_active_partial`` so autogenerate
        # diff stays clean.
        Index(
            "idx_query_listing_states_cleanup",
            "last_seen_at",
            postgresql_where=text("active = false"),
            sqlite_where=text("active = false"),
        ),
    )


class TrackerEvent(Base, UserIDMixin):
    __tablename__ = "tracker_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracker_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trackers.id", ondelete="CASCADE"),
        nullable=False,
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
    price_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    delta_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Enriched metadata
    thumbnail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
        Index("idx_tracker_events_tracker_created", "tracker_id", text("created_at DESC")),
        CheckConstraint(
            "event_type IN ('new_listing', 'price_drop', 'trend_reversal', "
            "'price_threshold_alert', 'discount_alert')",
            name="chk_tracker_events_event_type",
        ),
    )


class LeadItem(Base, UserIDMixin, TimestampMixin):
    __tablename__ = "lead_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # DB-M9: widened from 512 → 2048 in migration 20260510_0006; see
    # QueryListingState.link for the rationale.
    link: Mapped[str] = mapped_column(String(2048), nullable=False)
    price_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    buy_price_byn: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    sold_price_byn: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    target_resale_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
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
    # Watchlist-merged columns (status='watching' uses these)
    initial_price_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    market_median_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    duplicate_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # NOTE: onupdate only fires on ORM-level attribute changes.
    # Bulk updates via session.execute(update(...)) will NOT trigger this.
    # For bulk updates, set updated_at=datetime.now(UTC) explicitly.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    user = relationship("User", back_populates="lead_items")
    expenses = relationship("DealExpense", back_populates="lead", cascade="all, delete-orphan")
    reminders = relationship("LeadReminder", back_populates="lead", cascade="all, delete-orphan")
    price_snapshots = relationship(
        "LeadItemPriceSnapshot",
        back_populates="lead_item",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "ad_id", name="uq_lead_items_user_ad"),
        CheckConstraint(
            "status IN ('watching', 'new', 'reviewing', 'in_progress', "
            "'researching', 'negotiating', 'deferred', 'closed', "
            "'abandoned', 'bought', 'sold', 'skipped')",
            name="chk_lead_items_status",
        ),
        Index("idx_lead_items_user", "user_id"),
        # DB-M4: idx_lead_items_status and idx_lead_items_market_status
        # were dropped in migration 20260510_0006. The former was
        # redundant with the compound (user_id, status) index below
        # for per-user queries (every user-scoped query combines the
        # two); the latter indexed a 2-3 value column that Postgres
        # would never pick over a seq scan.
        Index("idx_lead_items_user_status", "user_id", "status"),
    )


class LeadItemPriceSnapshot(Base):
    """Per-row price history for lead_items (covers both watchlist and
    active leads). One row is appended every time a price refresh sees
    a change vs. the most recent snapshot, capped to a rolling window
    so the table stays small and queries cheap.

    Used by the watchlist sparkline in the Mini App and by future
    price-trend charts on the deal-detail screen."""

    __tablename__ = "lead_item_price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("lead_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price_byn: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    snapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    lead_item = relationship("LeadItem", back_populates="price_snapshots")

    __table_args__ = (
        Index("idx_lead_item_price_snapshots_lookup", "lead_item_id", "snapped_at"),
    )


class DealExpense(Base):
    """Expense tracking for deals (delivery, repair, etc.)."""

    __tablename__ = "deal_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("lead_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
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
        CheckConstraint(
            "expense_type IN ('delivery', 'repair', 'customs', 'packaging', 'transport', 'other')",
            name="chk_deal_expenses_expense_type",
        ),
        Index("idx_deal_expenses_lead", "lead_id"),
        Index("idx_deal_expenses_user", "user_id"),
    )


class LeadReminder(Base):
    """Reminder for a lead — notifies the user at a scheduled time."""

    __tablename__ = "lead_reminders"

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
    remind_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent: Mapped[bool] = mapped_column(
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

    # Relationships
    lead = relationship("LeadItem", back_populates="reminders")
    user = relationship("User", back_populates="reminders")

    __table_args__ = (
        Index("idx_reminders_due", "remind_at", "sent"),
    )


class UserConsent(Base):
    """User consent records for PD processing and AI analysis."""

    __tablename__ = "user_consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="ai_analysis | pd_processing | cross_border",
    )
    version: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="2026.1",
        server_default="2026.1",
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="consents")

    __table_args__ = (
        Index("idx_user_consents_user", "user_id"),
        Index("idx_user_consents_type", "consent_type"),
        # DB-M6: UNIQUE partial index. Enforces "at most one active
        # consent per (user_id, consent_type)" at the storage layer,
        # belt-and-suspenders over the application-level "revoke old
        # before insert new" pattern in routers/consent.grant_consent.
        # Without it, two concurrent grant_consent requests could
        # race past the SELECT and end up with duplicate active
        # rows. Router handles the IntegrityError and returns the
        # surviving row. Migration 20260510_0006 replaces the old
        # non-unique partial index.
        Index(
            "idx_user_consents_user_type_active",
            "user_id", "consent_type",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )


class AIAuditLog(Base):
    """Audit trail for AI-assisted decisions (Belarus Law No. 91-Z requirement)."""

    __tablename__ = "ai_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    ad_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    query: Mapped[str | None] = mapped_column(String(256), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user = relationship("User", back_populates="ai_audit_logs")

    __table_args__ = (
        Index("idx_ai_audit_user", "user_id"),
        Index("idx_ai_audit_created", "created_at"),
    )


class SavedSearch(Base, UserIDMixin, TimestampMixin):
    """User-saved search queries with optional alert filters."""

    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    strict_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    target_discount_percent: Mapped[float] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=10.0,
        server_default="10",
    )
    max_price_byn: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
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
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    # Relationships
    user = relationship("User", back_populates="saved_searches")

    __table_args__ = (
        UniqueConstraint("user_id", "query", "strict_mode", name="uq_saved_searches_user_query"),
        Index("idx_saved_searches_user", "user_id"),
        Index("idx_saved_searches_active", "active"),
    )


class Contact(Base):
    """Saved seller contacts for a user."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    seller_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kufar_profile: Mapped[str | None] = mapped_column(String(512), nullable=True)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    user = relationship("User", back_populates="contacts")

    __table_args__ = (
        UniqueConstraint("user_id", "phone", name="uq_contacts_user_phone"),
        Index("idx_contacts_user", "user_id"),
    )
