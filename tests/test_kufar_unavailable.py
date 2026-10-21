"""RV-BE-05: a Kufar outage surfaces as ``KufarAPIError``, never as data.

The circuit breaker used to answer with ``{"ads": [], "total": 0}``. The
API cached that stub and showed "nothing found" with zero medians; the
scheduler took it as a successful check (empty baseline, zero snapshot).
These tests pin the consumers' side of the contract: the API answers 503
and caches nothing, the scheduler skips the whole query group.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.services.cache import MemoryCache
from api.services.kufar_client import KufarAPIError, KufarClient, KufarUnavailableError
from tests.conftest import FakeCurrencyService, make_user

ANALYTICS_PATHS = [
    "/api/v1/price-stats",
    "/api/v1/listings",
    "/api/v1/segments",
    "/api/v1/geography",
]


class _DownKufar:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def search_all_ads(self, **kwargs) -> dict:
        raise self.exc

    async def search(self, **kwargs) -> dict:
        raise self.exc

    async def aclose(self) -> None:
        return None


def _app_with_kufar(kufar) -> tuple[object, MemoryCache]:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app

    app = create_app()
    cache = MemoryCache()
    app.dependency_overrides[get_kufar_client] = lambda: kufar
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    return app, cache


@pytest.mark.parametrize("path", ANALYTICS_PATHS)
def test_open_breaker_answers_503_instead_of_empty_results(path: str) -> None:
    app, cache = _app_with_kufar(
        _DownKufar(KufarUnavailableError("breaker open", retry_after=12.3)),
    )
    with TestClient(app) as client:
        response = client.get(path, params={"query": "iphone"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Kufar временно недоступен. Попробуйте через минуту."
    }
    assert response.headers["retry-after"] == "13"
    # Never replayable from a browser/proxy cache, even on /listings and
    # /price-stats where successful answers are public for 60 s.
    assert response.headers["cache-control"] == "no-store"
    # Handled inside the middleware stack, unlike the catch-all 500.
    assert response.headers.get("x-request-id")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert not cache._storage, "nothing may be cached from a failed fetch"


@pytest.mark.parametrize("path", ANALYTICS_PATHS)
def test_kufar_api_error_answers_503_without_retry_after(path: str) -> None:
    app, _ = _app_with_kufar(
        _DownKufar(KufarAPIError("Kufar API request failed after 3 attempts")),
    )
    with TestClient(app) as client:
        response = client.get(path, params={"query": "iphone"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Не удалось получить данные с Kufar. Попробуйте позже."
    }
    assert "retry-after" not in response.headers


@pytest.mark.asyncio
async def test_scheduler_skips_query_group_while_breaker_is_open(monkeypatch) -> None:
    """The collector (unchanged) already treats KufarAPIError as a group
    error; with the breaker raising instead of returning the stub, an open
    breaker no longer produces a snapshot, a baseline or a sync."""
    from api.database import get_engine, get_session_factory
    from api.models import QueryListingState, QuerySnapshot, Tracker, TrackerEvent
    from scheduler import collector

    class OpenBreakerClient(KufarClient):
        def __init__(self, settings) -> None:
            super().__init__(settings)
            self._circuit_state = "open"
            self._circuit_open_until = asyncio.get_running_loop().time() + 600

    monkeypatch.setattr(collector, "KufarClient", OpenBreakerClient)
    collector._tracker_failure_counts.clear()

    engine = get_engine()
    sf = get_session_factory(engine)
    async with sf() as session:
        user = make_user(telegram_user_id=4242, first_name="Breaker")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="iphone 15", strict_mode=False))
        await session.commit()

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"
    settings.kufar_parallel_semaphore = 2
    settings.kufar_request_delay = 0.0
    settings.kufar_timeout = 1.0
    bot = AsyncMock()
    try:
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get_mock:
            await collector.check_trackers(bot, sf, settings)

        assert get_mock.await_count == 0
        bot.send_message.assert_not_awaited()
        async with sf() as session:
            tracker = (await session.execute(select(Tracker))).scalar_one()
            # No baseline: the next successful tick is still the "first check".
            assert tracker.last_checked_at is None
            assert tracker.last_seen_ad_id is None
            for model in (QuerySnapshot, QueryListingState, TrackerEvent):
                count = await session.scalar(select(func.count()).select_from(model))
                assert count == 0, model.__name__
        # Counted as a failure for C-04, not mistaken for a clean tick.
        assert collector._tracker_failure_counts.get(tracker.id) == 1
    finally:
        collector._tracker_failure_counts.clear()
        await engine.dispose()
