"""WebP/AVIF transcoding proxy for Kufar JPEG images.

Kufar's CDN (rms.kufar.by) only serves JPEG. Modern Telegram /
mobile clients support WebP and AVIF, which average 25-40 % smaller
than JPEG at the same perceptual quality. We can't change Kufar's
output, but we can fetch their JPEG once, re-encode off the event
loop, and let the browser cache the result.

URL shape: ``/api/v1/img/<gallery_path>?w=<width>&fmt=auto|webp|avif``

* ``gallery_path`` is the suffix after ``rms.kufar.by/v1/gallery/``
  e.g. ``ad/abc123.jpg``. We hard-restrict to that namespace so the
  endpoint can't be used as an open redirector.
* ``w`` (optional) caps the longest edge. We never up-scale.
* ``fmt`` defaults to ``auto`` which picks the best format the
  client advertised in ``Accept`` (AVIF > WebP > JPEG).

Output carries ``Cache-Control: public, max-age=2592000, immutable``
so the browser keeps the transcoded byte-stream for 30 days.

Performance notes:
  * Pillow's encode/decode is sync and CPU-bound. We hand it off to
    ``asyncio.to_thread`` so a 200-card list view can't stall the
    event loop with 200 simultaneous transcodes.
  * A bounded ``asyncio.Semaphore`` caps concurrent transcodes — too
    many simultaneous Pillow workers thrash CPU and slow every
    request. The cap is conservative (4 workers) since most clients
    will pull cached responses on the second visit anyway.
  * A small in-process LRU caches the latest transcoded bytes so
    duplicate requests within a session (same listing card visible
    in multiple lists) don't re-fetch / re-encode.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections import OrderedDict
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from PIL import Image, UnidentifiedImageError

from api.config import Settings
from api.dependencies import get_settings_dependency, get_telegram_user
from api.limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assets"])

# Restrict to safe gallery paths: ad/foo.jpg, list_thumbs/foo.jpg, etc.
# Disallow `..`, leading slashes, query strings.
_PATH_RE = re.compile(r"^[A-Za-z0-9_\-/]+\.(?:jpe?g|JPE?G)$")
_KUFAR_BASE = "https://rms.kufar.by/v1/gallery/"

# Cap simultaneous Pillow encodes to keep the event loop responsive.
# 4 workers ≈ a typical Cloud Run container's CPU; if we ever scale
# vertically the semaphore can grow. Lazily initialised at first use
# so the value picks up the running event loop.
_TRANSCODE_LIMIT = 4
_transcode_semaphore: asyncio.Semaphore | None = None


def _get_transcode_semaphore() -> asyncio.Semaphore:
    global _transcode_semaphore
    if _transcode_semaphore is None:
        _transcode_semaphore = asyncio.Semaphore(_TRANSCODE_LIMIT)
    return _transcode_semaphore


# In-process LRU for transcoded bytes. Browser cache does the
# cross-request work (30-day Cache-Control), so this only catches
# duplicate hits within a session — same listing thumbnail visible
# in "Все" + "Избранное" + tracker events at the same time. Keep
# it small; oldest entries fall out when capacity is reached.
_LRU_CAPACITY = 256
_transcoded_cache: OrderedDict[tuple[str, int, str], bytes] = OrderedDict()
_transcoded_cache_lock = asyncio.Lock()


def _cache_get(key: tuple[str, int, str]) -> bytes | None:
    value = _transcoded_cache.get(key)
    if value is not None:
        # Touch — promote to MRU end of the OrderedDict.
        _transcoded_cache.move_to_end(key)
    return value


def _cache_put(key: tuple[str, int, str], value: bytes) -> None:
    if key in _transcoded_cache:
        _transcoded_cache.move_to_end(key)
    _transcoded_cache[key] = value
    while len(_transcoded_cache) > _LRU_CAPACITY:
        _transcoded_cache.popitem(last=False)


def _clear_cache_for_tests() -> None:
    """Test hook — pytest fixtures call this between cases so a
    cached response from one test doesn't leak into the next.

    Also tears down the cached httpx client so the next test's
    ``patch("...httpx.AsyncClient", FakeClient)`` is honoured —
    the singleton would otherwise hold a real client built before
    the patch took effect.
    """
    global _http_client
    _transcoded_cache.clear()
    _http_client = None


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

    Pillow does the heavy lifting. Must always be called via
    ``asyncio.to_thread`` — never call directly from the event loop
    as the CPU-bound encode will block async scheduling.
    """
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        width, orig_height = image.size
        target = max(1, min(max_width, width))
        if width > target:
            scale = target / width
            new_size = (target, max(1, int(round(orig_height * scale))))
            image = image.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        if fmt == "webp":
            image.save(buf, format="WEBP", quality=quality, method=4)
        elif fmt == "avif":
            image.save(buf, format="AVIF", quality=quality)
        else:
            image.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


