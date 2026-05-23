from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.services.cache import MemoryCache
from tests.conftest import FakeCurrencyService

CAR_CATEGORY_PARAM = {
    "p": "category",
    "pl": "Категория",
    "v": "2010",
    "vl": "Легковые авто",
}
PART_CATEGORY_PARAM = {
    "p": "category",
    "pl": "Категория",
    "v": "2040",
    "vl": "Запчасти",
}


VERDICTS = {"Хорошая цена", "Ниже рынка", "Средняя цена", "Выше рынка"}


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "total": 1,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15",
                    "price_byn": 200000,
                    "price_usd": 600,
                    "ad_link": "https://www.kufar.by/item/1",
                    "list_time": "2026-04-01T10:00:00",
                    "region_id": 6,
                    "body": "Полное описание объявления",
                    "company_ad": True,
                    "phone_hidden": False,
                    "images": [{"path": "adim1/test.jpg"}],
                    "ad_parameters": [
                        {"p": "category", "pl": "Подкатегория", "vl": "Мобильные телефоны"},
                        {"p": "condition", "pl": "Состояние", "v": "Новый", "vl": "Новое"},
                        {"p": "seller_type", "pl": "Продавец", "v": "Частное лицо"},
                        {"p": "phones_memory", "pl": "Память", "vl": "256 Гб"},
                        {"p": "phone", "pl": "Телефон", "vl": "+375291234567"},
                    ],
                    "account_parameters": [
                        {"p": "name", "pl": "Имя", "v": "Иван"},
                        {"p": "shop_address", "pl": "Город", "v": "Минск"},
                    ],
                }
            ],
        }

    async def aclose(self) -> None:
        return None


