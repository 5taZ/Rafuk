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


class ListingsResponse(BaseModel):
    query: str
    currency: str
    sort: str
    total: int
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


class TrackerCreate(BaseModel):
    query: str
    interval_min: int = 15


class TrackerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    query: str
    interval_min: int
    last_seen_ad_id: int | None = None
    active: bool
    created_at: datetime
