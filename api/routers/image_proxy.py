"""WebP/AVIF transcoding proxy for Kufar JPEG images.

Kufar's CDN (rms.kufar.by) only serves JPEG. Modern Telegram /
mobile clients support WebP and AVIF, which average 25-40 % smaller
than JPEG at the same perceptual quality. We can't change Kufar's
output, but we can fetch their JPEG once, re-encode in-process, and
let the browser cache the result.

URL shape: ``/api/v1/img/<gallery_path>?w=<width>&fmt=auto|webp|avif``

* ``gallery_path`` is the suffix after ``rms.kufar.by/v1/gallery/``
  e.g. ``ad/abc123.jpg``. We hard-restrict to that namespace so the
  endpoint can't be used as an open redirector.
* ``w`` (optional) caps the longest edge. We never up-scale.
* ``fmt`` defaults to ``auto`` which picks the best format the
  client advertised in ``Accept`` (AVIF > WebP > JPEG).

Output carries ``Cache-Control: public, max-age=2592000, immutable``
so the browser keeps the transcoded byte-stream for 30 days. The
endpoint is intentionally stateless server-side (no Redis cache) —
the browser cache + CDN headers do all the heavy lifting.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from PIL import Image, UnidentifiedImageError

from api.config import Settings
from api.dependencies import get_settings_dependency
from api.limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assets"])

# Restrict to safe gallery paths: ad/foo.jpg, list_thumbs/foo.jpg, etc.
# Disallow `..`, leading slashes, query strings.
_PATH_RE = re.compile(r"^[A-Za-z0-9_\-/]+\.(?:jpe?g|JPE?G)$")
_KUFAR_BASE = "https://rms.kufar.by/v1/gallery/"


def _pick_format(requested: str, accept: str) -> Literal["webp", "avif", "jpeg"]:
    """Decide the response format.

    ``requested`` is the explicit ``fmt=`` query param (``auto`` /
    ``webp`` / ``avif`` / ``jpeg``). ``auto`` falls back to the
    client's ``Accept`` header, with AVIF preferred when supported.
    """
    if requested in {"webp", "avif", "jpeg"}:
        return requested  # type: ignore[return-value]
    accept_lower = (accept or "").lower()
    if "image/avif" in accept_lower:
        return "avif"
    if "image/webp" in accept_lower:
        return "webp"
    return "jpeg"


def _content_type(fmt: str) -> str:
    return {
        "webp": "image/webp",
        "avif": "image/avif",
        "jpeg": "image/jpeg",
    }[fmt]


def _transcode(
    raw: bytes,
    *,
    fmt: str,
    max_width: int,
    quality: int,
) -> bytes:
    """Decode JPEG, optionally down-scale, encode to ``fmt``.

    Pillow does the heavy lifting; we run it on the event loop because
    Mini-App image requests are bursty but small (≤5 MB cap), so the
    transcoded output is usually under ~80 KB and finishes in a few
    milliseconds. If we ever need to scale this up, swap the call site
    to ``run_in_executor``.
    """
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        width, height = image.size
        target = max(1, min(max_width, width))
        if width > target:
            scale = target / width
            new_size = (target, max(1, int(round(height * scale))))
            image = image.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        if fmt == "webp":
            image.save(buf, format="WEBP", quality=quality, method=4)
        elif fmt == "avif":
            image.save(buf, format="AVIF", quality=quality)
        else:
            image.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


@router.get("/img/{path:path}")
@limiter.limit("120/minute")
async def get_optimized_image(
    request: Request,
    path: str,
    w: int | None = Query(default=None, ge=16, le=2048),
    fmt: Literal["auto", "webp", "avif", "jpeg"] = "auto",
    settings: Settings = Depends(get_settings_dependency),
) -> Response:
    if not settings.image_proxy_enabled:
        raise HTTPException(status_code=404, detail="Image proxy disabled")
    if not _PATH_RE.match(path):
        raise HTTPException(status_code=400, detail="Invalid image path")

    chosen_fmt = _pick_format(fmt, request.headers.get("accept", ""))
    target_width = min(w or settings.image_proxy_max_width, settings.image_proxy_max_width)
    upstream_url = f"{_KUFAR_BASE}{path}"

    try:
        async with httpx.AsyncClient(
            timeout=settings.image_proxy_fetch_timeout,
            follow_redirects=True,
            headers={"User-Agent": "Rafuk-ImageProxy/1.0"},
        ) as client:
            upstream = await client.get(upstream_url)
    except httpx.HTTPError as exc:
        logger.warning("Image fetch failed for %s: %s", path, exc)
        raise HTTPException(status_code=502, detail="Upstream image fetch failed") from exc

    if upstream.status_code != 200:
        raise HTTPException(status_code=upstream.status_code, detail="Upstream not OK")
    if len(upstream.content) > settings.image_proxy_max_bytes:
        raise HTTPException(status_code=413, detail="Upstream image too large")

    try:
        body = _transcode(
            upstream.content,
            fmt=chosen_fmt,
            max_width=target_width,
            quality=settings.image_proxy_quality,
        )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("Image transcode failed for %s: %s", path, exc)
        raise HTTPException(status_code=415, detail="Unsupported source image") from exc

    return Response(
        content=body,
        media_type=_content_type(chosen_fmt),
        headers={
            "Cache-Control": "public, max-age=2592000, immutable",
            # Tell caches to keep separate copies per Accept header so
            # AVIF clients don't poison the WebP entry.
            "Vary": "Accept",
            # Lets us swap encoders later without users noticing —
            # Telegram's WebView aggressively caches by URL.
            "X-Image-Format": chosen_fmt,
        },
    )
