from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    price_vs_median: float | None = None
    region_name: str | None = None
    config_summary: str | None = None
    fair_price_band: str | None = None
    fair_price_label: str | None = None
    anomaly_flags: list[str] = Field(default_factory=list)
    anomaly_labels: list[str] = Field(default_factory=list)
    is_duplicate: bool = False
    duplicate_count: int = 0
    deal_score: float = 0.0
    deal_verdict: str | None = None
    deal_reasons: list[str] = Field(default_factory=list)
    price_byn: float | None = None
    liquidity: LiquidityInsight | None = None
    flip_estimates: list[FlipEstimate] = Field(default_factory=list)
    thumbnail: str | None = None


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
    region_name: str | None = None
    fair_price_band: str | None = None
    fair_price_label: str | None = None
    anomaly_flags: list[str] = Field(default_factory=list)
    anomaly_labels: list[str] = Field(default_factory=list)
    is_duplicate: bool = False
    duplicate_count: int = 0
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
    exclude_duplicates: bool = False


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
    exclude_duplicates: bool = False
    last_seen_ad_id: int | None = None
    last_seen_price_byn: float | None = None
    last_checked_at: datetime | None = None
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
    created_at: datetime


class LeadCreate(BaseModel):
    query: str
    ad_id: int
    title: str
    link: str
    price_byn: float | None = None
    target_resale_byn: float | None = None
    status: str = "new"
    source: str = "manual"
    notes: str | None = None


class LeadUpdate(BaseModel):
    status: str | None = None
    target_resale_byn: float | None = None
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
    target_resale_byn: float | None = None
    status: str
    source: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class WatchlistCreate(BaseModel):
    query: str
    ad_id: int
    title: str
    link: str
    price_byn: float | None = None
    notes: str | None = None


class WatchlistUpdate(BaseModel):
    workflow_status: str | None = None
    notes: str | None = None


class WatchlistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    ad_id: int
    query: str
    title: str
    link: str
    initial_price_byn: float | None = None
    current_price_byn: float | None = None
    price_delta_byn: float | None = None
    price_delta_percent: float | None = None
    workflow_status: str
    market_status: str
    duplicate_count: int = 0
    notes: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None
    updated_at: datetime


class WatchlistRefreshResponse(BaseModel):
    updated: int
    missing: int
    price_drops: int


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


class CompareResponse(BaseModel):
    currency: str
    base_query: str
    items: list[CompareRequestItem]
