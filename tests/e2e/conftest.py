"""E2E test configuration — gated behind KUFAR_E2E=1.

The entire directory is skip-collected when the env var is unset so the
default `uv run pytest` invocation doesn't require Chromium or Playwright.
"""
from __future__ import annotations

import os
import pathlib

import pytest

# G-12: skip the whole directory unless explicitly opted in.
collect_ignore_glob = []
if os.getenv("KUFAR_E2E") != "1":
    collect_ignore_glob = ["test_*.py"]


FRONTEND_DIR = pathlib.Path(__file__).resolve().parents[2] / "frontend"


@pytest.fixture(scope="session")
def frontend_path() -> pathlib.Path:
    """Absolute path to the frontend/ directory for file:// navigation."""
    return FRONTEND_DIR
