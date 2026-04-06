from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from api.config import get_settings
from api.services.aggregator import compute_price_stats, extract_prices
from api.services.kufar_client import KufarClient

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
        "min": stats.min,
        "max": stats.max,
        "currency": currency,
    }


async def fetch_listings(query: str, currency: str = "USD") -> dict:
    from api.services.aggregator import compute_price_stats, extract_prices
    from api.services.cache import MemoryCache
    from api.services.currency_service import CurrencyService
    from api.services.listing_mapper import build_listing_item

    settings = get_settings()
    client = KufarClient(settings)
    cache = MemoryCache()
    currency_service = CurrencyService(cache)
    try:
        payload = await client.search(query=query, currency="BYN", size=50)
    finally:
        await client.aclose()
    ads = payload.get("ads", [])
    prices = extract_prices(ads)
    market_stats = compute_price_stats(prices)
    median_byn = market_stats.median
    rates: dict[str, float] = {}
    items = [
        build_listing_item(
            ad,
            query=query,
            currency=currency,
            rates=rates,
            currency_service=currency_service,
            median_byn=median_byn,
            market_stats=market_stats,
        )
        for ad in ads
    ]
    return {"listings": items}


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: /price <query>")
        return

    try:
        stats = await fetch_price_stats(query)
    except Exception:
        await message.answer("Price lookup is temporarily unavailable.")
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
    except Exception:
        await message.answer("Listings lookup is temporarily unavailable.")
        return
    listings = payload.get("listings", [])[:5]
    if not listings:
        await message.answer(f"No listings found for {query}.")
        return

    lines = [f"Top listings for {query}:"]
    for item in listings:
        lines.append(f"- {item.title}: {item.price} {item.currency}")
    await message.answer("\n".join(lines))
