from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PriceStatsResponse(BaseModel):
    query: str
    currency: str
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int
    total_results: int = 0
    analyzed_count: int = 0


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
    thumbnail: str | None = None


class ListingField(BaseModel):
    label: str
    value: str


class ListingDetailResponse(BaseModel):
    query: str
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
    company_ad: bool = False
    phone_hidden: bool = True
    description: str | None = None
    images: list[str] = Field(default_factory=list)
    parameters: list[ListingField] = Field(default_factory=list)
    seller_fields: list[ListingField] = Field(default_factory=list)


class ListingsResponse(BaseModel):
    query: str
    currency: str
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


class TrackerCreate(BaseModel):
    query: str
    strict_mode: bool = False
    interval_min: int = 15


class TrackerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    query: str
    strict_mode: bool = False
    interval_min: int
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
    query: str
    strict_mode: bool
    event_type: str
    title: str
    link: str
    price_byn: float | None = None
    delta_byn: float | None = None
    created_at: datetime
