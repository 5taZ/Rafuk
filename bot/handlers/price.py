from __future__ import annotations

import httpx
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from api.config import get_settings

router = Router(name="price")


async def fetch_price_stats(query: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{settings.api_base_url}/api/v1/price-stats",
            params={"query": query, "currency": "USD"},
        )
        response.raise_for_status()
        return response.json()


async def fetch_listings(query: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{settings.api_base_url}/api/v1/listings",
            params={"query": query, "currency": "USD", "sort": "newest"},
        )
        response.raise_for_status()
        return response.json()


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: /price <query>")
        return

    try:
        stats = await fetch_price_stats(query)
    except httpx.HTTPError:
        await message.answer("Price lookup is temporarily unavailable.")
        return
    await message.answer(
        f"{query}\n"
        f"Count: {stats['count']}\n"
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
    except httpx.HTTPError:
        await message.answer("Listings lookup is temporarily unavailable.")
        return
    listings = payload.get("listings", [])[:5]
    if not listings:
        await message.answer(f"No listings found for {query}.")
        return

    lines = [f"Top listings for {query}:"]
    for item in listings:
        lines.append(f"- {item['title']}: {item['price']} {item['currency']}")
    await message.answer("\n".join(lines))