# Single shared httpx client so we don't pay TLS handshake on every
# image request. 200-card list views fan out 200 thumbnails in
# parallel — recreating the connection pool each time was a major
# source of latency. The client is closed by the FastAPI lifespan
# when the app shuts down via aclose() exposed below.
_http_client: httpx.AsyncClient | None = None


def _get_http_client(settings: Settings) -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=settings.image_proxy_fetch_timeout,
            follow_redirects=False,
            headers={"User-Agent": "Rafuk-ImageProxy/1.0"},
            # http2 keep-alive across image requests cuts the per-
            # request RTT roughly in half on warm connections.
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=60.0,
            ),
        )
    return _http_client


async def aclose_http_client() -> None:
    """Called from the FastAPI lifespan teardown to drain the pool.

    Tolerates fake clients (test mocks) that may not implement the
    full httpx surface — we don't want lifespan teardown to fail
    just because a TestClient stub is missing ``is_closed``.
    """
    global _http_client
    client = _http_client
    _http_client = None
    if client is None:
        return
    if getattr(client, "is_closed", False):
        return
    aclose = getattr(client, "aclose", None)
    if aclose is not None:
        await aclose()


@router.get("/img/{path:path}")
@limiter.limit("300/minute")
async def get_optimized_image(
    request: Request,
    path: str,
    w: int | None = Query(default=None, ge=16, le=2048),
    fmt: Literal["auto", "webp", "avif", "jpeg"] = "auto",
    settings: Settings = Depends(get_settings_dependency),
    _user=Depends(get_telegram_user),
) -> Response:
    if not settings.image_proxy_enabled:
        raise HTTPException(status_code=404, detail="Image proxy disabled")
    if not _PATH_RE.match(path):
        raise HTTPException(status_code=400, detail="Invalid image path")

    chosen_fmt = _pick_format(fmt, request.headers.get("accept", ""))
    target_width = min(w or settings.image_proxy_max_width, settings.image_proxy_max_width)
    cache_key = (path, target_width, chosen_fmt)

    async with _transcoded_cache_lock:
        cached_body = _cache_get(cache_key)
    if cached_body is not None:
        return _build_response(cached_body, chosen_fmt, hit="lru")

    upstream_url = f"{_KUFAR_BASE}{path}"
    try:
        client = _get_http_client(settings)
        upstream = await client.get(upstream_url)
    except httpx.HTTPError as exc:
        logger.warning("Image fetch failed for %s: %s", path, exc)
        raise HTTPException(status_code=502, detail="Upstream image fetch failed") from exc

    if upstream.status_code != 200:
        raise HTTPException(status_code=upstream.status_code, detail="Upstream not OK")
    if len(upstream.content) > settings.image_proxy_max_bytes:
        raise HTTPException(status_code=413, detail="Upstream image too large")

    # Pillow is sync and CPU-bound — handing it off to a worker
    # thread + bounding concurrency keeps the event loop responsive
    # while many list cards request thumbnails in parallel.
    semaphore = _get_transcode_semaphore()
    async with semaphore:
        try:
            body = await asyncio.to_thread(
                _transcode,
                upstream.content,
                fmt=chosen_fmt,
                max_width=target_width,
                quality=settings.image_proxy_quality,
            )
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            logger.warning("Image transcode failed for %s: %s", path, exc)
            raise HTTPException(status_code=415, detail="Unsupported source image") from exc

    async with _transcoded_cache_lock:
        _cache_put(cache_key, body)
    return _build_response(body, chosen_fmt, hit="miss")


def _build_response(body: bytes, chosen_fmt: str, *, hit: str) -> Response:
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
            # Cheap observability — `lru` means we skipped the
            # upstream fetch + Pillow encode entirely.
            "X-Image-Cache": hit,
        },
    )
