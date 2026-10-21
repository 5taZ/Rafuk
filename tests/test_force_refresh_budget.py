"""RV-SEC-04: ``force_refresh`` is a per-user budget, not a free pass.

Every analytics endpoint accepted ``force_refresh=true`` without limits,
so one account could keep the shared Kufar client (~1 request/s per
worker) busy with re-paginations of the same query. Now one upstream
re-fetch per user per FORCE_REFRESH_COOLDOWN_SECONDS is allowed across
all endpoints; beyond that the request is served from cached data.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData
from api.services.cache import MemoryCache
from tests.conftest import FakeCurrencyService


class _VersionedKufar:
    """Every upstream pagination returns a new price, so a response shows
    which fetch it was built from."""

    def __init__(self) -> None:
        self.fetches = 0

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        self.fetches += 1
        price = 1000 + self.fetches * 1000  # 2000, 3000, ...
        return {
            "ads": [
                {
                    "ad_id": 500 + i,
                    "subject": "iPhone 15 128GB",
                    "price_byn": price * 100,
                    "price_usd": price * 30,
                    "ad_link": f"https://www.kufar.by/item/{500 + i}",
                    "list_time": "2026-04-01T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "2"}],
                }
                for i in range(3)
            ],
            "total": 3,
        }

    async def aclose(self) -> None:
        return None


def _app(kufar: _VersionedKufar, user: dict[str, int]):
    from api.dependencies import (
        get_cache,
        get_currency_service,
        get_kufar_client,
        get_telegram_user,
    )
    from api.main import create_app

    app = create_app()
    cache = MemoryCache()  # one instance: the budget lives in the cache
    app.dependency_overrides[get_kufar_client] = lambda: kufar
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    app.dependency_overrides[get_telegram_user] = lambda: TelegramInitData(
        user_id=user["id"], first_name="T", raw={},
    )
    return app


def test_force_refresh_refetches_at_most_once_per_user_across_endpoints() -> None:
    kufar = _VersionedKufar()
    user = {"id": 1001}
    params = {"query": "iphone 15", "strict_search": "false"}
    forced = {**params, "force_refresh": "true"}
    with TestClient(_app(kufar, user)) as client:
        assert client.get("/api/v1/price-stats", params=params).status_code == 200
        assert kufar.fetches == 1

        first = client.get("/api/v1/price-stats", params=forced)
        assert first.status_code == 200
        assert kufar.fetches == 2

        # Within the cooldown every endpoint answers normally (no error),
        # but none of them reaches Kufar again.
        for path in (
            "/api/v1/price-stats",
            "/api/v1/listings",
            "/api/v1/segments",
            "/api/v1/geography",
        ):
            response = client.get(path, params=forced)
            assert response.status_code == 200, (path, response.text)
        assert kufar.fetches == 2

        # The budget is per user.
        user["id"] = 1002
        assert client.get("/api/v1/price-stats", params=forced).status_code == 200
        assert kufar.fetches == 3


def test_refused_force_refresh_is_rebuilt_from_the_refreshed_dataset() -> None:
    """The Mini App's refresh hits /price-stats first and then fans out to
    /listings, /segments and /geography with the same flag. Only the first
    call may re-fetch; the others must still drop their own (older) cached
    responses and rebuild from the dataset it just refreshed, otherwise the
    refresh would show new stats next to stale cards."""
    kufar = _VersionedKufar()
    user = {"id": 2001}
    params = {"query": "iphone 15", "strict_search": "false"}
    forced = {**params, "force_refresh": "true"}
    with TestClient(_app(kufar, user)) as client:
        stale = client.get("/api/v1/listings", params=params).json()
        assert {item["price"] for item in stale["listings"]} == {2000}

        refreshed_stats = client.get("/api/v1/price-stats", params=forced).json()
        assert refreshed_stats["median"] == 3000
        assert kufar.fetches == 2

        listings = client.get("/api/v1/listings", params=forced).json()
        assert {item["price"] for item in listings["listings"]} == {3000}
        assert kufar.fetches == 2

        # Without the flag the (now fresh) cached page is served as usual.
        cached = client.get("/api/v1/listings", params=params).json()
        assert {item["price"] for item in cached["listings"]} == {3000}
        assert kufar.fetches == 2
