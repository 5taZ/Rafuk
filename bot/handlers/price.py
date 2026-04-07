from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from httpx import HTTPError

from api.config import get_settings
from api.services.aggregator import compute_price_stats, extract_prices
from api.services.kufar_client import KufarAPIError, KufarClient

logger = logging.getLogger(__name__)

router = Router(name="price")


async def fetch_price_stats(query: str, currency: str = "USD") -> dict:
    settings = get_settings()
    client = KufarClient(settings)
    try:
        payload = await client.search(query=query, currency="BYN", size=100)
    finally:
        await client.aclose()
    ads = payload.get("ads", [])
    prices = extract_prices(ads)
    stats = compute_price_stats(prices)
    return {
        "total_results": len(ads),
        "count": len(ads),
        "analyzed_count": len(ads),
        "mean": stats.mean,
        "median": stats.median,
        "min": stats.min_price,
        "max": stats.max_price,
        "currency": currency,
    }


async def fetch_listings(query: str, currency: str = "USD") -> dict:
    from api.services.listing_mapper import map_to_listing_items

    settings = get_settings()
    client = KufarClient(settings)
    try:
        payload = await client.search(query=query, currency="BYN", size=50)
    finally:
        await client.aclose()
    ads = payload.get("ads", [])
    items = map_to_listing_items(ads, currency=currency, rates={})
    return {"listings": items}


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: /price <query>")
        return

    try:
        stats = await fetch_price_stats(query)
    except (KufarAPIError, HTTPError):
        await message.answer("Price lookup is temporarily unavailable.")
        return
    except Exception:
        logger.exception("Unexpected error in /price command for query: %s", query)
        await message.answer("An unexpected error occurred. Please try again later.")
        return
    await message.answer(
        f"{query}\n"
        f"Market total: {stats.get('total_results', stats['count'])}\n"
        f"Analyzed: {stats.get('analyzed_count', stats['count'])}\n"
        f"Mean: {stats['mean']} {stats['currency']}\n"
        f"Median: {stats['median']} {stats['currency']}\n"
        f"Range: {stats['min']} - {stats['max']} {stats['currency']}"
    )


@router.message(Command("top"))
async def cmd_top(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: /top <query>")
        return

    try:
        payload = await fetch_listings(query)
    except (KufarAPIError, HTTPError):
        await message.answer("Listings lookup is temporarily unavailable.")
        return
    except Exception:
        logger.exception("Unexpected error in /top command for query: %s", query)
        await message.answer("An unexpected error occurred. Please try again later.")
        return
    listings = payload.get("listings", [])[:5]
    if not listings:
        await message.answer(f"No listings found for {query}.")
        return

    lines = [f"Top listings for {query}:"]
    for item in listings:
        lines.append(f"- {item.title}: {item.price} {item.currency}")
    await message.answer("\n".join(lines))
