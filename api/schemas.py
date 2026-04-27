from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LeadStatusEnum(StrEnum):
    """Allowed lead statuses.

    `watching` is the merged-in watchlist state — items the user is
    monitoring but hasn't promoted to active deal pipeline yet.
    """

    watching = "watching"
    new = "new"
    in_progress = "in_progress"
    researching = "researching"
    bought = "bought"
    sold = "sold"
    closed = "closed"
    skipped = "skipped"


class WatchlistStatusEnum(StrEnum):
    """Allowed watchlist workflow statuses.

    Watchlist priorities used by the mini-app dropdown
    (default / important / very_important) plus pipeline-style
    states the frontend filter and sort code references
    (watching / reviewing / interested / contacted / passed / skipped).
    Keep this list aligned with frontend/js/render_card_builders.js
    and frontend/js/render_cards.js.
    """

    default = "default"
    important = "important"
    very_important = "very_important"
    watching = "watching"
    reviewing = "reviewing"
    interested = "interested"
    contacted = "contacted"
    passed = "passed"
    skipped = "skipped"


class ExpenseTypeEnum(StrEnum):
    """Allowed expense types."""

    delivery = "delivery"
    repair = "repair"
    other = "other"


class CategoryBucket(BaseModel):
    id: int
    label: str
    count: int


class PriceStatsResponse(BaseModel):
    query: str
    currency: str
    normalized_query: str = ""
    config_summary: str | None = None
    storage_gb: int | None = None
    ram_gb: int | None = None
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int
    total_results: int = 0
    analyzed_count: int = 0
    fair_price_from: float | None = None
    fair_price_to: float | None = None
    categories: list[CategoryBucket] = Field(default_factory=list)
    # 3-5 short refinements pulled from the result-set titles —
    # tokens / phrases that appear most often in the listings'
    # `subject` but aren't already part of the user's query. The
    # frontend renders them as tap-to-append chips.
    suggested_refinements: list[str] = Field(default_factory=list)


class FlipEstimate(BaseModel):
    label: str
    target_price: float
    profit_byn: float
    profit_percent: float


class LiquidityInsight(BaseModel):
    score: float
    label: str
    reasons: list[str] = Field(default_factory=list)


class ListingItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ad_id: int
    title: str = Field(validation_alias="subject")
    price: float
    currency: str
    link: str = Field(validation_alias="ad_link")
    list_time: str | None = None
    region_id: int | None = None
    condition: str | None = None
    seller_type: str | None = None
    company_ad: bool = False
    price_vs_median: float | None = None
    price_reference_scope: str = "query"
    price_reference_label: str | None = None
    region_name: str | None = None
    area_name: str | None = None
    config_summary: str | None = None
    fair_price_band: str | None = None
    fair_price_label: str | None = None
    anomaly_flags: list[str] = Field(default_factory=list)
    anomaly_labels: list[str] = Field(default_factory=list)
    deal_score: float = 0.0
    deal_verdict: str | None = None
    deal_reasons: list[str] = Field(default_factory=list)
    price_byn: float | None = None
    liquidity: LiquidityInsight | None = None
    flip_estimates: list[FlipEstimate] = Field(default_factory=list)
    thumbnail: str | None = None
    seller_rating: float | None = None


class ListingField(BaseModel):
    label: str
    value: str


class ListingDetailResponse(BaseModel):
    query: str
    normalized_query: str = ""
    config_summary: str | None = None
    storage_gb: int | None = None
    ram_gb: int | None = None
    ad_id: int
    title: str
    price: float
    currency: str
    link: str
    list_time: str | None = None
    region_id: int | None = None
    category: str | None = None
    condition: str | None = None
    seller_type: str | None = None
    price_vs_median: float | None = None
    price_reference_scope: str = "query"
    price_reference_label: str | None = None
    region_name: str | None = None
    area_name: str | None = None
    fair_price_band: str | None = None
    fair_price_label: str | None = None
    anomaly_flags: list[str] = Field(default_factory=list)
    anomaly_labels: list[str] = Field(default_factory=list)
    deal_score: float = 0.0
    deal_verdict: str | None = None
    deal_reasons: list[str] = Field(default_factory=list)
    price_byn: float | None = None
    liquidity: LiquidityInsight | None = None
    flip_estimates: list[FlipEstimate] = Field(default_factory=list)
    company_ad: bool = False
    phone_hidden: bool = True
    description: str | None = None
    images: list[str] = Field(default_factory=list)
    parameters: list[ListingField] = Field(default_factory=list)
    seller_fields: list[ListingField] = Field(default_factory=list)
    seller_rating: float | None = None


