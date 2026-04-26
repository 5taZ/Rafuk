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
    """Patch httpx.AsyncClient so the proxy never makes real network calls."""

    class _FakeResponse:
        def __init__(self, content: bytes, status_code: int = 200) -> None:
            self.content = content
            self.status_code = status_code

    class _FakeClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, _url: str) -> _FakeResponse:
            return _FakeResponse(jpeg_bytes, status_code=200)

    with patch("api.routers.image_proxy.httpx.AsyncClient", _FakeClient):
        yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    from api.main import create_app

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

        async def get(self, _url: str):
            raise httpx.ConnectTimeout("upstream timeout")

    with patch("api.routers.image_proxy.httpx.AsyncClient", _FailingClient):
        response = client.get("/api/v1/img/ad/abc123.jpg")
    assert response.status_code == 502


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
