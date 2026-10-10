"""Canonical status vocabularies shared by the DB, the API, the bot and the Mini App."""

from __future__ import annotations

from typing import Literal, get_args

LeadStatus = Literal[
    "new",
    "reviewing",
    "in_progress",
    "negotiating",
    "deferred",
    "bought",
    "reselling",
    "sold",
    "skipped",
]
WatchlistStatus = Literal["watching", "reviewing", "in_progress", "deferred", "skipped"]
MarketStatus = Literal["active", "missing", "price_drop", "duplicate"]

LEAD_STATUSES: tuple[str, ...] = get_args(LeadStatus)
WATCHLIST_STATUSES: tuple[str, ...] = get_args(WatchlistStatus)
MARKET_STATUSES: tuple[str, ...] = get_args(MarketStatus)

DEFAULT_LEAD_STATUS: LeadStatus = "new"
DEFAULT_WATCHLIST_STATUS: WatchlistStatus = "watching"
DEFAULT_MARKET_STATUS: MarketStatus = "active"