class ListingsResponse(BaseModel):
    query: str
    currency: str
    normalized_query: str = ""
    config_summary: str | None = None
    storage_gb: int | None = None
    ram_gb: int | None = None
    sort: str
    total: int
    returned: int = 0
    # Pagination cursor — frontend keeps appending pages by raising
    # ``offset`` until ``offset + returned >= total`` (or the server
    # cap, whichever comes first). ``limit`` mirrors what was asked
    # for so the client can detect server-side downsizing.
    offset: int = 0
    limit: int = 0
    has_more: bool = False
    discount_percent: float | None = None
    discount_from_percent: float | None = None
    discount_to_percent: float | None = None
    listings: list[ListingItem]


class SegmentStats(BaseModel):
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int


class SegmentsResponse(BaseModel):
    query: str
    currency: str
    new_private: SegmentStats
    new_shop: SegmentStats
    used_private: SegmentStats
    used_shop: SegmentStats


class CurrencyRatesResponse(BaseModel):
    base: str = "BYN"
    rates: dict[str, float]
    source: str
    fetched_at: datetime


class PriceHistoryPoint(BaseModel):
    snapshot_at: datetime
    mean: float
    median: float
    min: float
    max: float
    analyzed_count: int
    total_results: int


class PriceHistoryResponse(BaseModel):
    query: str
    currency: str
    days: int
    points: list[PriceHistoryPoint]


class GeographyRegionPoint(BaseModel):
    region_id: int
    region_name: str
    count: int
    share_percent: float
    mean: float
    median: float
    min: float
    max: float


class GeographyResponse(BaseModel):
    query: str
    currency: str
    total_analyzed: int
    regions: list[GeographyRegionPoint]


class TrackerCreate(BaseModel):
    query: str
    strict_mode: bool = False
    interval_min: int = 15
    min_discount_percent: float | None = None
    max_price_byn: float | None = None
    seller_type: str | None = None
    condition: str | None = None
    region_name: str | None = None
    config_keyword: str | None = None


class TrackerUpdate(BaseModel):
    """Schema for updating an existing tracker."""

    strict_mode: bool | None = None
    interval_min: int | None = None
    min_discount_percent: float | None = None
    max_price_byn: float | None = None
    seller_type: str | None = None
    condition: str | None = None
    region_name: str | None = None
    config_keyword: str | None = None


class TrackerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    query: str
    strict_mode: bool = False
    interval_min: int
    min_discount_percent: float | None = None
    max_price_byn: float | None = None
    seller_type: str | None = None
    condition: str | None = None
    region_name: str | None = None
    config_keyword: str | None = None
    last_seen_ad_id: int | None = None
    last_seen_price_byn: float | None = None
    last_checked_at: datetime | None = None
    # Pause support
    paused: bool = False
    paused_at: datetime | None = None
    # Computed stats (not stored in DB, added by API)
    event_count: int = 0
    new_listings_count: int = 0
    price_drops_count: int = 0
    last_event_at: datetime | None = None
    avg_events_per_day: float = 0.0
    active: bool
    created_at: datetime


class TrackerEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tracker_id: int
    user_id: int
    ad_id: int | None = None
    query: str
    strict_mode: bool
    event_type: str
    title: str
    link: str
    price_byn: float | None = None
    delta_byn: float | None = None
    # Enriched metadata
    thumbnail: str | None = None
    parameters: dict | None = None
    seller_type: str | None = None
    region_name: str | None = None
    created_at: datetime


