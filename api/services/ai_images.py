"""AI image download and normalisation helpers for multimodal prompts."""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_KUFAR_IMAGE_HOST_RE = re.compile(r"^rms\d*\.kufar\.by$")
MAX_AI_IMAGE_BYTES = 5_000_000
MAX_AI_REDIRECTS = 2

GetClient = Callable[[], Awaitable[httpx.AsyncClient]]


async def fetch_image_b64(url: str, get_client: GetClient) -> dict | None:
    """Download image, resize to max 768px, compress, return as vision content."""
    data = await fetch_image_bytes(url, get_client)
    if not data:
        return None

    # Try to resize for faster AI processing
    compressed = compress_image(data)
    if compressed:
        data = compressed

    mime = "image/jpeg"
    if ".png" in url.lower():
        mime = "image/png"
    elif ".webp" in url.lower():
        mime = "image/webp"
    b64 = base64.b64encode(data).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


async def fetch_image_bytes(url: str, get_client: GetClient) -> bytes | None:
    """Download image bytes using shared httpx client.

    Re-validates every redirect target before following it. Streams
    the final response to enforce the byte limit without loading the
    entire body into memory first.
    """
    if not is_allowed_image_url(url):
        logger.warning("Skipped AI image fetch from unsupported host: %s", url[:80])
        return None
    try:
        client = await get_client()
        current_url = url
        for _ in range(MAX_AI_REDIRECTS + 1):
            if not is_allowed_image_url(current_url):
                logger.warning(
                    "Skipped AI image fetch redirect to unsupported host: %s",
                    current_url[:80],
                )
                return None
            async with client.stream(
                "GET", current_url, timeout=8, follow_redirects=False,
            ) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    continue
                if resp.status_code != 200:
                    return None
                content_type = resp.headers.get("content-type", "")
                if not content_type.lower().startswith("image/"):
                    logger.warning("Skipped AI image fetch with content-type=%s", content_type)
                    return None
                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > MAX_AI_IMAGE_BYTES:
                            logger.warning("Skipped AI image fetch larger than byte limit")
                            return None
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_AI_IMAGE_BYTES:
                        logger.warning(
                            "Skipped AI image fetch larger than byte limit (streaming)"
                        )
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        logger.warning("Skipped AI image fetch after too many redirects")
    except httpx.HTTPError as e:
        logger.warning("Failed to fetch image %s: %s", url[:80], e)
    return None


def is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    hostname = parsed.hostname or ""
    return parsed.scheme == "https" and bool(_KUFAR_IMAGE_HOST_RE.fullmatch(hostname))


def compress_image(data: bytes, max_dim: int = 768, quality: int = 75) -> bytes | None:
    """Resize and compress image to reduce AI processing time."""
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_dim:
            ratio = max_dim / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except (ImportError, OSError, ValueError) as e:
        logger.debug("Image compression skipped: %s", e)
        return None
