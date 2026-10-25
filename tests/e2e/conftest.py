"""E2E test configuration — gated behind KUFAR_E2E=1.

The entire directory is skip-collected when the env var is unset so the
default `uv run pytest` invocation doesn't require Chromium or Playwright.
"""
from __future__ import annotations

import functools
import os
import pathlib
import posixpath
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

# G-12: skip the whole directory unless explicitly opted in.
collect_ignore_glob = []
if os.getenv("KUFAR_E2E") != "1":
    collect_ignore_glob = ["test_*.py"]


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"


@pytest.fixture(scope="session")
def frontend_path() -> pathlib.Path:
    """Absolute path to the frontend/ directory."""
    return FRONTEND_DIR


class _FrontendHandler(SimpleHTTPRequestHandler):
    """Serves frontend/ at / and assets/ at /assets/, the layout nginx uses."""

    def translate_path(self, path: str) -> str:
        url_path = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
        if posixpath.normpath(url_path).startswith("/assets/"):
            self.directory = str(REPO_ROOT)
        return super().translate_path(path)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="session")
def frontend_url() -> Iterator[str]:
    """OPS-12: the Mini App over http://127.0.0.1, not file://. Under file://
    the ES modules, the service worker and same-origin requests do not load
    the way they do in Telegram's WebView."""
    handler = functools.partial(_FrontendHandler, directory=str(FRONTEND_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