class LeadCreate(BaseModel):
    query: str
    ad_id: int
    title: str
    link: str
    price_byn: float | None = None
    thumbnail: str | None = None
    target_resale_byn: float | None = None
    market_median_byn: float | None = None
    notes: str | None = None
    status: LeadStatusEnum = LeadStatusEnum.new
    source: str = "manual"


class LeadUpdate(BaseModel):
    status: LeadStatusEnum | None = None
    target_resale_byn: float | None = None
    buy_price_byn: float | None = None
    sold_price_byn: float | None = None
    notes: str | None = None


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    ad_id: int
    query: str
    title: str
    link: str
    price_byn: float | None = None
    buy_price_byn: float | None = None
    sold_price_byn: float | None = None
    thumbnail: str | None = None
    target_resale_byn: float | None = None
    status: str
    source: str
    sold_at: datetime | None = None
    market_status: str = "active"
    missing_since_at: datetime | None = None
    # Watchlist-merged fields
    initial_price_byn: float | None = None
    market_median_byn: float | None = None
    duplicate_count: int = 0
    last_seen_at: datetime | None = None
    notes: str | None = None
    # Computed fields (not in DB)
    total_expenses: float = 0.0
    actual_profit: float | None = None
    roi_percent: float | None = None
    price_delta_byn: float | None = None
    price_delta_percent: float | None = None
    created_at: datetime
    updated_at: datetime


class WatchlistCreate(BaseModel):
    query: str
    ad_id: int
    title: str
    link: str
    thumbnail: str | None = None
    price_byn: float | None = None
    market_median_byn: float | None = None
    notes: str | None = None


class WatchlistUpdate(BaseModel):
    workflow_status: WatchlistStatusEnum | None = None
    notes: str | None = None


class PriceSnapshotPoint(BaseModel):
    """A single (timestamp, price) pair for the watchlist sparkline."""

    model_config = ConfigDict(from_attributes=True)
    snapped_at: datetime
    price_byn: float


class WatchlistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    ad_id: int
    query: str
    title: str
    link: str
    thumbnail: str | None = None
    initial_price_byn: float | None = None
    current_price_byn: float | None = None
    price_delta_byn: float | None = None
    price_delta_percent: float | None = None
    workflow_status: str
    market_status: str
    market_median_byn: float | None = None
    notes: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None
    missing_since_at: datetime | None = None
    updated_at: datetime
    # Compact 30-day price trend (newest last) so the watchlist card
    # can render a sparkline without a per-row round-trip. Empty list
    # means "no movement recorded yet" — frontend renders a flat line.
    price_history: list[PriceSnapshotPoint] = []


class WatchlistRefreshResponse(BaseModel):
    updated: int
    missing: int
    price_drops: int
    auto_removed: int = 0


class LeadsRefreshResponse(BaseModel):
    checked: int
    active: int
    missing: int


class SavedSearchCreate(BaseModel):
    name: str | None = None
    group_name: str | None = None
    query: str
    strict_mode: bool = False
    target_discount_percent: float = 10.0
    max_price_byn: float | None = None
    seller_type: str | None = None
    condition: str | None = None
    region_name: str | None = None
    config_keyword: str | None = None
    exclude_duplicates: bool = False


class SavedSearchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    group_name: str | None = None
    query: str
    normalized_query: str = ""
    config_summary: str | None = None
    storage_gb: int | None = None
    ram_gb: int | None = None
    strict_mode: bool = False
    target_discount_percent: float
    max_price_byn: float | None = None
    seller_type: str | None = None
    condition: str | None = None
    region_name: str | None = None
    config_keyword: str | None = None
    exclude_duplicates: bool = False
    active: bool
    created_at: datetime


class OpportunityItem(BaseModel):
    saved_search_id: int
    saved_search_name: str
    query: str
    normalized_query: str = ""
    config_summary: str | None = None
    strict_mode: bool = False
    target_discount_percent: float = 10.0
    max_price_byn: float | None = None
    seller_type: str | None = None
    condition: str | None = None
    region_name: str | None = None
    config_keyword: str | None = None
    exclude_duplicates: bool = False
    signal_label: str | None = None
    listing: ListingItem


