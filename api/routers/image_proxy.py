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


# SEC-08 / INF-09: per-user concurrency cap on top of the global
# transcode semaphore. Without this, a single abusive client can open
# 4+ parallel image-proxy requests, occupy every slot of
# ``_transcode_semaphore`` and starve every other user. The global
# slowapi limiter caps total request *rate* per user but not concurrency
# — a burst that completes one transcode every couple of seconds slips
# under 60/min while still hogging Pillow workers.
#
# Each authenticated user (telegram user_id) gets their own
# ``asyncio.Semaphore(_USER_TRANSCODE_LIMIT)``. The lazy registry below
# is bounded by ``_MAX_USER_SEMAPHORES`` and FIFO-evicts stale entries
# the same way ``ai_task_store._task_locks`` does — Python dicts
# preserve insertion order, so popping from the front evicts oldest.
_USER_TRANSCODE_LIMIT = 2
_MAX_USER_SEMAPHORES = 1024
_user_semaphores: dict[str, asyncio.Semaphore] = {}


def _get_user_semaphore(user_key: str) -> asyncio.Semaphore:
    sem = _user_semaphores.get(user_key)
    if sem is None:
        if len(_user_semaphores) >= _MAX_USER_SEMAPHORES:
            # PR-07: only evict semaphores that are NOT currently
            # locked. The previous FIFO sweep could drop a semaphore
            # that an in-flight request still held; the next request
            # from that user would then build a fresh semaphore and
            # bypass the per-user concurrency cap.
            #
            # ``asyncio.Semaphore.locked()`` returns True iff every
            # slot is in use, so a request that just acquired its
            # first slot (``_USER_TRANSCODE_LIMIT == 2`` ⇒ value=1
            # after one acquire) would technically be eligible for
            # eviction by ``locked()`` alone. Cover that case via the
            # ``_value`` check too — any in-flight request leaves
            # ``_value < _USER_TRANSCODE_LIMIT``, so it pins the
            # semaphore in the registry.
            target = _MAX_USER_SEMAPHORES // 2
            evicted = 0
            for stale_key in list(_user_semaphores.keys()):
                candidate = _user_semaphores.get(stale_key)
                if candidate is None:
                    continue
                inflight = getattr(candidate, "_value", _USER_TRANSCODE_LIMIT)
                if candidate.locked() or inflight < _USER_TRANSCODE_LIMIT:
                    continue
                _user_semaphores.pop(stale_key, None)
                evicted += 1
                if evicted >= target:
                    break
            # Emergency release valve: if every semaphore is in use
            # (1024+ concurrent users mid-request), we still have to
            # make room or the registry grows unbounded. Drop the
            # oldest entries — same fallback as before, just only
            # when there are no idle entries to harvest.
            if evicted == 0:
                for stale_key in list(_user_semaphores.keys())[:target]:
                    _user_semaphores.pop(stale_key, None)
        sem = asyncio.Semaphore(_USER_TRANSCODE_LIMIT)
        _user_semaphores[user_key] = sem
    return sem


# In-process LRU for transcoded bytes. Browser cache does the
# cross-request work (30-day Cache-Control), so this only catches
# duplicate hits within a session — same listing thumbnail visible
# in "Все" + "Избранное" + tracker events at the same time. Keep
# it small; oldest entries fall out when capacity is reached.
_LRU_CAPACITY = 256
_transcoded_cache: OrderedDict[tuple[str, int, str], bytes] = OrderedDict()
_transcoded_cache_lock: asyncio.Lock | None = None


