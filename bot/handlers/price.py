from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from httpx import HTTPError

from api.config import get_settings
from api.services.aggregator import normalize_price_byn
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.query_pipeline import (
    dataset_cache_key,
    get_dataset_cache,
    load_query_dataset,
    normalize_ads,
)
from bot.keyboards import is_http_url
from bot.middlewares import ThrottlingMiddleware

if TYPE_CHECKING:
    from aiogram.types import TelegramObject

    from api.services.query_pipeline import QueryDataset

logger = logging.getLogger(__name__)

router = Router(name="price")

MAX_QUERY_LENGTH = 255
# Telegram rejects messages longer than 4096 characters.
MAX_MESSAGE_LENGTH = 4096
TOP_LIMIT = 5
TOP_FETCH_SIZE = 20
MAX_TITLE_LENGTH = 120
CURRENCY = "BYN"


def _validate_query(command: CommandObject, usage: str) -> tuple[str | None, str | None]:
    """Return (query, None) for a usable query or (None, reply_text) otherwise."""
    query = (command.args or "").strip()
    if not query:
        return None, usage
    if len(query) > MAX_QUERY_LENGTH:
        return None, f"Query is too long (max {MAX_QUERY_LENGTH} characters)."
    return query, None


def _is_kufar_lookup(_event: TelegramObject, data: dict[str, Any]) -> bool:
    """True when the command will query Kufar; a usage hint is answered without limits."""
    command = data.get("command")
    if command is None:
        return True
    query, _ = _validate_query(command, "")
    return query is not None


# /price and /top hit Kufar: one command per user every few seconds, one command in flight per
# user and a small process-wide cap on concurrent lookups (with no waiting queue: the user is
# told the bot is busy) keep the shared server IP off Kufar's radar and the bot responsive.
router.message.middleware(
    ThrottlingMiddleware(rate_seconds=3.0, max_concurrent=2, applies_to=_is_kufar_lookup)
)


def _clip(text: str, limit: int = MAX_MESSAGE_LENGTH) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _format_byn(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + f" {CURRENCY}"


def _market_total(dataset: QueryDataset) -> int:
    """Kufar's reported total when it exceeds the fetched sample (the search is not strict)."""
    total = dataset.total_results
    reported = dataset.response.get("total")
    if isinstance(reported, int) and not isinstance(reported, bool) and reported > total:
        return reported
    return total


async def fetch_price_stats(query: str) -> dict[str, Any]:
    """Compute BYN price statistics for a query with the shared non-strict pipeline."""
    dataset = await load_query_dataset(
        query=query,
        currency=CURRENCY,
        strict_search=False,
        settings=get_settings(),
        client_factory=KufarClient,
    )
    stats = dataset.price_stats
    truncated = getattr(dataset, "truncated", False) or dataset.response.get("truncated")
    return {
        "market_total": _market_total(dataset),
        "fetched_count": len(dataset.ads) + len(dataset.unidentified_ads),
        "truncated": bool(truncated),
        "analyzed_count": stats.count,
        "mean": stats.mean,
        "median": stats.median,
        "min": stats.min,
        "max": stats.max,
        "currency": CURRENCY,
    }


async def _load_top_ads(query: str) -> list[dict[str, Any]]:
    client = KufarClient(get_settings())
    try:
        payload = await client.search(query=query, currency=CURRENCY, size=TOP_FETCH_SIZE)
    finally:
        await client.aclose()
    # /top needs no ad_id; normalization only guarantees field types, so one malformed
    # listing is dropped or cleaned up instead of failing the whole command.
    raw_ads = payload.get("ads") if isinstance(payload, dict) else None
    return normalize_ads(raw_ads, require_ad_id=False)


async def _load_top_page(query: str) -> dict[str, Any]:
    return {"ads": await _load_top_ads(query)}


async def fetch_listings(query: str, limit: int = TOP_LIMIT) -> list[dict[str, Any]]:
    """Return the most recent listings as plain dicts: title, BYN price (or None), link.

    Only the first page of the newest ads is requested. When the process-wide dataset cache
    is enabled (the bot enables it), repeated /top for the same query reuse that one page.
    """
    cache = get_dataset_cache()
    if cache is None:
        ads = await _load_top_ads(query)
    else:
        key = "top|" + dataset_cache_key(query, False, {"size": TOP_FETCH_SIZE})
        ads = (await cache.get_or_load(key, lambda: _load_top_page(query)))["ads"]

    listings: list[dict[str, Any]] = []
    for ad in ads:
        title = " ".join(str(ad.get("subject") or "").split())
        if not title:
            continue
        link = str(ad.get("ad_link") or "")
        listings.append(
            {
                "title": title,
                "price_byn": normalize_price_byn(ad.get("price_byn")),
                "link": link if is_http_url(link) else None,
            }
        )
        if len(listings) >= limit:
            break
    return listings


def _truncation_note(stats: dict[str, Any]) -> str:
    if not stats.get("truncated"):
        return ""
    return f"\nNote: only the latest {stats['fetched_count']} listings were analyzed."


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject) -> None:
    query, error_text = _validate_query(command, "Usage: /price <query>")
    if query is None:
        await message.answer(error_text or "")
        return

    try:
        stats = await fetch_price_stats(query)
    except (KufarAPIError, HTTPError):
        logger.warning("Kufar lookup failed in /price for query: %s", query, exc_info=True)
        await message.answer("Price lookup is temporarily unavailable.")
        return

    if not stats["analyzed_count"]:
        await message.answer(
            _clip(
                f"No priced listings found for {query}.\n"
                f"Market total: {stats['market_total']}"
                f"{_truncation_note(stats)}"
            )
        )
        return

    await message.answer(
        _clip(
            f"{query}\n"
            f"Market total: {stats['market_total']}\n"
            f"Analyzed: {stats['analyzed_count']}\n"
            f"Mean: {_format_byn(stats['mean'])}\n"
            f"Median: {_format_byn(stats['median'])}\n"
            f"Range: {_format_byn(stats['min'])} - {_format_byn(stats['max'])}"
            f"{_truncation_note(stats)}"
        )
    )


@router.message(Command("top"))
async def cmd_top(message: Message, command: CommandObject) -> None:
    query, error_text = _validate_query(command, "Usage: /top <query>")
    if query is None:
        await message.answer(error_text or "")
        return

    try:
        listings = await fetch_listings(query)
    except (KufarAPIError, HTTPError):
        logger.warning("Kufar lookup failed in /top for query: %s", query, exc_info=True)
        await message.answer("Listings lookup is temporarily unavailable.")
        return

    if not listings:
        await message.answer(_clip(f"No listings found for {query}."))
        return

    lines = [f"Top listings for {query}:"]
    for item in listings:
        title = item["title"]
        if len(title) > MAX_TITLE_LENGTH:
            title = title[: MAX_TITLE_LENGTH - 1] + "…"
        price = item["price_byn"]
        price_text = _format_byn(price) if price is not None else "no price"
        lines.append(f"- {title}: {price_text}")
        if item["link"]:
            lines.append(f"  {item['link']}")
    await message.answer(_clip("\n".join(lines)))