class OpportunitySignal(BaseModel):
    title: str
    subtitle: str
    query: str
    metric: str


class OpportunityBoardResponse(BaseModel):
    currency: str
    items: list[OpportunityItem]
    top_price_drops: list[OpportunitySignal] = Field(default_factory=list)
    rare_opportunities: list[OpportunityItem] = Field(default_factory=list)
    market_signals: list[OpportunitySignal] = Field(default_factory=list)


class CompareRequestItem(BaseModel):
    query: str
    normalized_query: str = ""
    config_summary: str | None = None
    median: float
    cheap_count: int
    total_results: int
    trend_percent: float | None = None
    best_listing: ListingItem | None = None
    # Full price-stats block in the response currency. The frontend
    # uses these for the side-by-side metric grid (min/mean/max
    # plus the IQR band) so users can compare distributions, not
    # only medians.
    mean: float = 0.0
    min: float = 0.0
    max: float = 0.0
    q1: float = 0.0
    q3: float = 0.0
    analyzed_count: int = 0
    fair_price_from: float | None = None
    fair_price_to: float | None = None


class CompareResponse(BaseModel):
    currency: str
    base_query: str
    items: list[CompareRequestItem]


class LeadFunnelStage(BaseModel):
    """One bar in the lead-pipeline funnel chart."""

    status: str
    label: str
    count: int


class LeadMonthlyStat(BaseModel):
    """One month bucket of sold leads — drives the trend chart."""

    month: str  # YYYY-MM
    sold_count: int
    revenue_byn: float
    profit_byn: float


class LeadAnalyticsResponse(BaseModel):
    """Aggregated lead-pipeline analytics for the deals dashboard."""

    period_days: int
    total_leads: int
    pursued_leads: int
    sold_leads: int
    skipped_leads: int
    active_leads: int
    win_rate_percent: float
    total_revenue_byn: float
    total_cost_byn: float
    total_profit_byn: float
    total_expenses_byn: float
    average_roi_percent: float
    average_days_to_close: float
    median_days_to_close: float
    funnel: list[LeadFunnelStage]
    monthly: list[LeadMonthlyStat]


# Deal Expenses schemas
class DealExpenseCreate(BaseModel):
    expense_type: ExpenseTypeEnum  # delivery, repair, other
    amount_byn: float = Field(gt=0, description="Expense amount in BYN (must be positive)")
    notes: str | None = None
    expense_date: datetime | None = None


class DealExpenseUpdate(BaseModel):
    expense_type: ExpenseTypeEnum | None = None
    amount_byn: float | None = None
    notes: str | None = None
    expense_date: datetime | None = None


class DealExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    user_id: int
    expense_type: str
    amount_byn: float
    notes: str | None = None
    expense_date: datetime
    created_at: datetime


# Contacts schemas
class ContactCreate(BaseModel):
    phone: str | None = None
    seller_name: str | None = None
    kufar_profile: str | None = None


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    phone: str | None = None
    seller_name: str | None = None
    kufar_profile: str | None = None
    saved_at: datetime


# Risk assessment schemas
class RiskItem(BaseModel):
    type: str  # too_cheap, suspicious_words, duplicates
    level: str  # low, medium, high
    message: str


class RiskAssessmentResponse(BaseModel):
    risks: list[RiskItem] = Field(default_factory=list)
    overall_risk: str  # low, medium, high
    overall_emoji: str  # 🟢, 🟡, 🔴


# ── AI Analysis ──────────────────────────────────────────────────────────


class AIAnalysisRequest(BaseModel):
    ad_id: int
    query: str = Field(min_length=1, max_length=200)
    category: int | None = None


class AIQuickConditionRequest(BaseModel):
    ad_id: int
    query: str = Field(min_length=1, max_length=200)
    category: int | None = None


class AIConditionAssessment(BaseModel):
    label: str = ""
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)


