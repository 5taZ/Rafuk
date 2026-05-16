from __future__ import annotations

import io
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def jpeg_bytes() -> bytes:
    """Generate a 200×200 RGB JPEG in-memory for upstream mocking."""
    img = Image.new("RGB", (200, 200), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture
def fake_upstream(jpeg_bytes: bytes) -> Iterator[None]:
    """Patch httpx.AsyncClient so the proxy never makes real network calls.

    PR-01: the proxy now uses ``client.stream("GET", url)`` so an
    oversized upstream is rejected before being buffered. The fake
    response below reproduces enough of the streaming protocol
    (``aiter_bytes`` + async-context-manager) that the handler treats
    it as a real httpx Response.
    """

    class _FakeStreamResponse:
        def __init__(self, content: bytes, status_code: int = 200) -> None:
            self.content = content
            self.status_code = status_code
            self.headers = {"content-length": str(len(content))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int = 65536):
            yield self.content

    class _FakeClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def stream(self, _method: str, _url: str):
            return _FakeStreamResponse(jpeg_bytes, status_code=200)

    with patch("api.routers.image_proxy.httpx.AsyncClient", _FakeClient):
        yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    from api.main import create_app
    from api.routers.image_proxy import _clear_cache_for_tests

    # Each test exercises the proxy from scratch — without this clear
    # the LRU's "lru hit" path would mask transcode regressions in the
    # next test that happens to ask for the same (path, w, fmt).
    _clear_cache_for_tests()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_image_proxy_rejects_invalid_path(client: TestClient) -> None:
    """Non-jpeg extensions and dot/space tricks must be 400.

    Note: leading-segment traversals like ``../etc/passwd`` get
    normalised by Starlette before they reach the route handler, so
    they 404 outside our regex's reach. The cases below are the ones
    that actually hit the validator.
    """
    for bad_path in (
        "ad/file.exe",
        "ad/file.png",
        "ad/file.jpg.suffix",
        "ad with spaces/file.jpg",
    ):
        response = client.get(f"/api/v1/img/{bad_path}")
        assert response.status_code == 400, bad_path


def test_image_proxy_returns_webp_when_accept_header_supports_it(
    client: TestClient,
    fake_upstream: None,
) -> None:
    response = client.get(
        "/api/v1/img/ad/abc123.jpg",
        headers={"Accept": "image/webp,image/*,*/*"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["x-image-format"] == "webp"
    # Body must be a valid WebP — Pillow refuses to decode anything else.
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.format == "WEBP"


def test_image_proxy_returns_avif_when_accept_header_lists_it(
    client: TestClient,
    fake_upstream: None,
) -> None:
    response = client.get(
        "/api/v1/img/ad/abc123.jpg",
        headers={"Accept": "image/avif,image/webp,image/*,*/*"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/avif"
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.format == "AVIF"


def test_image_proxy_explicit_jpeg_format(
    client: TestClient,
    fake_upstream: None,
) -> None:
    response = client.get("/api/v1/img/ad/abc123.jpg?fmt=jpeg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_image_proxy_caches_aggressively(
    client: TestClient,
    fake_upstream: None,
) -> None:
    response = client.get(
        "/api/v1/img/ad/abc123.jpg",
        headers={"Accept": "image/webp"},
    )
    assert response.status_code == 200
    assert "max-age=2592000" in response.headers.get("cache-control", "")
    assert "immutable" in response.headers.get("cache-control", "")
    assert "Accept" in response.headers.get("vary", "")


def test_image_proxy_resizes_when_width_specified(
    client: TestClient,
    fake_upstream: None,
) -> None:
    """The fake upstream serves 200×200; with w=100 the body must be 100×100."""
    response = client.get(
        "/api/v1/img/ad/abc123.jpg?w=100",
        headers={"Accept": "image/webp"},
    )
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.size == (100, 100)


def test_image_proxy_does_not_upscale(
    client: TestClient,
    fake_upstream: None,
) -> None:
    """Asking for w=1024 on a 200-px source must NOT upscale to 1024."""
    response = client.get(
        "/api/v1/img/ad/abc123.jpg?w=1024",
        headers={"Accept": "image/webp"},
    )
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.size == (200, 200)


def test_image_proxy_rejects_oversized_via_content_length_header(
    client: TestClient,
    jpeg_bytes: bytes,
) -> None:
    """PR-01: a Content-Length header above the max-bytes ceiling
    must short-circuit BEFORE we buffer the body, so a malicious
    upstream can't push 100s of MB into the worker just by claiming
    a small payload up front. The stream protocol is structured so
    the handler may abort either at the header check or via the
    streaming-byte counter; either path must yield 413.
    """
    over_limit = b"x" * 16
    fake_size = 9_999_999_999  # well past image_proxy_max_bytes (5 MB default)

    class _OversizedStream:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-length": str(fake_size)}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int = 65536):
            # Should never reach this point — content-length already
            # over the limit. Guard anyway so a regression is loud.
            yield over_limit

    class _OversizedClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def stream(self, _method: str, _url: str):
            return _OversizedStream()

    with patch("api.routers.image_proxy.httpx.AsyncClient", _OversizedClient):
        response = client.get("/api/v1/img/ad/abc123.jpg")
    assert response.status_code == 413


def test_image_proxy_streams_with_running_byte_cap(
    client: TestClient,
) -> None:
    """PR-01: when the upstream omits Content-Length, the running
    byte counter inside ``aiter_bytes`` must enforce the same
    ``image_proxy_max_bytes`` ceiling so a chunk-by-chunk attack
    can't slip past the header check.
    """

    class _ChunkedStream:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers: dict[str, str] = {}  # no content-length

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int = 65536):
            # 6 MB total body — over the 5 MB default cap.
            for _ in range(6):
                yield b"x" * (1024 * 1024)

    class _ChunkedClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def stream(self, _method: str, _url: str):
            return _ChunkedStream()

    with patch("api.routers.image_proxy.httpx.AsyncClient", _ChunkedClient):
        response = client.get("/api/v1/img/ad/abc123.jpg")
    assert response.status_code == 413


def test_image_proxy_handles_upstream_failure(
    client: TestClient,
) -> None:
    class _FailingClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def stream(self, _method: str, _url: str):
            raise httpx.ConnectTimeout("upstream timeout")

    with patch("api.routers.image_proxy.httpx.AsyncClient", _FailingClient):
        response = client.get("/api/v1/img/ad/abc123.jpg")
    assert response.status_code == 502


def test_image_proxy_lru_cache_skips_upstream_on_repeat(
    client: TestClient,
    jpeg_bytes: bytes,
) -> None:
    """Same (path, w, fmt) within a session must be served from the
    in-process LRU — that's what keeps a 200-card list view from
    re-encoding the same thumbnail when it shows up in tracker
    events + leads + watchlist at once.
    """
    upstream_calls = 0

    class _StreamResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-length": str(len(jpeg_bytes))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int = 65536):
            yield jpeg_bytes

    class _CountingClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        @property
        def is_closed(self) -> bool:
            return False

        async def aclose(self) -> None:
            return None

        def stream(self, _method: str, _url: str):
            nonlocal upstream_calls
            upstream_calls += 1
            return _StreamResp()

    with patch("api.routers.image_proxy.httpx.AsyncClient", _CountingClient):
        # First hit — cache miss, upstream is fetched.
        first = client.get(
            "/api/v1/img/ad/abc.jpg?w=100",
            headers={"Accept": "image/webp"},
        )
        assert first.status_code == 200
        assert first.headers["x-image-cache"] == "miss"

        # Second hit, identical key — must be served from LRU.
        second = client.get(
            "/api/v1/img/ad/abc.jpg?w=100",
            headers={"Accept": "image/webp"},
        )
        assert second.status_code == 200
        assert second.headers["x-image-cache"] == "lru"
        assert second.content == first.content
        # Upstream was hit exactly once across the two requests.
        assert upstream_calls == 1


def test_image_proxy_disabled_via_settings(
    client: TestClient,
) -> None:
    from api.dependencies import get_settings_dependency
    from api.main import create_app

    settings = get_settings_dependency()
    settings.image_proxy_enabled = False
    try:
        app = create_app()
        app.dependency_overrides[get_settings_dependency] = lambda: settings
        with TestClient(app) as disabled_client:
            response = disabled_client.get("/api/v1/img/ad/abc123.jpg")
        assert response.status_code == 404
    finally:
        settings.image_proxy_enabled = True


# ── SEC-08 / INF-09 (Wave 29): per-user concurrency cap ─────────────────
# Unit-level tests over the per-user semaphore helper. End-to-end
# concurrency is harder to exercise through TestClient (which runs
# requests serially on the same loop), so we validate the mechanism
# directly: same key returns the same Semaphore, distinct keys get
# distinct semaphores, and ``locked()`` flips once the cap is hit.


def test_user_semaphore_reuses_same_instance_per_key() -> None:
    from api.routers.image_proxy import _get_user_semaphore, _user_semaphores

    _user_semaphores.clear()
    sem1 = _get_user_semaphore("tg:42")
    sem2 = _get_user_semaphore("tg:42")
    sem_other = _get_user_semaphore("tg:99")
    assert sem1 is sem2, "Same user key must return the same semaphore"
    assert sem1 is not sem_other, "Different users must get distinct semaphores"


def test_user_semaphore_locks_at_limit_and_unlocks_on_release() -> None:
    from api.routers.image_proxy import (
        _USER_TRANSCODE_LIMIT,
        _get_user_semaphore,
        _user_semaphores,
    )

    _user_semaphores.clear()
    sem = _get_user_semaphore("tg:42")
    # Fresh semaphore: has _USER_TRANSCODE_LIMIT slots, not locked.
    assert not sem.locked()
    # Exhaust all slots synchronously — asyncio.Semaphore.acquire() on
    # an already-available value returns a done future, so the awaits
    # below complete in the same tick without an event loop.
    import asyncio

    async def _exhaust() -> None:
        for _ in range(_USER_TRANSCODE_LIMIT):
            await sem.acquire()
        # All slots held — handler code would raise 429 here.
        assert sem.locked()
        sem.release()
        # One slot freed — parallel request can proceed again.
        assert not sem.locked()

    asyncio.run(_exhaust())


def test_user_semaphore_registry_evicts_oldest_over_cap() -> None:
    from api.routers.image_proxy import (
        _MAX_USER_SEMAPHORES,
        _get_user_semaphore,
        _user_semaphores,
    )

    _user_semaphores.clear()
    # Fill to the cap.
    for i in range(_MAX_USER_SEMAPHORES):
        _get_user_semaphore(f"tg:{i}")
    assert len(_user_semaphores) == _MAX_USER_SEMAPHORES
    # One more entry — should trip the FIFO eviction path and drop
    # half of the oldest keys.
    _get_user_semaphore(f"tg:{_MAX_USER_SEMAPHORES}")
    assert len(_user_semaphores) <= _MAX_USER_SEMAPHORES
    # The newest key must still be present; the oldest half must be gone.
    assert f"tg:{_MAX_USER_SEMAPHORES}" in _user_semaphores
    assert "tg:0" not in _user_semaphores


def test_user_semaphore_registry_skips_inflight_semaphores() -> None:
    """PR-07: an evictable semaphore must NOT be one currently held by
    an in-flight request. The previous FIFO sweep evicted by insertion
    order alone, which could drop a semaphore mid-request and let a
    parallel request from the same user create a fresh one — bypassing
    the per-user concurrency cap. Now the sweep prefers idle entries.
    """
    import asyncio

    from api.routers.image_proxy import (
        _MAX_USER_SEMAPHORES,
        _USER_TRANSCODE_LIMIT,
        _get_user_semaphore,
        _user_semaphores,
    )

    async def _run() -> None:
        _user_semaphores.clear()
        # Pin "tg:0" by acquiring all its slots — emulates a request
        # in flight for that user (one slot would be enough; we take
        # both to make the test deterministic against the eviction
        # heuristic).
        pinned = _get_user_semaphore("tg:0")
        for _ in range(_USER_TRANSCODE_LIMIT):
            await pinned.acquire()

        # Fill the registry to the cap with idle semaphores.
        for i in range(1, _MAX_USER_SEMAPHORES):
            _get_user_semaphore(f"tg:{i}")
        assert len(_user_semaphores) == _MAX_USER_SEMAPHORES

        # Trip the cap — eviction must keep the pinned semaphore.
        _get_user_semaphore(f"tg:{_MAX_USER_SEMAPHORES}")
        assert "tg:0" in _user_semaphores, "pinned in-flight semaphore must survive eviction"
        # The new entry landed.
        assert f"tg:{_MAX_USER_SEMAPHORES}" in _user_semaphores

        # Cleanup — release the held slots so other tests see a fresh
        # registry through the conftest tear-down.
        for _ in range(_USER_TRANSCODE_LIMIT):
            pinned.release()

    asyncio.run(_run())