def _get_cache_lock() -> asyncio.Lock:
    global _transcoded_cache_lock
    if _transcoded_cache_lock is None:
        _transcoded_cache_lock = asyncio.Lock()
    return _transcoded_cache_lock


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

    SEC-08 / Wave 29: also drops the per-user semaphore registry so
    a test that triggers the 429-on-concurrency path doesn't leave a
    held slot behind to spoil the next test's setup.
    """
    global _http_client, _client_lock
    _transcoded_cache.clear()
    _http_client = None
    _client_lock = None
    _user_semaphores.clear()


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
_client_lock: asyncio.Lock | None = None


def _get_client_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def _get_http_client(settings: Settings) -> httpx.AsyncClient:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        return _http_client
    async with _get_client_lock():
        if _http_client is not None and not _http_client.is_closed:
            return _http_client
        _http_client = httpx.AsyncClient(
            timeout=settings.image_proxy_fetch_timeout,
            follow_redirects=False,
            headers={"User-Agent": "Rafuk-ImageProxy/1.0"},
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
# SEC-08 / Wave 29: tightened from 300/minute (the audit's original
# global cap, which was actually per-user via slowapi's tg:{user_id}
# key) to 60/minute. 60 covers a normal session — a power user opening
# 3-4 long lists with ~15 cards each — without giving an abusive
# client room to slow-drain the transcode pool.
@limiter.limit("60/minute")
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

    async with _get_cache_lock():
        cached_body = _cache_get(cache_key)
    if cached_body is not None:
        return _build_response(cached_body, chosen_fmt, hit="lru")

    # SEC-08 / INF-09: per-user concurrency cap on top of the global
    # transcode pool. Claim the user's slot synchronously — no ``await``
    # between ``locked()`` and ``acquire_nowait`` (via the cheap-path
    # acquire below) means a parallel request from the same user
    # observes ``locked() == True`` on its check and gets 429 instead
    # of queueing into the global pool. Held across the upstream fetch
    # AND the transcode so a slow Kufar upstream also counts toward
    # the user's budget — otherwise an abuser would just stack 100
    # in-flight upstream gets cheaply.
    user_sem = _get_user_semaphore(f"tg:{_user.user_id}")
    if user_sem.locked():
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent image requests; please slow down",
        )
    # Synchronous acquire path: when value > 0, ``acquire()`` returns
    # an already-completed future and the ``await`` is a single tick.
    await user_sem.acquire()
    try:
        upstream_url = f"{_KUFAR_BASE}{path}"
        max_bytes = settings.image_proxy_max_bytes
        # PR-01: stream the upstream body so an oversized response is
        # rejected BEFORE we buffer it into the worker. The previous
        # ``client.get`` loaded the whole body to memory and then
        # checked ``len(...) > max_bytes`` — fine for kind upstreams,
        # but an authenticated abuser could pin known-large gallery
        # paths and force the worker to spool hundreds of MB before
        # the 413. Same shape as the streaming guard already used by
        # ``api.services.ai_images.fetch_image_bytes``.
        try:
            client = await _get_http_client(settings)
            async with client.stream("GET", upstream_url) as upstream:
                if upstream.status_code != 200:
                    raise HTTPException(
                        status_code=upstream.status_code,
                        detail="Upstream not OK",
                    )
                content_length = upstream.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise HTTPException(
                                status_code=413, detail="Upstream image too large"
                            )
                    except ValueError:
                        # Malformed Content-Length — fall through to streaming guard.
                        pass
                chunks: list[bytes] = []
                total = 0
                async for chunk in upstream.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(
                            status_code=413, detail="Upstream image too large"
                        )
                    chunks.append(chunk)
                upstream_body = b"".join(chunks)
        except httpx.HTTPError as exc:
            logger.warning("Image fetch failed for %s: %s", path, exc)
            raise HTTPException(status_code=502, detail="Upstream image fetch failed") from exc

        # Pillow is sync and CPU-bound — handing it off to a worker
        # thread + bounding concurrency keeps the event loop responsive
        # while many list cards request thumbnails in parallel. The
        # per-user semaphore above wraps this one so a single abuser
        # can never consume all _TRANSCODE_LIMIT slots.
        semaphore = _get_transcode_semaphore()
        async with semaphore:
            try:
                body = await asyncio.to_thread(
                    _transcode,
                    upstream_body,
                    fmt=chosen_fmt,
                    max_width=target_width,
                    quality=settings.image_proxy_quality,
                )
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                logger.warning("Image transcode failed for %s: %s", path, exc)
                raise HTTPException(status_code=415, detail="Unsupported source image") from exc

        async with _get_cache_lock():
            _cache_put(cache_key, body)
        return _build_response(body, chosen_fmt, hit="miss")
    finally:
        user_sem.release()


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
