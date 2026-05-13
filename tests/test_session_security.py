"""Tests for api/services/session_security.py — blacklist + replay log."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from api.services.session_security import is_user_blacklisted, track_init_data_use


class _StubCache:
    """Tiny in-memory CacheBackend for tests."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._counters: dict[str, int] = {}

    async def get_json(self, key: str) -> Any:
        return self._store.get(key)

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._store[key] = value

    async def incr(self, key: str, ttl: int | None = None) -> int:
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]


class _ExplodingCache:
    """Cache that always raises — simulates a Redis outage."""

    async def get_json(self, key: str) -> Any:
        raise ConnectionError("redis down")

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_blacklist_blocks_listed_user() -> None:
    cache = _StubCache()
    cache._store["auth:blacklist:42"] = 1
    assert await is_user_blacklisted(cache, 42) is True


@pytest.mark.asyncio
async def test_blacklist_allows_unknown_user() -> None:
    cache = _StubCache()
    assert await is_user_blacklisted(cache, 99) is False


@pytest.mark.asyncio
async def test_blacklist_fails_open_when_cache_unavailable() -> None:
    """Redis outage MUST NOT lock everyone out — fail-open by design."""
    cache = _ExplodingCache()
    assert await is_user_blacklisted(cache, 42) is False


@pytest.mark.asyncio
async def test_blacklist_handles_none_cache() -> None:
    assert await is_user_blacklisted(None, 42) is False


@pytest.mark.asyncio
async def test_track_init_data_first_call_records_ip(caplog) -> None:
    cache = _StubCache()
    await track_init_data_use(cache, "abc123", user_id=7, client_ip="1.2.3.4")
    # First time → no warning
    assert not any("ip_mismatch" in r.message for r in caplog.records)
    # Stored
    assert any(k.startswith("auth:initdata:") for k in cache._store)


@pytest.mark.asyncio
async def test_track_init_data_logs_ip_mismatch(caplog) -> None:
    cache = _StubCache()
    await track_init_data_use(cache, "abc123", user_id=7, client_ip="1.2.3.4")
    caplog.set_level(logging.WARNING)
    await track_init_data_use(cache, "abc123", user_id=7, client_ip="9.9.9.9")
    # Same hash from a second IP must trigger the operator warning.
    assert any("ip_mismatch" in r.message for r in caplog.records), (
        "Expected an initdata_ip_mismatch warning when same blob arrives "
        "from a different IP"
    )


@pytest.mark.asyncio
async def test_track_init_data_silent_on_cache_error() -> None:
    """Tracking is best-effort observability — must never raise."""
    cache = _ExplodingCache()
    # Should NOT raise.
    await track_init_data_use(cache, "abc123", user_id=7, client_ip="1.2.3.4")


@pytest.mark.asyncio
async def test_track_init_data_handles_none_inputs() -> None:
    await track_init_data_use(None, "abc", user_id=1, client_ip="1.1.1.1")
    cache = _StubCache()
    await track_init_data_use(cache, "", user_id=1, client_ip="1.1.1.1")
    # No state was recorded for the empty initdata.
    assert not cache._store


@pytest.mark.asyncio
async def test_track_init_data_autoblacklists_after_threshold(caplog) -> None:
    """OPUS-8: 5 IP-mismatches in the rolling window must auto-blacklist
    the user without waiting for an ops human to read the logs.
    """
    cache = _StubCache()
    # First call seeds the (initdata → first_ip) mapping.
    await track_init_data_use(cache, "blob", user_id=11, client_ip="1.1.1.1")
    caplog.set_level(logging.WARNING)
    # Five subsequent calls from a different IP — each one increments
    # the warning counter; the fifth crosses the threshold.
    for ip in ("9.9.9.1", "9.9.9.2", "9.9.9.3", "9.9.9.4", "9.9.9.5"):
        await track_init_data_use(cache, "blob", user_id=11, client_ip=ip)

    # Blacklist row written.
    assert cache._store.get("auth:blacklist:11") is not None
    assert await is_user_blacklisted(cache, 11) is True
    # Auto-block log line emitted.
    assert any("initdata_replay_autoblock" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_track_init_data_under_threshold_does_not_blacklist() -> None:
    """OPUS-8: a couple of mismatches (e.g. wifi ↔ cellular handoff)
    must NOT trigger the auto-blacklist."""
    cache = _StubCache()
    await track_init_data_use(cache, "blob", user_id=22, client_ip="1.1.1.1")
    for ip in ("9.9.9.1", "9.9.9.2"):
        await track_init_data_use(cache, "blob", user_id=22, client_ip=ip)
    assert cache._store.get("auth:blacklist:22") is None
    assert await is_user_blacklisted(cache, 22) is False
