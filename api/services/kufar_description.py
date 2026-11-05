from __future__ import annotations

from html.parser import HTMLParser

import httpx

_PAGE_LIMIT = 512 * 1024
_DESCRIPTION_LIMIT = 16_000


class _DescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.parts: list[str] = []
        self.complete = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.complete:
            return
        if tag == "div" and (self.depth or ("itemprop", "description") in attrs):
            self.depth += 1
        elif self.depth and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag == "div":
            self.depth -= 1
            self.complete = self.depth == 0

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


async def fetch_listing_description(
    client: httpx.AsyncClient, ad_id: int, *, user_agent: str,
) -> str | None:
    url = f"https://www.kufar.by/item/{ad_id}"
    async with client.stream(
        "GET", url, headers={"User-Agent": user_agent}, follow_redirects=False,
    ) as response:
        if response.status_code in {404, 410}:
            return None
        response.raise_for_status()
        parser = _DescriptionParser()
        remaining = _PAGE_LIMIT
        async for chunk in response.aiter_text():
            parser.feed(chunk[:remaining])
            if parser.complete:
                return "".join(parser.parts).strip()[:_DESCRIPTION_LIMIT] or None
            remaining -= len(chunk)
            if remaining <= 0:
                break
    return None