class AIFairPrice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_price: float | None = Field(None, alias="from")
    to_price: float | None = Field(None, alias="to")
    reasoning: str = ""


class AIResalePrice(BaseModel):
    label: str = ""
    price_byn: float = 0.0
    reasoning: str = ""


class AIResalePotential(BaseModel):
    fast_price: AIResalePrice | None = None
    market_price: AIResalePrice | None = None
    optimal_price: AIResalePrice | None = None
    reasoning: str = ""


class AIWatchOutItem(BaseModel):
    point: str = ""
    why: str = ""


class AIRecommendation(BaseModel):
    verdict: str = ""  # worth_it, think_twice, overpriced
    text: str = ""


class AISimilarListing(BaseModel):
    ad_id: int
    title: str
    price_byn: float
    image_url: str | None = None
    link: str = ""
    deal_score: float = 0.0
    condition: str | None = None
    ai_note: str = ""


class AIAnalysisResponse(BaseModel):
    ad_id: int
    condition: AIConditionAssessment | None = None
    fair_price: AIFairPrice | None = None
    resale_potential: AIResalePotential | None = None
    watch_out: list[AIWatchOutItem] = Field(default_factory=list)
    recommendation: AIRecommendation | None = None
    similar_listings: list[AISimilarListing] = Field(default_factory=list)
    best_alternative: AISimilarListing | None = None
    meeting_checklist: list[str] = Field(default_factory=list)
    negotiation_tips: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    market_context: str = ""
    best_pick_reason: str = ""
    summary: str = ""
    disclaimer: str = "Анализ носит информационный характер. Результаты не являются гарантией."
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AIQuickConditionResponse(BaseModel):
    ad_id: int
    condition: str = ""
    notes: list[str] = Field(default_factory=list)


# ─── Listing Assistant (seller side) ──────────────────────────────────────


class AIListingAssistantRequest(BaseModel):
    """Draft of an item the user wants to list on Kufar.

    `title` is required — we use it both for the search context and for the
    generated improved title. `category` is optional but improves market
    targeting. `draft_price_byn` is what the user *thinks* of asking; the
    model uses it only as one anchor among several. `photos` is a list of
    `data:image/...;base64,...` URLs (already compressed on the frontend);
    we hard-cap count and per-photo size in the router.
    """

    title: str = Field(min_length=3, max_length=200)
    category: int | None = None
    condition: str | None = Field(None, max_length=64)
    draft_price_byn: float | None = Field(None, ge=0, le=10_000_000)
    is_negotiable: bool = False
    extra_notes: str | None = Field(None, max_length=1200)
    # Hard cap on items is enforced server-side in `_coerce_listing_photos`
    # so that an extra photo doesn't 422 the whole request — we just drop
    # the overflow. Per-item byte-cap is also done there.
    photos: list[str] = Field(default_factory=list, max_length=8)


class AIListingPriceTier(BaseModel):
    label: str = ""
    price_byn: float = 0.0
    weeks_to_sell: str = ""
    reasoning: str = ""


class AIListingPricing(BaseModel):
    fast: AIListingPriceTier | None = None
    market: AIListingPriceTier | None = None
    patient: AIListingPriceTier | None = None
    floor_byn: float | None = None
    market_median_byn: float | None = None
    market_q1_byn: float | None = None
    market_q3_byn: float | None = None
    competing_count: int = 0


class AINegotiationCounter(BaseModel):
    scenario: str = ""
    response: str = ""


class AIListingAssistantResponse(BaseModel):
    title_suggestion: str = ""
    description: str = ""
    description_short: str = ""
    selling_points: list[str] = Field(default_factory=list)
    pricing: AIListingPricing = Field(default_factory=AIListingPricing)
    negotiation_playbook: list[AINegotiationCounter] = Field(default_factory=list)
    photo_tips: list[str] = Field(default_factory=list)
    market_summary: str = ""
    disclaimer: str = (
        "Рекомендации носят информационный характер. Финальное решение по цене и тексту "
        "остаётся за продавцом."
    )
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