def test_listing_detail_endpoint_returns_full_card(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listing_detail

    monkeypatch.setattr(listing_detail, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listing-detail",
            params={"query": "iphone", "ad_id": 1, "currency": "BYN", "strict_search": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "iPhone 15"
    assert payload["description"] == "Полное описание объявления"
    assert payload["images"][0].endswith("/adim1/test.jpg")
    assert payload["parameters"][0]["label"] == "Подкатегория"
    assert all(item["label"] != "Телефон" for item in payload["parameters"])
    assert payload["seller_fields"][0]["value"] == "Иван"
    assert payload["fair_price_label"] is not None
    assert payload["region_name"] == "Регион 6"
    assert payload["normalized_query"] == "iphone"
    assert payload["deal_verdict"] in VERDICTS
    assert payload["price_byn"] == 2000
    assert payload["liquidity"] is not None
    assert payload["flip_estimates"] is not None


def test_listing_detail_not_found(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listing_detail

    monkeypatch.setattr(listing_detail, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listing-detail",
            params={"query": "iphone", "ad_id": 999, "currency": "BYN"},
        )

    assert response.status_code == 404


def test_listing_detail_keeps_price_delta_stable_in_category_view(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import listing_detail

    broad_ads = [
        {
            "ad_id": 10,
            "subject": "Audi Q7 3.0 TDI",
            "price_byn": 3200000,
            "ad_link": "https://www.kufar.by/item/10",
            "list_time": "2026-04-01T10:00:00",
            "region_id": 6,
            "category": "2010",
            "body": "Q7",
            "ad_parameters": [CAR_CATEGORY_PARAM],
            "account_parameters": [],
        },
        {
            "ad_id": 11,
            "subject": "Audi Q7 4L",
            "price_byn": 3400000,
            "ad_link": "https://www.kufar.by/item/11",
            "list_time": "2026-04-01T10:10:00",
            "region_id": 6,
            "category": "2010",
            "body": "Q7",
            "ad_parameters": [CAR_CATEGORY_PARAM],
            "account_parameters": [],
        },
        {
            "ad_id": 12,
            "subject": "Audi Q7 рестайлинг",
            "price_byn": 3600000,
            "ad_link": "https://www.kufar.by/item/12",
            "list_time": "2026-04-01T10:20:00",
            "region_id": 6,
            "category": "2010",
            "body": "Q7",
            "ad_parameters": [CAR_CATEGORY_PARAM],
            "account_parameters": [],
        },
        {
            "ad_id": 20,
            "subject": "Форсунка Audi Q7",
            "price_byn": 10000,
            "ad_link": "https://www.kufar.by/item/20",
            "list_time": "2026-04-01T10:30:00",
            "region_id": 6,
            "category": "2040",
            "body": "part",
            "ad_parameters": [PART_CATEGORY_PARAM],
            "account_parameters": [],
        },
        {
            "ad_id": 21,
            "subject": "ТНВД Audi Q7",
            "price_byn": 12000,
            "ad_link": "https://www.kufar.by/item/21",
            "list_time": "2026-04-01T10:40:00",
            "region_id": 6,
            "category": "2040",
            "body": "part",
            "ad_parameters": [PART_CATEGORY_PARAM],
            "account_parameters": [],
        },
        {
            "ad_id": 22,
            "subject": "Регулятор давления Audi Q7",
            "price_byn": 14000,
            "ad_link": "https://www.kufar.by/item/22",
            "list_time": "2026-04-01T10:50:00",
            "region_id": 6,
            "category": "2040",
            "body": "part",
            "ad_parameters": [PART_CATEGORY_PARAM],
            "account_parameters": [],
        },
    ]
    category_ads = [
        *broad_ads[:3],
        {
            "ad_id": 13,
            "subject": "Audi Q7 S-Line",
            "price_byn": 3800000,
            "ad_link": "https://www.kufar.by/item/13",
            "list_time": "2026-04-01T11:00:00",
            "region_id": 6,
            "category": "2010",
            "body": "Q7",
            "ad_parameters": [CAR_CATEGORY_PARAM],
            "account_parameters": [],
        },
        {
            "ad_id": 14,
            "subject": "Audi Q7 3.0 бензин",
            "price_byn": 4000000,
            "ad_link": "https://www.kufar.by/item/14",
            "list_time": "2026-04-01T11:10:00",
            "region_id": 6,
            "category": "2010",
            "body": "Q7",
            "ad_parameters": [CAR_CATEGORY_PARAM],
            "account_parameters": [],
        },
    ]

    class CategoryDriftClient:
        def __init__(self, settings) -> None:
            del settings

        async def search_all_ads(self, **kwargs) -> dict:
            category = kwargs.get("category")
            ads = category_ads if category == 2010 else broad_ads
            return {"total": len(ads), "ads": ads}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(listing_detail, "KufarClient", CategoryDriftClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: CategoryDriftClient(None)
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        broad_response = client.get(
            "/api/v1/listing-detail",
            params={"query": "audi q7", "ad_id": 10, "currency": "BYN"},
        )
        category_response = client.get(
            "/api/v1/listing-detail",
            params={
                "query": "audi q7",
                "ad_id": 10,
                "currency": "BYN",
                "category": 2010,
                "reference_context": "base_query",
            },
        )

    assert broad_response.status_code == 200
    assert category_response.status_code == 200
    assert broad_response.json()["price_vs_median"] == category_response.json()["price_vs_median"]



# ---------------------------------------------------------------------------
# _backfill_lead_thumbnail — unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def backfill_session_factory():
    """In-memory aiosqlite + freshly-created tables. Mirrors the
    pattern used by test_ai_consent_enforcement so the unit tests
    don't depend on the autouse ``create_test_tables`` fixture
    leaking between modules."""
    from api.database import get_engine, get_session_factory
    from api.models import Base

    engine = get_engine("sqlite+aiosqlite:///:memory:")
    factory = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_lead_thumbnail_patches_null_row(
    backfill_session_factory,
) -> None:
    """LeadItem with thumbnail=NULL gets patched when the detail
    endpoint discovers a thumbnail. Targets the bot-callback flow:
    rows added via '📌 В покупки' / '⭐ В Избранное' before the
    scheduler fix landed with NULL — opening detail backfills them."""
    from api.models import LeadItem, User
    from api.routers.listing_detail import _backfill_lead_thumbnail

    async with backfill_session_factory() as session:
        user = User(telegram_user_id=12345, first_name="Test")
        session.add(user)
        await session.flush()
        lead = LeadItem(
            user_id=user.id,
            ad_id=999,
            query="x",
            title="t",
            link="https://www.kufar.by/item/999",
            price_byn=100.0,
            thumbnail=None,
            status="new",
            source="bot_callback",
        )
        session.add(lead)
        await session.commit()

    await _backfill_lead_thumbnail(
        backfill_session_factory,
        telegram_user_id=12345,
        first_name="Test",
        ad_id=999,
        thumbnail="https://rms.kufar.by/v1/gallery/x/y.jpg",
    )

    async with backfill_session_factory() as session:
        from sqlalchemy import select
        refreshed = await session.scalar(select(LeadItem).where(LeadItem.ad_id == 999))
        assert refreshed.thumbnail == "https://rms.kufar.by/v1/gallery/x/y.jpg"


@pytest.mark.asyncio
async def test_backfill_lead_thumbnail_skips_existing(
    backfill_session_factory,
) -> None:
    """An existing thumbnail must NOT be overwritten. The WHERE clause
    has ``thumbnail IS NULL`` precisely so a manual edit or a later
    Kufar response with a different image doesn't clobber what the
    user already saw."""
    from api.models import LeadItem, User
    from api.routers.listing_detail import _backfill_lead_thumbnail

    existing_url = "https://rms.kufar.by/v1/gallery/old/photo.jpg"
    async with backfill_session_factory() as session:
        user = User(telegram_user_id=12346, first_name="Test")
        session.add(user)
        await session.flush()
        session.add(
            LeadItem(
                user_id=user.id,
                ad_id=998,
                query="x",
                title="t",
                link="https://www.kufar.by/item/998",
                price_byn=100.0,
                thumbnail=existing_url,
                status="new",
                source="bot_callback",
            )
        )
        await session.commit()

    await _backfill_lead_thumbnail(
        backfill_session_factory,
        telegram_user_id=12346,
        first_name="Test",
        ad_id=998,
        thumbnail="https://rms.kufar.by/v1/gallery/new/photo.jpg",
    )

    async with backfill_session_factory() as session:
        from sqlalchemy import select
        refreshed = await session.scalar(select(LeadItem).where(LeadItem.ad_id == 998))
        assert refreshed.thumbnail == existing_url


@pytest.mark.asyncio
async def test_backfill_lead_thumbnail_noop_when_thumbnail_arg_is_none(
    backfill_session_factory,
) -> None:
    """Caller passes thumbnail=None (Kufar returned no images) → the
    function returns immediately without opening a session. Important
    so a missing image doesn't NULL out an existing thumbnail via the
    UPDATE."""
    from api.routers.listing_detail import _backfill_lead_thumbnail

    # Should not raise even though the user/lead don't exist — early
    # return before any DB I/O happens.
    await _backfill_lead_thumbnail(
        backfill_session_factory,
        telegram_user_id=999999,
        first_name="Missing",
        ad_id=12345,
        thumbnail=None,
    )
